#!/usr/bin/env python3
"""
inspect_extraction.py — why is this filing 'partial', or 'FLAGGED'?

Reads the stored filings out of the SAME database the app uses (its own session
factory, so .env is honoured) and prints, per filing, everything that can answer
the question:

  * the row status the review screen filters on (PROCESSED / PROCESSED_WITH_ALERTS
    / FLAGGED) AND the reason text behind it, which lives in audit_notes
  * extraction_status, as stored — plus where the value came from
  * how many FIGURES the answer sections actually contain, counted the same way
    the rules engine counts them, because zero figures is itself a flag
  * the two warning lists, which are different things and are often mixed up:
      validation_warnings           arithmetic findings, computed by the SCHEMA
      metadata.extraction_warnings  what the PIPELINE had to guess or coerce

Usage, from anywhere inside the project:

    python inspect_extraction.py                    last 10 filings
    python inspect_extraction.py SUB-A1790087000    everything, for one filing
    python inspect_extraction.py --limit 50         more rows in the list
    python inspect_extraction.py SUB-A179... --dump also write the payload JSON

Reading only. It never writes to the database and never calls the model.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import textwrap
from pathlib import Path

LINE = "=" * 74

# Parts whose FIGURES the rules engine counts. Metadata and Part A are excluded
# there on purpose: reporting_year always has a default, so counting it would
# make an unread document look like a read one. Mirrored here exactly.
FIGURE_SECTIONS = ("part_b", "part_c_liabilities", "part_c_assets",
                   "part_c_memorandum", "part_d_fats")

SECTION_ORDER = ("part_a", "part_b", "part_c_assets", "part_c_liabilities",
                 "part_c_financial", "part_c_memorandum", "part_d_fats", "part_e",
                 "part_f")


# --------------------------------------------------------------------------
# Find the backend root — the folder holding the `app` package. Same approach
# as check_vlm.py: never trust the working directory.
# --------------------------------------------------------------------------
def _looks_like_backend_root(candidate: Path) -> bool:
    app = candidate / "app"
    if not (app / "main.py").exists():
        return False
    return any((app / d).is_dir() for d in ("routers", "services", "db", "schemas"))


def find_root() -> Path | None:
    here = Path(__file__).resolve().parent
    seen: list[Path] = []
    for start in (here, Path.cwd()):
        for candidate in [start, *start.parents]:
            if candidate in seen:
                continue
            seen.append(candidate)
            if _looks_like_backend_root(candidate):
                return candidate
            if (candidate / "backend").is_dir() and _looks_like_backend_root(candidate / "backend"):
                return candidate / "backend"
    return None


ROOT = find_root()
if ROOT is None:
    print("Could not find the backend root — the folder that contains your `app` folder.")
    print("Copy this script next to check_files.py (in the backend root or in app/) and")
    print("run it again.")
    raise SystemExit(2)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# .env first, exactly like main.py does at startup — the DATABASE_URL lives there.
try:
    from dotenv import load_dotenv

    _env = ROOT / ".env"
    if _env.is_file():
        load_dotenv(_env)
except ImportError:
    pass

from sqlalchemy import select                                        # noqa: E402

from app.db.database import AsyncSessionLocal, describe              # noqa: E402
from app.db.models import FilingRecord                                # noqa: E402


# --------------------------------------------------------------------------
# Counting, mirroring the rules engine
# --------------------------------------------------------------------------
def _numerics(node) -> int:
    """Non-zero numbers under a node — the engine's own rule (numerics only)."""
    if isinstance(node, dict):
        return sum(_numerics(v) for v in node.values())
    if isinstance(node, (list, tuple)):
        return sum(_numerics(v) for v in node)
    if isinstance(node, bool) or node is None:
        return 0
    if isinstance(node, (int, float)):
        return 1 if node else 0
    return 0


def figures_read(payload: dict) -> int:
    """How many figures the answer sections hold, counted like the engine.

    The same number decides FLAGGED vs PROCESSED_WITH_ALERTS: the engine flags a
    filing where this is 0 — "every value in Parts B, C and D is zero or absent",
    which is either an unreadable document or a nil return.
    """
    total = 0
    for key in FIGURE_SECTIONS:
        if key in payload:
            section = payload[key]
            if key == "part_b" and isinstance(section, dict):
                # currency_used is a code, not a figure — the engine excludes it.
                section = {k: v for k, v in section.items() if k != "currency_used"}
            total += _numerics(section)
    # Shareholding rows live under part_a, and the engine counts them too.
    total += _numerics((payload.get("part_a") or {}).get("shareholding_structure"))
    return total


def count_populated(node) -> tuple[int, int]:
    """(populated, total) leaf values under a node, for the section table.

    A heuristic, and the report says so: the schema fills defaults for anything
    the model left out, so a section the model never mentioned arrives as nulls.
    A section with 0 populated was almost certainly not read — but a FATS table
    that legitimately reads zero looks the same. Cross-check against the PDF.
    """
    if isinstance(node, dict):
        pairs = [count_populated(v) for v in node.values()]
    elif isinstance(node, list):
        if not node:
            return 0, 0
        pairs = [count_populated(v) for v in node]
    elif node is None:
        return 0, 1
    elif isinstance(node, bool):
        return 1, 1
    elif isinstance(node, (int, float)):
        return (1 if node else 0), 1
    elif isinstance(node, str):
        return (1 if node.strip() else 0), 1
    else:
        return 1, 1
    return sum(p for p, _ in pairs), sum(t for _, t in pairs)


# --------------------------------------------------------------------------
# Interpreting
# --------------------------------------------------------------------------
def extraction_route(payload: dict) -> str:
    """Why extraction_status has the value it has."""
    if not payload:
        return ("there is no extracted_payload on this row — the upload never reached "
                "the extractor, or it crashed before anything was stored. audit_notes "
                "is the whole story here.")
    status = str(payload.get("extraction_status") or "failed")
    warnings = payload.get("validation_warnings") or []
    meta_warnings = (payload.get("metadata") or {}).get("extraction_warnings") or []

    if status == "failed":
        return ("the extraction FAILED. audit_notes holds the exception text; the "
                "pipeline's own explanation is in metadata.extraction_warnings.")
    if status == "partial" and warnings:
        return (f"the schema's arithmetic checks recorded {len(warnings)} finding(s) "
                "and the run was not allowed to report 'ok'. These are about the "
                "NUMBERS in the document, not about reading it.")
    if status == "partial":
        return ("the MODEL reported its own read as incomplete, and no arithmetic "
                "finding was recorded. Nothing is broken by this alone — sections it "
                "could not read were left blank and it said so.")
    if status == "ok" and meta_warnings:
        return ("clean by the checks, but the PIPELINE had to infer or coerce "
                "something while normalising the payload — see the pipeline notes.")
    return "clean: the model reported a complete read and no check objected."


def row_route(row, payload: dict) -> str:
    """Why the ROW has this status. Separate from extraction_status, and the one
    the review screen's badges and filters actually use."""
    status = str(row.status or "").upper()
    notes = str(row.audit_notes or "")
    figures = figures_read(payload) if payload else 0

    if not payload:
        return ("no payload was stored, so the engine had nothing to evaluate; the "
                "row is flagged on the extraction failure alone.")
    if status == "FLAGGED":
        if notes.startswith("EXT rejected"):
            return ("the rules engine flagged it because the AUTOMATED EXTRACTION did "
                    "not complete. Reason text in audit_notes.")
        if figures == 0:
            return (f"the rules engine flagged it and the answer sections hold 0 "
                    f"figures. Its own wording for that: every value in Parts B, C and "
                    f"D is zero or absent — the document could not be read, or it is a "
                    f"nil return, and those must be told apart before approval.")
        return ("the rules engine flagged it against its own checks (arithmetic, an "
                "anomaly, or nothing extractable). The reason text is in audit_notes.")
    if status == "PROCESSED_WITH_ALERTS":
        return ("it parsed and nothing was fatal, but there are advisories — or the "
                "extraction did not report a clean 'ok'. See audit_notes.")
    if status == "PROCESSED":
        return "it passed the engine's checks with nothing advisory."
    return f"row status {status!r} — see audit_notes."


def first_reason(row) -> str:
    """The one line a reader wants in the list: the engine's reason, shortened.

    audit_notes holds ' | '-joined findings, so the first segment is the headline.
    """
    notes = str(row.audit_notes or "").strip()
    if not notes:
        return "(no audit_notes recorded)"
    head = notes.split(" | ")[0].splitlines()[0].strip()
    return head if len(head) <= 150 else head[:147] + "..."


def print_filing(row, dump: bool) -> None:
    payload = row.extracted_payload
    payload = payload if isinstance(payload, dict) else {}
    meta = payload.get("metadata") or {}
    details = ((payload.get("part_a") or {}).get("details") or {})
    warnings = payload.get("validation_warnings") or []
    meta_warnings = meta.get("extraction_warnings") or []
    company = (row.company_name or "").strip() or "(none read)"

    print(LINE)
    print(f"  {row.filing_id}")
    print(LINE)
    print(f"  company            : {company!r}"
          f"   (payload: {details.get('company_name')!r})")
    print(f"  row status         : {row.status}")
    print(f"  extraction_status  : {payload.get('extraction_status')!r}"
          f"   (root)   {meta.get('extraction_status')!r}   (metadata)")
    print(f"  figures read       : {figures_read(payload)}"
          f"   (Parts B, C, D — the engine's own count; 0 triggers FLAGGED)")
    print(f"  questionnaire type : {meta.get('questionnaire_type')!r}"
          f"   survey years: {meta.get('reporting_year')!r} / {meta.get('previous_year')!r}")
    print(f"  created / validated: {row.created_at}   /   {row.validated_at}")
    print(f"  totals             : assets_usd={row.total_assets_usd} "
          f"assets_tzs={row.total_assets_tzs} net_worth_usd={row.net_worth_usd}")

    print()
    print("  WHY THIS ROW STATUS")
    print(textwrap.fill(row_route(row, payload), width=70,
                        initial_indent="    ", subsequent_indent="    "))
    print()
    print("  WHY THIS EXTRACTION STATUS")
    print(textwrap.fill(extraction_route(payload), width=70,
                        initial_indent="    ", subsequent_indent="    "))

    print()
    print(f"  arithmetic findings (payload validation_warnings) : {len(warnings)}")
    for w in warnings[:12]:
        print(textwrap.fill(str(w), width=70, initial_indent="      - ",
                            subsequent_indent="        "))
    if len(warnings) > 12:
        print(f"      ... and {len(warnings) - 12} more")

    # The same message can be stored several times: older schema revisions appended
    # it once per validation pass, and a payload is validated by the extractor, the
    # rules engine and ingestion. Fixed in the current schema, but rows written
    # before that still carry the repeats — so count DISTINCT messages and say so
    # rather than printing the same three lines three times.
    distinct_meta = list(dict.fromkeys(str(w) for w in meta_warnings))
    print(f"  pipeline notes (metadata.extraction_warnings) : {len(distinct_meta)}"
          + (f"   (the payload holds {len(meta_warnings)} entries — the same message "
             f"written once per validation pass)" if len(distinct_meta) != len(meta_warnings) else ""))
    for w in distinct_meta[:12]:
        print(textwrap.fill(str(w), width=70, initial_indent="      - ",
                            subsequent_indent="        "))

    if payload:
        print()
        print("  SECTIONS READ  (populated / total — 0 means the model left it out)")
        for key in SECTION_ORDER:
            if key in payload:
                filled, total = count_populated(payload[key])
                bar = "#" * round(20 * filled / total) if total else ""
                print(f"    {key:22} {filled:3}/{total:<3} {bar}")

    if row.audit_notes:
        print()
        print("  audit_notes  (verbatim — this is what the review screen shows)")
        for line in str(row.audit_notes).splitlines():
            print(f"      {line}")

    if dump:
        target = Path.cwd() / f"extraction_{row.filing_id}.json"
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                          encoding="utf-8")
        print()
        print(f"  full payload written to: {target}")


async def main() -> int:
    argv = list(sys.argv[1:])
    dump = "--dump" in argv
    limit = 10
    if "--limit" in argv:
        at = argv.index("--limit")
        try:
            limit = int(argv[at + 1])
        except (IndexError, ValueError):
            print("  --limit needs a number; using 10.")
            limit = 10
        # Drop the flag AND its value, or the number is mistaken for a filing id.
        del argv[at:at + 2]
    wanted = next((a for a in argv if not a.startswith("--")), None)

    print()
    print(LINE)
    print("  inspect_extraction.py — reading the pipeline's own records")
    print(LINE)
    print(f"  backend root : {ROOT}")
    print(f"  database     : {describe()}")

    async with AsyncSessionLocal() as session:
        if wanted:
            rows = (await session.execute(
                select(FilingRecord).where(FilingRecord.filing_id == wanted)
            )).scalars().all()
            if not rows:
                # A partial id is the common typo — show what exists instead.
                rows = (await session.execute(
                    select(FilingRecord).order_by(FilingRecord.created_at.desc()).limit(limit)
                )).scalars().all()
                print()
                print(f"  No filing with id {wanted!r}. The ids that DO exist:")
                for row in rows:
                    print(f"    {row.filing_id}   {str(row.status):24} "
                          f"{(row.company_name or '(none read)')}")
                return 1
        else:
            rows = (await session.execute(
                select(FilingRecord).order_by(FilingRecord.created_at.desc()).limit(limit)
            )).scalars().all()

        if not rows:
            print()
            print("  The filings table is empty. Nothing has been uploaded to this")
            print("  database, or DATABASE_URL points at a different one — compare the")
            print("  line above with the [DB] banner uvicorn prints at startup.")
            return 0

        if not wanted:
            print()
            print(LINE)
            print(f"  LAST {len(rows)} FILING(S) — pass an id for the full account")
            print(LINE)
            print(f"  {'filing_id':24} {'row status':22} {'extract':8} {'figs':5} "
                  f"{'arithm':7} company")
            for row in rows:
                payload = row.extracted_payload if isinstance(row.extracted_payload, dict) else {}
                arithmetic = len(payload.get("validation_warnings") or [])
                company = (row.company_name or "").strip() or "(none read)"
                print(f"  {row.filing_id:24} {str(row.status):22} "
                      f"{str(payload.get('extraction_status')):8} "
                      f"{figures_read(payload):<5} {arithmetic:<7} {company[:30]}")
                print(textwrap.fill(f"reason: {first_reason(row)}", width=96,
                                    initial_indent="      ", subsequent_indent="      "))
            print()
            print("  extract = the model's own verdict on its read.")
            print("  figs    = figures found in Parts B, C, D; 0 triggers FLAGGED.")
            print("  arithm  = arithmetic findings the SCHEMA computed (validation_warnings).")
            print("  reason  = the rules engine's note (audit_notes) — the row-status cause.")
            print()
            print("  Next:  python inspect_extraction.py <filing_id>   (use a real id,")
            print("         e.g. python inspect_extraction.py " + rows[0].filing_id + ")")
            return 0

        print_filing(rows[0], dump)
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:                                      # noqa: BLE001
        name = type(exc).__name__
        print()
        print(f"  Could not read the database: {name}: {exc}")
        print()
        if "does not exist" in str(exc) or "relation" in str(exc) or "no such table" in str(exc):
            print("  The tables are not there. Run the initialiser once:")
            print("      python -m app.db.init_db")
        else:
            print("  Check DATABASE_URL in the backend's .env against the [DB] banner")
            print("  uvicorn prints at startup — they must name the same database.")
        raise SystemExit(1)
