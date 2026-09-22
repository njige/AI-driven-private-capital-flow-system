import React, { useState, useEffect, useRef } from 'react';
import {
  BuildingOffice2Icon,
  CheckCircleIcon,
  XCircleIcon,
  XMarkIcon,
  ExclamationTriangleIcon,
  UserCircleIcon,
  MapPinIcon,
  GlobeAmericasIcon,
  BriefcaseIcon,
  BanknotesIcon,
  TableCellsIcon,
  UsersIcon,
  ClipboardDocumentCheckIcon,
  PlusIcon,
  TrashIcon
} from '@heroicons/react/24/outline';

// ══════════════════════════════════════════════════════════════════════════
//  ⚙️  DOCUMENT ENDPOINT
//      'api'    ->  {base}/api/v1/ingest/document-file/{filingId}   (default)
//      'static' ->  {base}/files/{filename}
//  Must stay identical in Overview.tsx and AuditReview.tsx.
// ══════════════════════════════════════════════════════════════════════════
const DOCUMENT_ENDPOINT: 'api' | 'static' = 'api';

interface Submission {
  id?: string;
  filing_id?: string;
  filingId?: string;
  submission_id?: string;
  submissionId?: string;
  submission_ref?: string;
  file_url?: string;
  vlm_score?: string;
  investor_entity?: string;
  tin_number?: string;
  company_name?: string;
  extracted_payload?: any;
  status?: string;
  audit_notes?: string;
  confidence_score?: number;
  created_at?: string;
}

interface AuditReviewProps {
  record: Submission | null;
  isSubmitting: boolean;
  onClose: () => void;
  onSave: (auditForm: {
    companyName: string;
    tinNumber: string;
    bpm6Category: string;
    economistNotes: string;
    auditStatus: string;
    lineItems: any[];
    /* ADDED. The whole edited questionnaire.
     *
     * Without this, every inline edit made on this screen — B1/B2/C1/D cells, the
     * N/A toggles, the survey-period years, the Table C2 rates, the A3
     * acknowledgement block, the A5/A6 tables — was discarded the moment the
     * economist pressed Approve. `lineItems` could not carry them: it holds the
     * A5/A6 rows only, and it has no backend field to land in either.
     *
     * The backend re-validates this through `parse_questionnaire` before storing
     * it, so the arithmetic checks run again against the EDITED figures. */
    payload: Record<string, any>;
  }) => void | Promise<any>;
}

/* ══════════════════════════════════════════════════════════════════════════
 * SHARED HELPERS  (duplicated verbatim in Overview.tsx — keep in sync)
 * ══════════════════════════════════════════════════════════════════════════ */

const ID_KEYS = [
  'filing_id', 'filingId', 'submission_id', 'submissionId',
  'submission_ref', 'record_id', 'recordId', 'uuid', '_id', 'id',
] as const;

const NESTED_KEYS = [
  'extracted_payload', 'payload', 'metadata', 'filing', 'submission', 'record',
] as const;

const looksLikeId = (v: unknown): v is string =>
  typeof v === 'string' && v.trim().length > 0 && v !== 'undefined' && v !== 'null';

/**
 * True when a value is really an extraction error the backend stored as if it
 * were data — e.g. "[AI Unavailable: Model Busy] Processing (tmp.pdf)".
 */
const EXTRACTION_FAILURE_PATTERNS = [
  /\[\s*ai unavailable/i,
  /model\s+busy/i,
  /extraction\s+(?:failed|error|unavailable)/i,
  /^\s*processing\s*\(/i,
];

function looksLikeExtractionFailure(value?: string | null): boolean {
  if (!value || typeof value !== 'string') return false;
  return EXTRACTION_FAILURE_PATTERNS.some((re) => re.test(value));
}

function resolveFilingId(record: any): string | null {
  if (!record) return null;

  for (const key of ID_KEYS) {
    if (looksLikeId((record as any)[key])) return (record as any)[key].trim();
  }

  for (const parent of NESTED_KEYS) {
    const node = (record as any)[parent];
    if (node && typeof node === 'object') {
      for (const key of ID_KEYS) {
        if (looksLikeId(node[key])) return (node as any)[key].trim();
      }
    }
  }

  for (const key of ID_KEYS) {
    const v = (record as any)[key];
    if (typeof v === 'number' && Number.isFinite(v)) return String(v);
  }

  return null;
}

const trimSlash = (s: string) => s.replace(/\/+$/, '');

function resolveApiBase(): string {
  const env = (import.meta as any)?.env?.VITE_API_BASE_URL;
  if (env !== undefined && env !== null) return trimSlash(String(env));
  const host = typeof window !== 'undefined' ? window.location.hostname : 'localhost';
  return `http://${host}:8000`;
}

function staticFilename(record: any, filingId: string): string {
  const raw = typeof record?.file_url === 'string' ? record.file_url.trim() : '';
  if (!raw) return `${filingId}_document.pdf`;
  const filename = raw.split('/').pop() || '';
  if (!filename) return `${filingId}_document.pdf`;
  return filename.startsWith(filingId) ? filename : `${filingId}_${filename}`;
}

function resolveDocumentUrl(
  record: any,
  endpoint: 'api' | 'static' = DOCUMENT_ENDPOINT,
): string | null {
  if (!record) return null;

  if (typeof record.file_url === 'string' && /^https?:\/\//i.test(record.file_url)) {
    return record.file_url;
  }

  const filingId = resolveFilingId(record);
  const base = resolveApiBase();

  if (endpoint === 'api') {
    return filingId
      ? `${base}/api/v1/ingest/document-file/${encodeURIComponent(filingId)}`
      : null;
  }

  if (!filingId) return null;
  return `${base}/files/${encodeURIComponent(staticFilename(record, filingId))}`;
}

/* ══════════════════════════════════════════════════════════════════════════
 * PAYLOAD NORMALISATION
 *
 * bot_questionnaire.py is the contract. Earlier versions of this form used
 * different key names and different nesting, so records already in the database
 * do not match it. Rather than sprinkling `?? legacyKey` through every input,
 * legacy shapes are migrated once, on load, into the schema's shape:
 *
 *   part_a.company_details            -> part_a.details
 *   part_a.affiliates_status          -> part_a.affiliates
 *   shareholder.source_country        -> .source_country_or_multilateral
 *   shareholder.previous_year_percentage -> .previous_year_shareholding_pct
 *   shareholder.reporting_year_percentage -> .reporting_year_shareholding_pct
 *   shareholder.relationship_type     -> .previous_year_relationship
 *                                        .reporting_year_relationship
 *   part_d                            -> part_d_fats
 *
 * Nothing is discarded; the original keys are left untouched so the migration
 * is reversible and auditable.
 * ══════════════════════════════════════════════════════════════════════════ */
function normaliseExtractedPayload(input: any): any {
  if (!input || typeof input !== 'object') return {};
  const p = JSON.parse(JSON.stringify(input));

  if (p.part_a) {
    if (p.part_a.company_details && !p.part_a.details) {
      p.part_a.details = p.part_a.company_details;
    }
    if (p.part_a.affiliates_status && !p.part_a.affiliates) {
      p.part_a.affiliates = p.part_a.affiliates_status;
    }

    const shs = p.part_a.shareholding_structure;
    if (Array.isArray(shs)) {
      p.part_a.shareholding_structure = shs.map((s: any) => ({
        source_country_or_multilateral: s.source_country_or_multilateral ?? s.source_country ?? '',
        shareholder_name: s.shareholder_name ?? '',
        previous_year_shareholding_pct: s.previous_year_shareholding_pct ?? s.previous_year_percentage ?? 0,
        previous_year_relationship: s.previous_year_relationship ?? s.relationship_type ?? 'OTHER',
        reporting_year_shareholding_pct: s.reporting_year_shareholding_pct ?? s.reporting_year_percentage ?? 0,
        reporting_year_relationship: s.reporting_year_relationship ?? s.relationship_type ?? 'OTHER',
      }));
    }
  }

  // Part C is a list named part_c_liabilities in the schema.
  if (!Array.isArray(p.part_c_liabilities) && Array.isArray(p.part_c)) {
    p.part_c_liabilities = p.part_c;
  }

  // Part D lives at part_d_fats in the schema.
  if (p.part_d && !p.part_d_fats) {
    p.part_d_fats = p.part_d;
  }

  return p;
}

/* ══════════════════════════════════════════════════════════════════════════
 * ENUM OPTION LISTS
 * Values below are the EXACT literals from bot_questionnaire.py — the backend
 * validates against them, so they must not be reworded. Only the display
 * labels are shortened.
 * ══════════════════════════════════════════════════════════════════════════ */

const CURRENCY_OPTIONS = [
  { value: 'TZS', label: 'TZS' },
  { value: 'USD', label: 'USD' },
];

const RELATIONSHIP_OPTIONS = [
  { value: 'DI', label: 'DI — Direct investor (≥ 10%)' },
  { value: 'DIE', label: 'DIE — Direct investment entity' },
  { value: 'FE', label: 'FE — Fellow enterprise' },
  { value: 'PI', label: 'PI — Portfolio investment (< 10%)' },
  { value: 'IFS', label: 'IFS — Investment fund shares' },
  { value: 'OTHER', label: 'OTHER' },
  { value: 'RESIDENT', label: 'RESIDENT' },
];

const MATURITY_OPTIONS = [
  { value: 'LT', label: 'LT — Long term (12 months or more)' },
  { value: 'ST', label: 'ST — Short term (less than 12 months)' },
];

const LIABILITY_CATEGORY_OPTIONS = [
  { value: 'Loans (Including Financial Leases, Repos)', label: 'Loans (including financial leases, repos)' },
  { value: 'Debt securities (Including Money Market Instruments, Bonds and notes)', label: 'Debt securities (including MMIs, bonds and notes)' },
  { value: 'Suppliers/Trade Credits & Advances', label: 'Suppliers / trade credits & advances' },
  { value: 'Life & Non-Life Insurance Technical Reserves', label: 'Life & non-life insurance technical reserves' },
  { value: 'Pension Entitlements/Claims', label: 'Pension entitlements / claims' },
  { value: 'Other Accounts Payable', label: 'Other accounts payable' },
];

/* ── Part B, Table B1: equity components × columns A…E ──────────────────── */
// `official: true` marks a column the printed form heads "Official Use Only" —
// the respondent does not supply D1/D2/D3, BoT derives them. BoT staff may edit
// them here, but they must be visibly distinguished from reported figures.
const EQUITY_COLUMNS = [
  { key: 'previous_year_closing_A', letter: 'A', label: 'Closing', year: 'prev', official: false },
  { key: 'purchase_increase_B', letter: 'B', label: 'Increase', year: null, official: false },
  { key: 'sales_decrease_C', letter: 'C', label: 'Decrease', year: null, official: false },
  { key: 'other_changes_price_D1', letter: 'D1', label: 'Other – price', year: null, official: true },
  { key: 'other_changes_exchange_rate_D2', letter: 'D2', label: 'Other – FX', year: null, official: true },
  { key: 'other_changes_volume_D3', letter: 'D3', label: 'Other – volume', year: null, official: true },
  { key: 'reporting_year_closing_E', letter: 'E', label: 'Closing', year: 'rep', official: false },
];

const B1_ROWS = [
  { key: 'paid_up_share_capital', label: 'Paid-up share capital' },
  { key: 'share_premium', label: 'Share premium' },
  { key: 'reserves_capital_statutory_revaluation_other', label: 'Reserves (capital, statutory, revaluation & other)' },
  { key: 'other_equity_debt_swaps_deposits', label: 'Other equity (debt swaps, deposits)' },
  { key: 'accumulated_retained_earnings_loss', label: 'Accumulated retained earnings / loss' },
];

/* ── Part B, Table B2: profits & dividends ──────────────────────────────── */
const B2_ROWS = [
  { key: 'net_profit_or_loss_after_tax_A', letter: 'A', label: 'Net profit or loss after tax (during T)' },
  { key: 'dividends_declared_B', letter: 'B', label: 'Dividends declared (during T)' },
  { key: 'dividends_paid_or_profits_remitted_C', letter: 'C', label: 'Dividends paid / profits remitted' },
  { key: 'retained_earnings_D', letter: 'D', label: 'Retained earnings (D = A − B)' },
];

/* ── Part C: numeric columns, A…G ───────────────────────────────────────── */
// Printed note on columns A and E of Table C1: "including accrued interest not paid".
const LIABILITY_COLUMNS = [
  { key: 'previous_year_closing_A', letter: 'A', label: 'Closing T-1 (incl. accrued int. not paid)' },
  { key: 'amount_received_B', letter: 'B', label: 'Received' },
  { key: 'principal_repayment_C', letter: 'C', label: 'Repaid' },
  { key: 'other_changes_price_D1', letter: 'D1', label: 'Price' },
  { key: 'other_changes_exchange_rate_D2', letter: 'D2', label: 'FX' },
  { key: 'other_changes_volume_D3', letter: 'D3', label: 'Volume' },
  { key: 'reporting_year_closing_E', letter: 'E', label: 'Closing T (incl. accrued int. not paid)' },
  { key: 'interest_paid_G', letter: 'G', label: 'Interest paid' },
];

/* ── Part D: FATS items 1–14 ────────────────────────────────────────────── */
const FATS_ITEMS = [
  { key: 'opening_stock_inventory_1', label: '1. Opening stock / inventory', kind: 'value', group: 'Financial position' },
  { key: 'closing_stock_inventory_2', label: '2. Closing stock / inventory', kind: 'value', group: 'Financial position' },
  { key: 'sales_turnover_3', label: '3. Sales / turnover', kind: 'value', group: 'Financial position' },
  { key: 'tax_on_income_4', label: '4. Tax on income', kind: 'value', group: 'Financial position' },
  { key: 'total_assets_5', label: '5. Total assets', kind: 'value', group: 'Financial position' },
  { key: 'total_liabilities_6', label: '6. Total liabilities', kind: 'value', group: 'Financial position' },
  { key: 'net_worth_7', label: '7. Net worth (item 5 − item 6)', kind: 'value', group: 'Financial position' },
  { key: 'total_number_of_employees_8', label: '8. Total number of employees', kind: 'count', group: 'Employment' },
  { key: 'professionals_count_9', label: '9. Professionals', kind: 'count', group: 'Employment' },
  { key: 'non_professionals_count_10', label: '10. Non-professionals', kind: 'count', group: 'Employment' },
  { key: 'total_employee_compensation_11', label: '11. Total employee compensation', kind: 'value', group: 'Employee compensation' },
  { key: 'compensation_short_term_foreign_12', label: '12. Compensation — short term (foreign)', kind: 'value', group: 'Employee compensation' },
  { key: 'compensation_long_term_foreign_13', label: '13. Compensation — long term (foreign)', kind: 'value', group: 'Employee compensation' },
  { key: 'compensation_local_14', label: '14. Compensation — local', kind: 'value', group: 'Employee compensation' },
];

/** Part D stores one node per item, with T-1 and T under different key names
 *  depending on whether the item is a monetary value or a headcount. */
const fatsKeys = (kind: string) =>
  kind === 'count'
    ? { prev: 'previous_year_count', rep: 'reporting_year_count' }
    : { prev: 'previous_year_value', rep: 'reporting_year_value' };

const blankLiabilityRecord = () => ({
  liability_category: 'Loans (Including Financial Leases, Repos)',
  source_country_or_multilateral: '',
  relationship: 'OTHER',
  original_maturity: 'LT',
  previous_year_closing_A: 0,
  amount_received_B: 0,
  principal_repayment_C: 0,
  other_changes_price_D1: 0,
  other_changes_exchange_rate_D2: 0,
  other_changes_volume_D3: 0,
  reporting_year_closing_E: 0,
  interest_paid_G: 0,
});

/* ══════════════════════════════════════════════════════════════════════════
 * SHARED FIELD COMPONENTS
 * ══════════════════════════════════════════════════════════════════════════ */

const num = (v: any, fallback = 0): number =>
  typeof v === 'number' && Number.isFinite(v) ? v : fallback;

/**
 * Numeric input that keeps its own text buffer.
 * A bare `<input type="number" value={number}>` fights the user: typing "-",
 * "1." or clearing the field produces a value that cannot be parsed, so React
 * rewrites the box mid-keystroke. Statutory returns contain negatives (losses)
 * and decimals, so this matters.
 */
function NumberInput({
  value,
  onChange,
  className = '',
  integer = false,
  placeholder = '0',
  title,
  disabled = false,
}: {
  value: number | undefined;
  onChange: (v: number) => void;
  className?: string;
  integer?: boolean;
  placeholder?: string;
  title?: string;
  disabled?: boolean;
}) {
  const external = value === undefined || value === null || Number.isNaN(value as number)
    ? ''
    : String(value);
  const [text, setText] = useState<string>(external);
  const [focused, setFocused] = useState<boolean>(false);

  // Re-sync when the record changes (or an external edit lands), but never
  // while the user is typing in this box.
  useEffect(() => {
    if (!focused) setText(external);
  }, [external, focused]);

  const handle = (t: string) => {
    setText(t);
    const cleaned = t.replace(/,/g, '').replace(/\s/g, '');
    if (cleaned === '' || cleaned === '-' || cleaned === '.' || cleaned === '-.') {
      onChange(0);
      return;
    }
    const n = Number(cleaned);
    if (Number.isFinite(n)) onChange(integer ? Math.trunc(n) : n);
  };

  return (
    <input
      type="text"
      inputMode={integer ? 'numeric' : 'decimal'}
      value={text}
      title={title}
      placeholder={placeholder}
      disabled={disabled}
      onFocus={() => setFocused(true)}
      onBlur={() => setFocused(false)}
      onChange={(e) => handle(e.target.value)}
      className={`${className} disabled:bg-slate-100 disabled:text-slate-400 disabled:line-through`}
    />
  );
}

function SelectInput({
  value,
  onChange,
  options,
  className = '',
  title,
  disabled = false,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  className?: string;
  title?: string;
  disabled?: boolean;
}) {
  return (
    <select
      value={value}
      title={title}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      className={`bg-white border border-slate-300 ${className} disabled:bg-slate-100 disabled:text-slate-400`}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

/**
 * "N/A" marker.
 *
 * The questionnaire instructs (page 3): "Please do not leave blank spaces even
 * where a question does not apply to you. Please enter 'N/A' in the appropriate
 * box." But every numeric field in the model defaults to 0.0, so "not
 * applicable" and "reported as nil" are otherwise the same value — a distinction
 * that matters when auditing.
 *
 * Ticking this records the dotted path in `not_applicable_fields` and greys out
 * the row's inputs.
 */
function NaToggle({
  checked,
  onChange,
  title,
}: {
  checked: boolean;
  onChange: (on: boolean) => void;
  title?: string;
}) {
  return (
    <label
      title={title || 'Mark as "N/A" — not applicable to this company, rather than reported as zero'}
      className={`flex items-center gap-1 shrink-0 cursor-pointer select-none ${
        checked ? 'text-slate-700' : 'text-slate-400 hover:text-slate-600'
      }`}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="w-3 h-3 rounded border-slate-300 text-slate-600 focus:ring-slate-400"
      />
      <span className="text-[9px] font-bold tracking-wide">N/A</span>
    </label>
  );
}

/**
 * Reconciliation marker. The questionnaire defines arithmetic relationships
 * (E = A + B − C + D, D = A − B, net worth = assets − liabilities). When the
 * stored value disagrees with the arithmetic, that is an audit exception worth
 * seeing — so a marker appears, and only then.
 */
function ReconNote({
  expected,
  actual,
  label,
}: {
  expected: number;
  actual: number;
  label?: string;
}) {
  if (!Number.isFinite(expected) || !Number.isFinite(actual)) return null;
  const diff = Math.round((actual - expected) * 100) / 100;
  if (Math.abs(diff) < 0.01) return null;
  return (
    <span
      title={`${label ? label + ': ' : ''}stored ${actual.toLocaleString()} differs from the computed ${expected.toLocaleString()} by ${diff.toLocaleString()}`}
      className="ml-1 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-amber-100 text-[10px] font-bold text-amber-700 cursor-help"
    >
      Δ
    </span>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
 * DOCUMENT LOADER
 *
 * Tries an authenticated fetch and hands the browser a blob: URL (so the token
 * travels and a JSON/HTML error body is caught instead of rendered). If the
 * browser blocks that fetch (CORS / server unreachable) it falls back to a
 * plain <iframe src>, which is a navigation and needs no CORS.
 * ══════════════════════════════════════════════════════════════════════════ */
interface DocumentState {
  url: string | null;
  loading: boolean;
  error: string | null;
  status: 'idle' | 'loading' | 'ready' | 'no-id' | 'error';
  direct?: boolean;
}

const authHeaders = (): Record<string, string> => {
  const token =
    localStorage.getItem('access_token') ||
    localStorage.getItem('token') ||
    localStorage.getItem('bot_token');
  return token ? { Authorization: `Bearer ${token}` } : {};
};

function useFilingDocument(url: string | null): DocumentState {
  const [state, setState] = useState<DocumentState>({
    url: null, loading: false, error: null, status: 'idle',
  });
  const objectUrlRef = useRef<string | null>(null);

  useEffect(() => {
    if (objectUrlRef.current) {
      URL.revokeObjectURL(objectUrlRef.current);
      objectUrlRef.current = null;
    }

    if (!url) {
      setState({ url: null, loading: false, error: null, status: 'no-id' });
      return;
    }

    const controller = new AbortController();
    let cancelled = false;
    setState({ url: null, loading: true, error: null, status: 'loading' });

    (async () => {
      try {
        const res = await fetch(url, {
          signal: controller.signal,
          headers: authHeaders(),
          credentials: 'include',
        });

        if (!res.ok) {
          let detail = `HTTP ${res.status} ${res.statusText}`;
          const ct = res.headers.get('content-type') || '';
          if (ct.includes('application/json')) {
            const body = await res.json().catch(() => null);
            detail = body?.detail || body?.message || detail;
          }
          throw new Error(detail);
        }

        const ct = res.headers.get('content-type') || '';
        const cd = res.headers.get('content-disposition') || '';

        if (ct.includes('text/html')) {
          throw new Error(
            'The server returned HTML instead of a PDF — the endpoint path is ' +
            'probably wrong and the request fell through to the frontend.',
          );
        }
        if (ct.includes('application/json')) {
          const body = await res.json().catch(() => null);
          throw new Error(body?.detail || 'The server returned JSON instead of a PDF.');
        }
        if (cd.toLowerCase().includes('attachment')) {
          console.warn(
            '[AuditReview] Content-Disposition: attachment — the in-app viewer may ' +
            'stay blank; use the Download link instead.',
          );
        }

        const blob = await res.blob();
        if (cancelled) return;

        const typed = blob.type.includes('pdf')
          ? blob
          : new Blob([blob], { type: 'application/pdf' });

        const objectUrl = URL.createObjectURL(typed);
        objectUrlRef.current = objectUrl;
        setState({ url: objectUrl, loading: false, error: null, status: 'ready' });
      } catch (err: any) {
        if (err?.name === 'AbortError' || cancelled) return;

        const isNetwork =
          err instanceof TypeError ||
          /failed to fetch|networkerror|load failed|network request failed/i.test(
            err?.message || '',
          );

        if (isNetwork) {
          setState({ url, loading: false, error: null, status: 'ready', direct: true });
          return;
        }

        setState({
          url: null, loading: false, status: 'error',
          error: err?.message || 'Failed to load document',
        });
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = null;
      }
    };
  }, [url]);

  return state;
}

/* ══════════════════════════════════════════════════════════════════════════ */

export default function AuditReview({ record, isSubmitting, onClose, onSave }: AuditReviewProps) {
  const [filingRecord, setFilingRecord] = useState<Submission | null>(record);
  const [activeTab, setActiveTab] = useState<'partA' | 'partB' | 'partC' | 'partD'>('partA');
  const [auditRemarks, setAuditRemarks] = useState<string>('');
  const [bpm6Category, setBpm6Category] = useState<string>('');
  const [forceDirect, setForceDirect] = useState<boolean>(false);

  // Close on Escape — the workbench fills the viewport, so there is no
  // backdrop left to click.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !isSubmitting) onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose, isSubmitting]);

  // Keep local state synced when record prop changes
  useEffect(() => {
    if (record) {
      let parsedPayload = record.extracted_payload;
      if (typeof parsedPayload === 'string') {
        try {
          parsedPayload = JSON.parse(parsedPayload);
        } catch (e) {
          console.error('Failed to parse stringified extracted_payload:', e);
          parsedPayload = {};
        }
      }

      // Migrate legacy key names into the shape bot_questionnaire.py declares.
      const normalised = normaliseExtractedPayload(parsedPayload);

      setFilingRecord({ ...record, extracted_payload: normalised });
      setAuditRemarks(record.audit_notes || '');

      const initialBpm6 =
        normalised.bpm6_category || normalised.part_a?.bpm6_category || '';
      setBpm6Category(initialBpm6);

      // Opening a second filing used to keep the previous tab, so the economist
      // landed on Part D of a different company.
      setActiveTab('partA');
      setForceDirect(false);
    }
  }, [record]);

  /* ── Document viewer state ──────────────────────────────────────────────
   * These hooks MUST stay ABOVE the early return below. A hook called after
   * `if (!filingRecord) return null;` breaks the rules of hooks and crashes the
   * first time `record` is null.
   * ─────────────────────────────────────────────────────────────────────── */
  const filingId = resolveFilingId(filingRecord);
  const docUrl = resolveDocumentUrl(filingRecord);
  const doc = useFilingDocument(docUrl);

  if (!filingRecord) return null;

  const handlePayloadFieldChange = (path: string[], value: any) => {
    setFilingRecord((prev) => {
      if (!prev) return prev;
      const updatedPayload = JSON.parse(JSON.stringify(prev.extracted_payload || {}));

      let current = updatedPayload;
      for (let i = 0; i < path.length - 1; i++) {
        if (!current[path[i]]) current[path[i]] = {};
        current = current[path[i]];
      }
      current[path[path.length - 1]] = value;

      return { ...prev, extracted_payload: updatedPayload };
    });
  };

  const payload = filingRecord.extracted_payload || {};

  /* ── Survey period metadata: drives every T-1 / T column header ───────── */
  const meta = payload.metadata || {};
  const prevYear = num(meta.previous_year, 2023);
  const repYear = num(meta.reporting_year, 2024);

  // The instrument declares its own cycle in the questionnaire type
  // ("PCF/C17/2027" = survey year 2027, so T = 2026). The type and the table
  // years are two statements of the same fact, read off two different places on
  // the form — if they disagree, every T/T-1 column header is suspect. The UI
  // does not silently rewrite either one; it shows the disagreement, matching
  // how the backend records it.
  const qtypeYearMatch = /(19|20)\d{2}/.exec(String(meta.questionnaire_type || ''));
  const typeSurveyYear = qtypeYearMatch ? Number(qtypeYearMatch[0]) : null;
  const expectedRepYear = typeSurveyYear !== null ? typeSurveyYear - 1 : null;
  const periodMismatch = expectedRepYear !== null && expectedRepYear !== repYear;

  /* ── Part A ───────────────────────────────────────────────────────────── */
  const partA = payload.part_a || payload.Part_A || {};
  const partA1 = partA.details || partA.company_details || {};
  const partA2 = partA.affiliates || partA.affiliates_status || {};
  const partA3 = partA.acknowledgement || {};
  const rates = meta.exchange_rates || {};
  const classifications = Array.isArray(partA.industrial_classifications)
    ? partA.industrial_classifications
    : [];
  const shareholders = Array.isArray(partA.shareholding_structure)
    ? partA.shareholding_structure
    : [];

  /* ── Part B ───────────────────────────────────────────────────────────── */
  const partB = payload.part_b || payload.Part_B || {};
  const partB1 = partB.table_b1 || {};
  const partB2 = partB.table_b2 || {};
  const ccy = partB.currency_used === 'USD' ? 'USD' : 'TZS';

  /* ── Part C: a LIST named part_c_liabilities ──────────────────────────── */
  const liabilities: any[] = Array.isArray(payload.part_c_liabilities)
    ? payload.part_c_liabilities
    : [];

  /* ── Part D ───────────────────────────────────────────────────────────── */
  const partD = payload.part_d_fats || {};

  /* ── "N/A" bookkeeping ────────────────────────────────────────────────────
   * Paths are stored in payload.not_applicable_fields using the same dotted
   * notation the backend documents, e.g.
   *   part_b.table_b1.share_premium
   *   part_d_fats.total_assets_5
   *   part_c_liabilities[2]
   * ─────────────────────────────────────────────────────────────────────── */
  const naList: string[] = Array.isArray(payload.not_applicable_fields)
    ? payload.not_applicable_fields
    : [];
  const isNa = (path: string) => naList.includes(path);
  const setNa = (path: string, on: boolean) => {
    const next = on
      ? Array.from(new Set([...naList, path]))
      : naList.filter((p) => p !== path);
    handlePayloadFieldChange(['not_applicable_fields'], next);
  };

  const updateLiability = (idx: number, key: string, value: any) => {
    const next = liabilities.map((r, i) => (i === idx ? { ...r, [key]: value } : r));
    handlePayloadFieldChange(['part_c_liabilities'], next);
  };

  const addLiability = () => {
    handlePayloadFieldChange(['part_c_liabilities'], [...liabilities, blankLiabilityRecord()]);
  };

  const removeLiability = (idx: number) => {
    handlePayloadFieldChange(
      ['part_c_liabilities'],
      liabilities.filter((_, i) => i !== idx),
    );
  };

  const rawName = filingRecord.company_name || partA1.company_name || '';
  // Prefer the pipeline's own status. The text sniff remains as a fallback for
  // records created before `extraction_status` existed on the model.
  const extractionFailed =
    payload.extraction_status === 'failed' || looksLikeExtractionFailure(rawName);

  // Everything the pipeline or the validator flagged. Extracted records carry
  // metadata.extraction_warnings; the model's arithmetic checks write
  // validation_warnings. Both are part of the audit trail, so both are shown.
  const dataWarnings: string[] = [
    ...(Array.isArray(meta.extraction_warnings) ? meta.extraction_warnings : []),
    ...(Array.isArray(payload.validation_warnings) ? payload.validation_warnings : []),
  ];

  const handleTriggerSave = (status: string) => {
    onSave({
      companyName: filingRecord.company_name || partA1.company_name || '',
      tinNumber: filingRecord.tin_number || partA1.tin_number || '',
      bpm6Category: bpm6Category,
      economistNotes: auditRemarks,
      auditStatus: status,
      lineItems: shareholders.length > 0 ? shareholders : classifications,
      // The edited payload as it currently stands. `payload` is
      // filingRecord.extracted_payload, which every editable field on this screen
      // writes into via handlePayloadFieldChange.
      payload: payload,
    });
  };

  /* Exactly one iframe renders, from one resolved source.
   *  - normal path: the blob: URL from the authenticated fetch
   *  - direct path: the raw URL, used when the fetch was blocked (CORS) or the
   *    user presses "Try direct embed" from the error state */
  const directMode = forceDirect || doc.direct === true;
  const viewUrl = directMode ? docUrl : doc.status === 'ready' ? doc.url : null;

  return (
    /* Full-bleed workbench: no backdrop gap, no rounding, no max-width.
     * Was `flex items-center justify-center bg-black/50 backdrop-blur-sm p-4`
     * with an inner `max-w-7xl h-[90vh] rounded-2xl` — that is what made the
     * panel float with the dashboard visible around it.
     * To go back to a floating window, restore those two class strings. */
    <div className="fixed inset-0 z-50 flex bg-slate-100">
      <div className="bg-slate-100 w-full h-full flex flex-col overflow-hidden">

        {/* Header Bar */}
        <header className="bg-white border-b border-slate-200 px-6 py-3 flex items-center justify-between gap-4 shrink-0">
          <div className="flex items-center space-x-4 min-w-0 flex-1">
            <button
              onClick={onClose}
              title="Close (Esc)"
              className="p-1.5 hover:bg-slate-100 rounded-lg text-slate-600 transition shrink-0"
            >
              <XMarkIcon className="w-5 h-5" />
            </button>
            <div className="min-w-0">
              <div className="flex items-center space-x-2 min-w-0">
                {/* min-w-0 + truncate only stop a very long name from pushing
                    the action buttons off-screen; the text shown is unchanged. */}
                <h1 className="text-lg font-bold text-slate-900 truncate">
                  {filingRecord.company_name || partA1.company_name || 'Unnamed Filing'}
                </h1>
                {filingId && (
                  <span className="text-xs font-mono bg-slate-100 border border-slate-300 px-2 py-0.5 rounded text-slate-600 shrink-0">
                    {filingId}
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-500 truncate">
                TIN: {filingRecord.tin_number || partA1.tin_number || 'N/A'}
              </p>
            </div>
          </div>

          {/* Action Controls */}
          <div className="flex items-center space-x-3 shrink-0">
            <button
              disabled={isSubmitting}
              onClick={() => handleTriggerSave('REJECTED')}
              className="flex items-center px-3 py-1.5 bg-red-50 border border-red-200 text-red-700 hover:bg-red-100 rounded-lg text-xs font-semibold transition disabled:opacity-50"
            >
              <XCircleIcon className="w-4 h-4 mr-1.5" /> Reject
            </button>
            <button
              disabled={isSubmitting}
              onClick={() => handleTriggerSave('FLAGGED')}
              className="flex items-center px-3 py-1.5 bg-amber-50 border border-amber-200 text-amber-700 hover:bg-amber-100 rounded-lg text-xs font-semibold transition disabled:opacity-50"
            >
              <ExclamationTriangleIcon className="w-4 h-4 mr-1.5" /> Flag for Review
            </button>
            <button
              disabled={isSubmitting}
              onClick={() => handleTriggerSave('APPROVED')}
              className="flex items-center px-4 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg text-xs font-semibold shadow-sm transition disabled:opacity-50"
            >
              <CheckCircleIcon className="w-4 h-4 mr-1.5" /> Approve Audit
            </button>
          </div>
        </header>

        {/* Main Split Layout */}
        <div className="flex-1 flex overflow-hidden min-h-0">
          {/* ─────────── Left Side: Raw Document Viewer ───────────
              min-w-0 stops the iframe from forcing the split to overflow. */}
          <div className="w-1/2 min-w-0 border-r border-slate-200 bg-slate-900 flex flex-col">
            <div className="bg-slate-800 px-4 py-2 flex items-center justify-between border-b border-slate-700 shrink-0">
              <span className="text-xs font-semibold text-slate-300 truncate">
                RAW SUBMITTED DOCUMENT
                {filingId && (
                  <span className="ml-2 font-mono text-[10px] text-slate-500">{filingId}</span>
                )}
              </span>
              {doc.status === 'ready' && doc.url && (
                <a
                  href={doc.url}
                  download={`${filingId || 'filing'}.pdf`}
                  className="text-xs text-emerald-400 hover:underline shrink-0"
                >
                  Download ↓
                </a>
              )}
            </div>

            {!viewUrl && doc.status === 'no-id' && (
              <div className="flex-1 flex items-center justify-center p-8 text-center">
                <div className="max-w-sm">
                  <p className="text-sm font-semibold text-amber-400">
                    No filing reference available
                  </p>
                  <p className="mt-2 text-xs text-slate-400 leading-relaxed">
                    This record carries no <code className="text-slate-300">filing_id</code>,{' '}
                    <code className="text-slate-300">submission_id</code> or{' '}
                    <code className="text-slate-300">id</code> field, so the document cannot be
                    requested. The document panel used to send the literal text
                    &ldquo;undefined&rdquo; in that case &mdash; it no longer does.
                  </p>
                </div>
              </div>
            )}

            {!viewUrl && doc.status === 'loading' && (
              <div className="flex-1 flex items-center justify-center">
                <p className="text-xs text-slate-400 animate-pulse">Loading document…</p>
              </div>
            )}

            {!viewUrl && doc.status === 'error' && (
              <div className="flex-1 flex items-center justify-center p-8 text-center">
                <div className="max-w-sm">
                  <p className="text-sm font-semibold text-red-400">
                    Document could not be loaded
                  </p>
                  <p className="mt-2 text-xs text-slate-400 break-words leading-relaxed">
                    {doc.error}
                  </p>
                  {docUrl && (
                    <p className="mt-3 text-[10px] text-slate-500 font-mono break-all">
                      {docUrl}
                    </p>
                  )}
                  {docUrl && (
                    <div className="mt-4 flex items-center justify-center gap-2">
                      <button
                        onClick={() => setForceDirect(true)}
                        className="px-3 py-1.5 bg-slate-700 text-slate-100 rounded-lg text-[11px] font-semibold hover:bg-slate-600 transition"
                      >
                        Try direct embed
                      </button>
                      <a
                        href={docUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="px-3 py-1.5 bg-slate-700 text-slate-100 rounded-lg text-[11px] font-semibold hover:bg-slate-600 transition"
                      >
                        Open in new tab ↗
                      </a>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* The document itself — exactly one iframe */}
            {viewUrl && (
              <iframe
                src={viewUrl}
                className="w-full flex-1 border-0 bg-white"
                title="Filing PDF Viewer"
              />
            )}

            {viewUrl && directMode && (
              <div className="bg-amber-900/40 border-t border-amber-700/50 px-4 py-1.5 shrink-0">
                <p className="text-[10px] text-amber-200">
                  Embedded directly — the in-browser fetch was blocked (CORS), so this view
                  cannot attach your auth token. If you see a login page or an error here,
                  enable CORS or a Vite proxy for /api and reload.
                </p>
              </div>
            )}
          </div>

          {/* ─────────── Right Side: Tabbed Extraction Form ─────────── */}
          <div className="w-1/2 min-w-0 flex flex-col bg-white overflow-y-auto">
            {/* Section Tabs */}
            <div className="flex border-b border-slate-200 bg-slate-50 px-4 sticky top-0 z-20">
              {[
                { id: 'partA', label: 'Part A: Details' },
                { id: 'partB', label: 'Part B: Equity' },
                { id: 'partC', label: 'Part C: Non-Equity' },
                { id: 'partD', label: 'Part D: FATS' }
              ].map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id as any)}
                  className={`px-4 py-3 text-xs font-bold border-b-2 transition ${
                    activeTab === tab.id
                      ? 'border-emerald-600 text-emerald-700 bg-white'
                      : 'border-transparent text-slate-500 hover:text-slate-700'
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            {/* Survey period — every T-1 / T column header below depends on it.
                The questionnaire type and the table years are both read off the
                document; when they disagree, the reviewer needs to see it rather
                than have one silently corrected. */}
            <div className="flex items-center gap-3 flex-wrap px-4 py-1.5 bg-slate-50 border-b border-slate-200 text-[11px] text-slate-600 sticky top-[41px] z-10 shrink-0">
              <span className="font-semibold uppercase tracking-wider text-slate-500">
                Survey period
              </span>
              <input
                type="text"
                value={meta.questionnaire_type || ''}
                onChange={(e) =>
                  handlePayloadFieldChange(['metadata', 'questionnaire_type'], e.target.value)
                }
                placeholder="not captured"
                title="metadata.questionnaire_type — printed at the top of page 1, e.g. PCF/C17/2025"
                className="w-32 px-1.5 py-0.5 bg-white border border-slate-300 rounded text-[11px] font-mono text-slate-700 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
              />
              <span className="flex items-center gap-1">
                T-1
                <NumberInput
                  integer
                  value={prevYear}
                  onChange={(v) => handlePayloadFieldChange(['metadata', 'previous_year'], v)}
                  className="w-16 px-1.5 py-0.5 bg-white border border-slate-300 rounded text-[11px] text-right font-mono"
                />
              </span>
              <span className="flex items-center gap-1">
                T
                <NumberInput
                  integer
                  value={repYear}
                  onChange={(v) => handlePayloadFieldChange(['metadata', 'reporting_year'], v)}
                  className="w-16 px-1.5 py-0.5 bg-white border border-slate-300 rounded text-[11px] text-right font-mono"
                />
              </span>
              {periodMismatch && (
                <span
                  title={`The questionnaire type implies T = ${expectedRepYear}, but the table years say T = ${repYear}. Every T / T-1 column header below is labelled from the table years. Confirm which is correct on the submitted document.`}
                  className="flex items-center gap-1 px-2 py-0.5 bg-amber-50 border border-amber-300 rounded text-[10px] font-semibold text-amber-800 cursor-help"
                >
                  ⚠ period mismatch — type implies {expectedRepYear}
                </span>
              )}
            </div>

            <div className="p-6 space-y-6 flex-1">
              {/* Extraction-failure banner. Approving a filing whose fields are
                  empty placeholders is a control failure, so say so up front. */}
              {extractionFailed && (
                <div className="bg-red-50 border border-red-200 border-l-4 border-l-red-500 rounded-lg p-3">
                  <p className="text-xs font-bold text-red-800 uppercase tracking-wider">
                    Automated extraction did not complete
                  </p>
                  <p className="text-xs text-red-700 mt-1 leading-relaxed">
                    The document below is the original submission and is authoritative.
                    Every extracted field on this panel is empty or a placeholder &mdash;
                    nothing here has been verified against the source. Re-key the required
                    fields from the PDF, or reject the filing and ask the submitter to
                    resubmit, before approving.
                  </p>
                  <p className="text-[11px] text-red-700 mt-2 font-mono break-all">
                    Raw stored company name: {rawName}
                  </p>
                </div>
              )}

              {/* Data-quality panel. The validator records arithmetic mismatches
                  and coercion decisions instead of rejecting the return, so this
                  is where an economist sees them. Collapsed unless there is
                  something to read. */}
              {dataWarnings.length > 0 && (
                <details className="bg-amber-50 border border-amber-200 rounded-lg p-3" open={extractionFailed}>
                  <summary className="cursor-pointer text-xs font-bold text-amber-800 uppercase tracking-wider select-none">
                    Data quality — {dataWarnings.length} item
                    {dataWarnings.length === 1 ? '' : 's'} to review
                  </summary>
                  <ul className="mt-2 space-y-1 list-disc pl-4">
                    {dataWarnings.map((w, i) => (
                      <li key={i} className="text-[11px] text-amber-900 leading-relaxed">
                        {w}
                      </li>
                    ))}
                  </ul>
                  <p className="mt-2 text-[10px] text-amber-700 italic">
                    Recorded by the pipeline and the arithmetic validator. Nothing is
                    auto-corrected — a figure that disagrees with the form's own
                    arithmetic is a finding, not a parse error. Edits made here are
                    re-validated when the filing is saved and reopened.
                  </p>
                </details>
              )}

              {/* Global Audit Remarks Section */}
              <div className="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-3">
                {/* BPM6 classification. The list table has a "BPM6 Category"
                    column, but there was no input for it anywhere in this modal —
                    the value was read into state and posted back unchanged, so an
                    economist could never actually assign one. BPM6 is a BoT
                    review decision, not respondent data, so it lives here with
                    the notes rather than on an extracted-fields tab. */}
                <div>
                  <label className="block text-xs font-bold uppercase tracking-wider text-slate-700 mb-1.5">
                    BPM6 Category
                    <span className="ml-2 font-normal normal-case tracking-normal text-[10px] text-slate-500">
                      assigned during review — not extracted from the document
                    </span>
                  </label>
                  <input
                    type="text"
                    value={bpm6Category}
                    onChange={(e) => {
                      // Update local state (what onSave posts) and write through
                      // to the payload so the value survives a round-trip.
                      setBpm6Category(e.target.value);
                      handlePayloadFieldChange(['bpm6_category'], e.target.value);
                    }}
                    placeholder="e.g. Direct investment — equity"
                    className="w-full px-3 py-1.5 bg-white border border-slate-300 rounded-lg text-xs text-slate-800 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                  />
                </div>

                <div className="space-y-2">
                  <label className="block text-xs font-bold uppercase tracking-wider text-slate-700">
                    Economist / Auditor Review Notes
                  </label>
                  <textarea
                    rows={2}
                    value={auditRemarks}
                    onChange={(e) => setAuditRemarks(e.target.value)}
                    placeholder="Enter remarks, flags, or validation notes..."
                    className="w-full px-3 py-2 bg-white border border-slate-300 rounded-lg text-xs text-slate-800 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                  />
                </div>
              </div>

              {/* ══════════════════════ PART A TAB ══════════════════════ */}
              {activeTab === 'partA' && (
                <div className="space-y-6">
                  {/* 1. Company Information & Addresses */}
                  <div className="space-y-3">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-emerald-700 flex items-center">
                      <BuildingOffice2Icon className="w-4 h-4 mr-1.5" /> 1. Company Identity & Registration
                    </h3>
                    <div className="grid grid-cols-2 gap-3 bg-slate-50 p-4 rounded-xl border border-slate-200">
                      <div>
                        <label className="block text-[11px] font-semibold text-slate-600 mb-1">Company Name</label>
                        <input
                          type="text"
                          value={partA1.company_name || filingRecord.company_name || ''}
                          onChange={(e) => handlePayloadFieldChange(['part_a', 'details', 'company_name'], e.target.value)}
                          className="w-full px-3 py-1.5 bg-white border border-slate-300 rounded-lg text-xs font-medium text-slate-800 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                        />
                      </div>
                      <div>
                        <label className="block text-[11px] font-semibold text-slate-600 mb-1">TIN Number</label>
                        <input
                          type="text"
                          value={partA1.tin_number || filingRecord.tin_number || ''}
                          onChange={(e) => handlePayloadFieldChange(['part_a', 'details', 'tin_number'], e.target.value)}
                          className="w-full px-3 py-1.5 bg-white border border-slate-300 rounded-lg text-xs font-mono text-slate-800 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                        />
                      </div>
                      <div>
                        <label className="block text-[11px] font-semibold text-slate-600 mb-1">Date Established</label>
                        <input
                          type="text"
                          value={partA1.date_established || ''}
                          onChange={(e) => handlePayloadFieldChange(['part_a', 'details', 'date_established'], e.target.value)}
                          className="w-full px-3 py-1.5 bg-white border border-slate-300 rounded-lg text-xs font-mono text-slate-800 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                        />
                      </div>
                      <div>
                        <label className="block text-[11px] font-semibold text-slate-600 mb-1">Commenced Ops</label>
                        <input
                          type="text"
                          value={partA1.date_commenced_ops || ''}
                          onChange={(e) => handlePayloadFieldChange(['part_a', 'details', 'date_commenced_ops'], e.target.value)}
                          className="w-full px-3 py-1.5 bg-white border border-slate-300 rounded-lg text-xs font-mono text-slate-800 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                        />
                      </div>

                      {/* Fields present in A1 on the printed form and in the model,
                          but previously absent from this panel. */}
                      <div>
                        <label className="block text-[11px] font-semibold text-slate-600 mb-1">Previous Name (if any)</label>
                        <input
                          type="text"
                          value={partA1.previous_name || ''}
                          onChange={(e) => handlePayloadFieldChange(['part_a', 'details', 'previous_name'], e.target.value)}
                          className="w-full px-3 py-1.5 bg-white border border-slate-300 rounded-lg text-xs text-slate-800 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                        />
                      </div>
                      <div>
                        <label className="block text-[11px] font-semibold text-slate-600 mb-1">Date Completed (dd/mm/yyyy)</label>
                        <input
                          type="text"
                          value={partA1.date_completed || ''}
                          onChange={(e) => handlePayloadFieldChange(['part_a', 'details', 'date_completed'], e.target.value)}
                          className="w-full px-3 py-1.5 bg-white border border-slate-300 rounded-lg text-xs font-mono text-slate-800 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                        />
                      </div>
                      <div>
                        <label className="block text-[11px] font-semibold text-slate-600 mb-1">Telephone</label>
                        <input
                          type="text"
                          value={partA1.telephone || ''}
                          onChange={(e) => handlePayloadFieldChange(['part_a', 'details', 'telephone'], e.target.value)}
                          className="w-full px-3 py-1.5 bg-white border border-slate-300 rounded-lg text-xs font-mono text-slate-800 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                        />
                      </div>
                      <div>
                        <label className="block text-[11px] font-semibold text-slate-600 mb-1">Fax</label>
                        <input
                          type="text"
                          value={partA1.fax || ''}
                          onChange={(e) => handlePayloadFieldChange(['part_a', 'details', 'fax'], e.target.value)}
                          className="w-full px-3 py-1.5 bg-white border border-slate-300 rounded-lg text-xs font-mono text-slate-800 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                        />
                      </div>
                      <div>
                        <label className="block text-[11px] font-semibold text-slate-600 mb-1">E-mail</label>
                        <input
                          type="text"
                          value={partA1.email || ''}
                          onChange={(e) => handlePayloadFieldChange(['part_a', 'details', 'email'], e.target.value)}
                          className="w-full px-3 py-1.5 bg-white border border-slate-300 rounded-lg text-xs text-slate-800 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                        />
                      </div>
                      <div>
                        <label className="block text-[11px] font-semibold text-slate-600 mb-1">Website</label>
                        <input
                          type="text"
                          value={partA1.website || ''}
                          onChange={(e) => handlePayloadFieldChange(['part_a', 'details', 'website'], e.target.value)}
                          className="w-full px-3 py-1.5 bg-white border border-slate-300 rounded-lg text-xs text-slate-800 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                        />
                      </div>
                    </div>
                  </div>

                  {/* Location & Addresses */}
                  <div className="space-y-3">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-emerald-700 flex items-center">
                      <MapPinIcon className="w-4 h-4 mr-1.5" /> Physical & Postal Locations
                    </h3>
                    <div className="grid grid-cols-3 gap-3 bg-slate-50 p-4 rounded-xl border border-slate-200">
                      <div>
                        <label className="block text-[11px] font-semibold text-slate-600 mb-1">Region</label>
                        <input
                          type="text"
                          value={partA1.region || ''}
                          onChange={(e) => handlePayloadFieldChange(['part_a', 'details', 'region'], e.target.value)}
                          className="w-full px-3 py-1.5 bg-white border border-slate-300 rounded-lg text-xs text-slate-800 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                        />
                      </div>
                      <div>
                        <label className="block text-[11px] font-semibold text-slate-600 mb-1">District</label>
                        <input
                          type="text"
                          value={partA1.district || ''}
                          onChange={(e) => handlePayloadFieldChange(['part_a', 'details', 'district'], e.target.value)}
                          className="w-full px-3 py-1.5 bg-white border border-slate-300 rounded-lg text-xs text-slate-800 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                        />
                      </div>
                      <div>
                        <label className="block text-[11px] font-semibold text-slate-600 mb-1">Area / Ward</label>
                        <input
                          type="text"
                          value={partA1.area || ''}
                          onChange={(e) => handlePayloadFieldChange(['part_a', 'details', 'area'], e.target.value)}
                          className="w-full px-3 py-1.5 bg-white border border-slate-300 rounded-lg text-xs text-slate-800 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                        />
                      </div>
                      <div>
                        <label className="block text-[11px] font-semibold text-slate-600 mb-1">Street / Plot</label>
                        <input
                          type="text"
                          value={partA1.street_plot || ''}
                          onChange={(e) => handlePayloadFieldChange(['part_a', 'details', 'street_plot'], e.target.value)}
                          className="w-full px-3 py-1.5 bg-white border border-slate-300 rounded-lg text-xs text-slate-800 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                        />
                      </div>
                      <div className="col-span-2">
                        <label className="block text-[11px] font-semibold text-slate-600 mb-1">Postal Address (P.O. Box)</label>
                        <input
                          type="text"
                          value={partA1.po_box || ''}
                          onChange={(e) => handlePayloadFieldChange(['part_a', 'details', 'po_box'], e.target.value)}
                          className="w-full px-3 py-1.5 bg-white border border-slate-300 rounded-lg text-xs text-slate-800 focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                        />
                      </div>
                    </div>
                  </div>

                  {/* Contact Persons */}
                  <div className="space-y-3">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-emerald-700 flex items-center">
                      <UserCircleIcon className="w-4 h-4 mr-1.5" /> Contact Personnel
                    </h3>
                    <div className="grid grid-cols-2 gap-3">
                      {/* Primary Contact */}
                      <div className="bg-slate-50 p-3 rounded-xl border border-slate-200 space-y-2">
                        <span className="text-[11px] font-bold text-slate-700">Primary Contact</span>
                        <input
                          type="text"
                          placeholder="Full Name"
                          value={partA1.primary_contact?.name || ''}
                          onChange={(e) => handlePayloadFieldChange(['part_a', 'details', 'primary_contact', 'name'], e.target.value)}
                          className="w-full px-2.5 py-1 bg-white border border-slate-300 rounded text-xs"
                        />
                        <input
                          type="text"
                          placeholder="Position"
                          value={partA1.primary_contact?.position || ''}
                          onChange={(e) => handlePayloadFieldChange(['part_a', 'details', 'primary_contact', 'position'], e.target.value)}
                          className="w-full px-2.5 py-1 bg-white border border-slate-300 rounded text-xs"
                        />
                        <div className="grid grid-cols-2 gap-2">
                          <input
                            type="text"
                            placeholder="Mobile"
                            value={partA1.primary_contact?.mobile || ''}
                            onChange={(e) => handlePayloadFieldChange(['part_a', 'details', 'primary_contact', 'mobile'], e.target.value)}
                            className="w-full px-2 py-1 bg-white border border-slate-300 rounded text-xs font-mono"
                          />
                          <input
                            type="text"
                            placeholder="Email"
                            value={partA1.primary_contact?.email || ''}
                            onChange={(e) => handlePayloadFieldChange(['part_a', 'details', 'primary_contact', 'email'], e.target.value)}
                            className="w-full px-2 py-1 bg-white border border-slate-300 rounded text-xs"
                          />
                        </div>
                      </div>

                      {/* Alternative Contact */}
                      <div className="bg-slate-50 p-3 rounded-xl border border-slate-200 space-y-2">
                        <span className="text-[11px] font-bold text-slate-700">Alternative Contact</span>
                        <input
                          type="text"
                          placeholder="Full Name"
                          value={partA1.alternative_contact?.name || ''}
                          onChange={(e) => handlePayloadFieldChange(['part_a', 'details', 'alternative_contact', 'name'], e.target.value)}
                          className="w-full px-2.5 py-1 bg-white border border-slate-300 rounded text-xs"
                        />
                        <input
                          type="text"
                          placeholder="Position"
                          value={partA1.alternative_contact?.position || ''}
                          onChange={(e) => handlePayloadFieldChange(['part_a', 'details', 'alternative_contact', 'position'], e.target.value)}
                          className="w-full px-2.5 py-1 bg-white border border-slate-300 rounded text-xs"
                        />
                        <div className="grid grid-cols-2 gap-2">
                          <input
                            type="text"
                            placeholder="Mobile"
                            value={partA1.alternative_contact?.mobile || ''}
                            onChange={(e) => handlePayloadFieldChange(['part_a', 'details', 'alternative_contact', 'mobile'], e.target.value)}
                            className="w-full px-2 py-1 bg-white border border-slate-300 rounded text-xs font-mono"
                          />
                          <input
                            type="text"
                            placeholder="Email"
                            value={partA1.alternative_contact?.email || ''}
                            onChange={(e) => handlePayloadFieldChange(['part_a', 'details', 'alternative_contact', 'email'], e.target.value)}
                            className="w-full px-2 py-1 bg-white border border-slate-300 rounded text-xs"
                          />
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* 2. Affiliates Status */}
                  <div className="space-y-3">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-emerald-700 flex items-center">
                      <GlobeAmericasIcon className="w-4 h-4 mr-1.5" /> 2. Affiliates & Reporting
                    </h3>
                    <div className="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-3">
                      <label className="flex items-center space-x-2 text-xs font-semibold text-slate-700 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={!!partA2.has_subsidiaries_in_tz}
                          onChange={(e) => handlePayloadFieldChange(['part_a', 'affiliates', 'has_subsidiaries_in_tz'], e.target.checked)}
                          className="rounded border-slate-300 text-emerald-600 focus:ring-emerald-500 w-4 h-4"
                        />
                        <span>Company has local subsidiaries in Tanzania</span>
                      </label>
                      <label className="flex items-center space-x-2 text-xs font-semibold text-slate-700 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={!!partA2.is_supplying_consolidated_info}
                          onChange={(e) => handlePayloadFieldChange(['part_a', 'affiliates', 'is_supplying_consolidated_info'], e.target.checked)}
                          className="rounded border-slate-300 text-emerald-600 focus:ring-emerald-500 w-4 h-4"
                        />
                        <span>Supplying consolidated financial and operational information</span>
                      </label>
                    </div>
                  </div>

                  {/* 3. Acknowledgement of Receipt — printed as section A3 on page 2.
                      Evidence-bearing: an unsigned or unacknowledged return is
                      itself a control exception, so it must be visible on review. */}
                  <div className="space-y-3">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-emerald-700 flex items-center">
                      <ClipboardDocumentCheckIcon className="w-4 h-4 mr-1.5" /> 3. Acknowledgement of Receipt (A3)
                    </h3>
                    <div className="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-3">
                      <div className="grid grid-cols-2 gap-3">
                        <div>
                          <label className="block text-[11px] font-semibold text-slate-600 mb-1">Received by (name)</label>
                          <input
                            type="text"
                            value={partA3.recipient_name || ''}
                            onChange={(e) => handlePayloadFieldChange(['part_a', 'acknowledgement', 'recipient_name'], e.target.value)}
                            className="w-full px-3 py-1.5 bg-white border border-slate-300 rounded-lg text-xs text-slate-800"
                          />
                        </div>
                        <div>
                          <label className="block text-[11px] font-semibold text-slate-600 mb-1">Of (company)</label>
                          <input
                            type="text"
                            value={partA3.recipient_company || ''}
                            onChange={(e) => handlePayloadFieldChange(['part_a', 'acknowledgement', 'recipient_company'], e.target.value)}
                            className="w-full px-3 py-1.5 bg-white border border-slate-300 rounded-lg text-xs text-slate-800"
                          />
                        </div>
                        <div>
                          <label className="block text-[11px] font-semibold text-slate-600 mb-1">Title</label>
                          <input
                            type="text"
                            value={partA3.title || ''}
                            onChange={(e) => handlePayloadFieldChange(['part_a', 'acknowledgement', 'title'], e.target.value)}
                            className="w-full px-3 py-1.5 bg-white border border-slate-300 rounded-lg text-xs text-slate-800"
                          />
                        </div>
                        <div>
                          <label className="block text-[11px] font-semibold text-slate-600 mb-1">Tel / Mobile no.</label>
                          <input
                            type="text"
                            value={partA3.tel_mobile || ''}
                            onChange={(e) => handlePayloadFieldChange(['part_a', 'acknowledgement', 'tel_mobile'], e.target.value)}
                            className="w-full px-3 py-1.5 bg-white border border-slate-300 rounded-lg text-xs font-mono text-slate-800"
                          />
                        </div>
                        <div>
                          <label className="block text-[11px] font-semibold text-slate-600 mb-1">Date</label>
                          <input
                            type="text"
                            value={partA3.date || ''}
                            onChange={(e) => handlePayloadFieldChange(['part_a', 'acknowledgement', 'date'], e.target.value)}
                            className="w-full px-3 py-1.5 bg-white border border-slate-300 rounded-lg text-xs font-mono text-slate-800"
                          />
                        </div>
                        <div className="flex items-end">
                          <label
                            title="The model can confirm a signature is present but cannot transcribe one, so only presence is recorded."
                            className="flex items-center space-x-2 text-xs font-semibold text-slate-700 cursor-pointer bg-white border border-slate-300 rounded-lg px-3 py-2 w-full"
                          >
                            <input
                              type="checkbox"
                              checked={!!partA3.signature_present}
                              onChange={(e) => handlePayloadFieldChange(['part_a', 'acknowledgement', 'signature_present'], e.target.checked)}
                              className="rounded border-slate-300 text-emerald-600 focus:ring-emerald-500 w-4 h-4"
                            />
                            <span>
                              Signature present
                              {!partA3.signature_present && (
                                <span className="ml-1.5 text-[10px] font-bold uppercase text-amber-700">
                                  — unverified
                                </span>
                              )}
                            </span>
                          </label>
                        </div>
                      </div>
                      <div className="grid grid-cols-2 gap-3 pt-1 border-t border-slate-200">
                        <div>
                          <label className="block text-[11px] font-semibold text-slate-600 mb-1">Researcher — name</label>
                          <input
                            type="text"
                            value={partA3.researcher?.name || ''}
                            onChange={(e) => handlePayloadFieldChange(['part_a', 'acknowledgement', 'researcher', 'name'], e.target.value)}
                            className="w-full px-3 py-1.5 bg-white border border-slate-300 rounded-lg text-xs text-slate-800"
                          />
                        </div>
                        <div>
                          <label className="block text-[11px] font-semibold text-slate-600 mb-1">Researcher — mobile</label>
                          <input
                            type="text"
                            value={partA3.researcher?.mobile || ''}
                            onChange={(e) => handlePayloadFieldChange(['part_a', 'acknowledgement', 'researcher', 'mobile'], e.target.value)}
                            className="w-full px-3 py-1.5 bg-white border border-slate-300 rounded-lg text-xs font-mono text-slate-800"
                          />
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* 4. Industrial Classifications — A5, printed with THREE columns */}
                  <div className="space-y-3">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-emerald-700 flex items-center">
                      <BriefcaseIcon className="w-4 h-4 mr-1.5" /> 4. Industrial Classifications (A5)
                    </h3>
                    <div className="space-y-2">
                      {classifications.length === 0 ? (
                        <p className="text-xs text-slate-500 italic">No industrial classifications recorded.</p>
                      ) : (
                        classifications.map((item: any, idx: number) => (
                          <div key={idx} className="flex items-center space-x-2 bg-slate-50 p-2.5 rounded-lg border border-slate-200">
                            {/* A5 column 1 — the classification itself. Was missing
                                entirely, which is the column BoT maps to a sector. */}
                            <input
                              type="text"
                              value={item.activity || ''}
                              onChange={(e) => {
                                const updated = [...classifications];
                                updated[idx] = { ...updated[idx], activity: e.target.value };
                                handlePayloadFieldChange(['part_a', 'industrial_classifications'], updated);
                              }}
                              placeholder="Activity / industrial classification"
                              className="w-44 shrink-0 px-2.5 py-1 bg-white border border-slate-300 rounded text-xs font-medium"
                            />
                            <input
                              type="text"
                              value={item.activity_description || ''}
                              onChange={(e) => {
                                const updated = [...classifications];
                                updated[idx] = { ...updated[idx], activity_description: e.target.value };
                                handlePayloadFieldChange(['part_a', 'industrial_classifications'], updated);
                              }}
                              placeholder="Description of the economic activity"
                              className="flex-1 px-2.5 py-1 bg-white border border-slate-300 rounded text-xs"
                            />
                            <div className="flex items-center space-x-1">
                              <NumberInput
                                value={num(item.estimated_percentage_contribution)}
                                onChange={(v) => {
                                  const updated = [...classifications];
                                  updated[idx] = { ...updated[idx], estimated_percentage_contribution: v };
                                  handlePayloadFieldChange(['part_a', 'industrial_classifications'], updated);
                                }}
                                className="w-20 px-2 py-1 bg-white border border-slate-300 rounded text-xs font-mono text-right"
                                title="Percentage contribution (0–100)"
                              />
                              <span className="text-xs text-slate-500">%</span>
                            </div>
                          </div>
                        ))
                      )}
                    </div>
                  </div>

                  {/* 5. Shareholding Structure Table — A6 */}
                  <div className="space-y-3">
                    <h3 className="text-xs font-bold uppercase tracking-wider text-emerald-700 flex items-center">
                      <BuildingOffice2Icon className="w-4 h-4 mr-1.5" /> 5. Shareholding Structure (A6)
                    </h3>
                    <div className="overflow-x-auto border border-slate-200 rounded-xl bg-slate-50">
                      <table className="w-full text-left text-xs">
                        <thead className="bg-slate-100 text-slate-700 border-b border-slate-200 font-semibold">
                          <tr>
                            <th className="p-2">Shareholder Name</th>
                            <th className="p-2">Source Country / Multilateral</th>
                            <th className="p-2 text-right">T-1 (%)</th>
                            <th className="p-2">Relationship (T-1)</th>
                            <th className="p-2 text-right">T (%)</th>
                            <th className="p-2">Relationship (T)</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-200">
                          {shareholders.length === 0 ? (
                            <tr>
                              <td colSpan={6} className="p-3 text-center text-slate-500 italic">No shareholding records found.</td>
                            </tr>
                          ) : (
                            shareholders.map((sh: any, idx: number) => (
                              <tr key={idx}>
                                <td className="p-2">
                                  <input
                                    type="text"
                                    value={sh.shareholder_name || ''}
                                    onChange={(e) => {
                                      const updated = [...shareholders];
                                      updated[idx] = { ...updated[idx], shareholder_name: e.target.value };
                                      handlePayloadFieldChange(['part_a', 'shareholding_structure'], updated);
                                    }}
                                    className="w-full px-2 py-1 bg-white border border-slate-300 rounded text-xs"
                                  />
                                </td>
                                <td className="p-2">
                                  <input
                                    type="text"
                                    value={sh.source_country_or_multilateral || ''}
                                    onChange={(e) => {
                                      const updated = [...shareholders];
                                      updated[idx] = { ...updated[idx], source_country_or_multilateral: e.target.value };
                                      handlePayloadFieldChange(['part_a', 'shareholding_structure'], updated);
                                    }}
                                    className="w-32 px-2 py-1 bg-white border border-slate-300 rounded text-xs"
                                  />
                                </td>
                                <td className="p-2 text-right">
                                  <NumberInput
                                    value={num(sh.previous_year_shareholding_pct)}
                                    onChange={(v) => {
                                      const updated = [...shareholders];
                                      updated[idx] = { ...updated[idx], previous_year_shareholding_pct: v };
                                      handlePayloadFieldChange(['part_a', 'shareholding_structure'], updated);
                                    }}
                                    className="w-16 px-1.5 py-1 bg-white border border-slate-300 rounded text-xs font-mono text-right"
                                  />
                                </td>
                                {/* A6 prints a Relationship column for BOTH years.
                                    The T-1 column was missing from this panel. */}
                                <td className="p-2">
                                  <SelectInput
                                    value={sh.previous_year_relationship || 'OTHER'}
                                    onChange={(v) => {
                                      const updated = [...shareholders];
                                      updated[idx] = { ...updated[idx], previous_year_relationship: v };
                                      handlePayloadFieldChange(['part_a', 'shareholding_structure'], updated);
                                    }}
                                    options={RELATIONSHIP_OPTIONS}
                                    className="w-52 px-1.5 py-1 rounded text-xs"
                                  />
                                </td>
                                <td className="p-2 text-right">
                                  <NumberInput
                                    value={num(sh.reporting_year_shareholding_pct)}
                                    onChange={(v) => {
                                      const updated = [...shareholders];
                                      updated[idx] = { ...updated[idx], reporting_year_shareholding_pct: v };
                                      handlePayloadFieldChange(['part_a', 'shareholding_structure'], updated);
                                    }}
                                    className="w-16 px-1.5 py-1 bg-white border border-slate-300 rounded text-xs font-mono text-right"
                                  />
                                </td>
                                <td className="p-2">
                                  <SelectInput
                                    value={sh.reporting_year_relationship || 'OTHER'}
                                    onChange={(v) => {
                                      const updated = [...shareholders];
                                      updated[idx] = { ...updated[idx], reporting_year_relationship: v };
                                      handlePayloadFieldChange(['part_a', 'shareholding_structure'], updated);
                                    }}
                                    options={RELATIONSHIP_OPTIONS}
                                    className="w-52 px-1.5 py-1 rounded text-xs"
                                  />
                                </td>
                              </tr>
                            ))
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              )}

              {/* ══════════════════════ PART B TAB ══════════════════════ */}
              {activeTab === 'partB' && (
                <div className="space-y-5">
                  <div className="flex items-center justify-between border-b pb-2 gap-3">
                    <h3 className="text-sm font-bold text-slate-800 flex items-center">
                      <BanknotesIcon className="w-4 h-4 mr-1.5 text-emerald-700" />
                      Part B — Equity Investment
                    </h3>
                    <div className="flex items-center gap-2 shrink-0">
                      <label className="text-[11px] font-semibold text-slate-600">
                        Currency used
                      </label>
                      <SelectInput
                        value={ccy}
                        onChange={(v) => handlePayloadFieldChange(['part_b', 'currency_used'], v)}
                        options={CURRENCY_OPTIONS}
                        className="w-24 px-2 py-1 rounded-lg text-xs font-mono"
                        title="part_b.currency_used — applies to every amount on this tab"
                      />
                    </div>
                  </div>

                  {/* ── Table C2 reference rates ──
                      The form requires a currency choice here and instructs the
                      respondent to "refer to a table of exchange rates in the last
                      page". Without the applied rate, a TZS figure cannot be
                      re-derived from its USD equivalent, and the official-use D2
                      column cannot be independently verified. */}
                  <div className="bg-slate-50 border border-slate-200 rounded-xl p-3">
                    <div className="flex items-center justify-between gap-2 mb-2">
                      <h4 className="text-[11px] font-bold uppercase tracking-wider text-slate-600">
                        Table C2 — TZS/USD reference rates
                      </h4>
                      <span className="text-[10px] text-slate-500 font-mono">
                        {rates.source || 'Table C2'}
                      </span>
                    </div>
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                      {[
                        { key: 'end_of_previous_year', label: `End of ${prevYear}` },
                        { key: 'average_previous_year', label: `${prevYear} average` },
                        { key: 'end_of_reporting_year', label: `End of ${repYear}` },
                        { key: 'average_reporting_year', label: `${repYear} average` },
                      ].map((r) => (
                        <div key={r.key}>
                          <label className="block text-[10px] font-semibold text-slate-500 mb-0.5">
                            {r.label}
                          </label>
                          <NumberInput
                            value={num(rates[r.key])}
                            onChange={(v) =>
                              handlePayloadFieldChange(['metadata', 'exchange_rates', r.key], v)
                            }
                            className="w-full px-2 py-1 bg-white border border-slate-300 rounded text-xs font-mono text-right focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                            title={`metadata.exchange_rates.${r.key}`}
                          />
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* ── Table B1 ── */}
                  <div className="space-y-2">
                    <div className="flex items-baseline justify-between gap-2">
                      <h4 className="text-xs font-bold uppercase tracking-wider text-slate-700">
                        Table B1 — Direct investment equity ({ccy})
                      </h4>
                      <span className="text-[10px] text-slate-500 font-mono">
                        E = A + B − C + D1 + D2 + D3
                      </span>
                    </div>
                    <div className="overflow-x-auto border border-slate-200 rounded-xl bg-white">
                      <table className="w-full text-left text-xs min-w-[840px]">
                        <thead>
                          <tr className="bg-slate-100 text-slate-700 border-b border-slate-200">
                            <th className="p-2 sticky left-0 bg-slate-100 z-10 min-w-[200px] text-[11px] font-semibold">
                              Component
                            </th>
                            {EQUITY_COLUMNS.map((c) => (
                              <th key={c.key} className="p-2 text-right whitespace-nowrap align-bottom">
                                <span className="font-bold text-slate-800">{c.letter}</span>
                                <span className="block font-normal text-[10px] text-slate-500">
                                  {c.label}
                                  {c.year === 'prev' ? ` ${prevYear}` : ''}
                                  {c.year === 'rep' ? ` ${repYear}` : ''}
                                </span>
                                {c.official && (
                                  <span className="block text-[9px] font-semibold text-amber-600">
                                    official use only
                                  </span>
                                )}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {B1_ROWS.map((row) => {
                            const r = partB1[row.key] || {};
                            const expectedE =
                              num(r.previous_year_closing_A) +
                              num(r.purchase_increase_B) -
                              num(r.sales_decrease_C) +
                              num(r.other_changes_price_D1) +
                              num(r.other_changes_exchange_rate_D2) +
                              num(r.other_changes_volume_D3);
                            const rowPath = `part_b.table_b1.${row.key}`;
                            const rowNa = isNa(rowPath);
                            return (
                              <tr
                                key={row.key}
                                className={`border-b border-slate-100 last:border-b-0 ${
                                  rowNa ? 'bg-slate-50' : ''
                                }`}
                              >
                                <td
                                  className={`p-1.5 sticky left-0 ${rowNa ? 'bg-slate-50' : 'bg-white'}`}
                                  title={rowPath}
                                >
                                  <div className="flex items-center justify-between gap-2">
                                    <span className="text-[11px] font-medium text-slate-800">
                                      {row.label}
                                    </span>
                                    <NaToggle
                                      checked={rowNa}
                                      onChange={(on) => setNa(rowPath, on)}
                                      title={`Mark ${row.label} as N/A (${rowPath})`}
                                    />
                                  </div>
                                </td>
                                {EQUITY_COLUMNS.map((col) => (
                                  <td key={col.key} className="p-1.5">
                                    <div className="flex items-center justify-end gap-0.5">
                                      <NumberInput
                                        value={num(r[col.key])}
                                        onChange={(v) =>
                                          handlePayloadFieldChange(
                                            ['part_b', 'table_b1', row.key, col.key],
                                            v,
                                          )
                                        }
                                        disabled={rowNa}
                                        className="w-24 px-2 py-1 bg-white border border-slate-300 rounded text-xs font-mono text-right focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                                        title={`part_b.table_b1.${row.key}.${col.key}`}
                                      />
                                      {col.key === 'reporting_year_closing_E' && !rowNa && (
                                        <ReconNote
                                          expected={expectedE}
                                          actual={num(r.reporting_year_closing_E)}
                                          label="Column E"
                                        />
                                      )}
                                    </div>
                                  </td>
                                ))}
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>

                  {/* ── Table B2 ── */}
                  <div className="space-y-2">
                    <div className="flex items-baseline justify-between gap-2">
                      <h4 className="text-xs font-bold uppercase tracking-wider text-slate-700">
                        Table B2 — Profits & dividends ({ccy})
                      </h4>
                      <span className="text-[10px] text-slate-500 font-mono">D = A − B</span>
                    </div>
                    <div className="overflow-x-auto border border-slate-200 rounded-xl bg-white">
                      <table className="w-full text-left text-xs">
                        <thead>
                          <tr className="bg-slate-100 text-slate-700 border-b border-slate-200">
                            <th className="p-2 text-[11px] font-semibold">Item</th>
                            <th className="p-2 text-right text-[11px] font-semibold w-40">
                              Amount ({ccy})
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {B2_ROWS.map((row) => {
                            const expectedD =
                              num(partB2.net_profit_or_loss_after_tax_A) -
                              num(partB2.dividends_declared_B);
                            const b2Path = `part_b.table_b2.${row.key}`;
                            const b2Na = isNa(b2Path);
                            return (
                              <tr
                                key={row.key}
                                className={`border-b border-slate-100 last:border-b-0 ${
                                  b2Na ? 'bg-slate-50' : ''
                                }`}
                              >
                                <td className="p-2" title={b2Path}>
                                  <div className="flex items-center justify-between gap-2">
                                    <span>
                                      <span className="font-bold text-slate-500 mr-1.5">
                                        {row.letter}
                                      </span>
                                      <span className="text-[11px] text-slate-800">{row.label}</span>
                                      {row.key === 'retained_earnings_D' && (
                                        <span className="ml-1.5 text-[9px] font-semibold text-amber-600">
                                          official use only
                                        </span>
                                      )}
                                    </span>
                                    <NaToggle
                                      checked={b2Na}
                                      onChange={(on) => setNa(b2Path, on)}
                                      title={`Mark ${row.label} as N/A (${b2Path})`}
                                    />
                                  </div>
                                </td>
                                <td className="p-1.5">
                                  <div className="flex items-center justify-end gap-0.5">
                                    <NumberInput
                                      value={num(partB2[row.key])}
                                      onChange={(v) =>
                                        handlePayloadFieldChange(['part_b', 'table_b2', row.key], v)
                                      }
                                      disabled={b2Na}
                                      className="w-28 px-2 py-1 bg-white border border-slate-300 rounded text-xs font-mono text-right focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                                      title={b2Path}
                                    />
                                    {row.key === 'retained_earnings_D' && !b2Na && (
                                      <ReconNote
                                        expected={expectedD}
                                        actual={num(partB2.retained_earnings_D)}
                                        label="Row D"
                                      />
                                    )}
                                  </div>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              )}

              {/* ══════════════════════ PART C TAB ══════════════════════ */}
              {activeTab === 'partC' && (
                <div className="space-y-4">
                  <div className="flex items-center justify-between border-b pb-2 gap-3">
                    <h3 className="text-sm font-bold text-slate-800 flex items-center">
                      <TableCellsIcon className="w-4 h-4 mr-1.5 text-emerald-700" />
                      Part C — Non-Equity Investments ({liabilities.length} record
                      {liabilities.length === 1 ? '' : 's'})
                    </h3>
                    <button
                      onClick={addLiability}
                      className="flex items-center px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg text-[11px] font-semibold transition shrink-0"
                    >
                      <PlusIcon className="w-3.5 h-3.5 mr-1" /> Add record
                    </button>
                  </div>

                  <p className="text-[10px] text-slate-500 font-mono">
                    E = A + B − C + D1 + D2 + D3 ·  amounts in {ccy === 'USD' ? 'USD' : 'the '
                      + 'reporting currency of the return'}
                  </p>

                  {/* Reserved table. It is empty for every filing unless a
                      Part C(II) page is supplied — but if data ever does arrive
                      here, hiding it would be worse than saying so. */}
                  {Array.isArray(payload.part_c_assets) && payload.part_c_assets.length > 0 && (
                    <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
                      <p className="text-xs font-bold text-amber-800">
                        {payload.part_c_assets.length} record
                        {payload.part_c_assets.length === 1 ? '' : 's'} present in{' '}
                        <code className="font-mono">part_c_assets</code>
                      </p>
                      <p className="text-[11px] text-amber-800 mt-1 leading-relaxed">
                        This is the reserved Part C(II) asset-side table. It is not yet
                        rendered here, so these values are <b>not visible for review</b>.
                        Confirm the filled questionnaire has no Part C(II) page before
                        approving this filing.
                      </p>
                    </div>
                  )}

                  {liabilities.length === 0 ? (
                    <p className="text-xs text-slate-500 italic">
                      No liability records captured. Use &ldquo;Add record&rdquo; for each
                      line item in Part C of the submitted document.
                    </p>
                  ) : (
                    <div className="overflow-x-auto border border-slate-200 rounded-xl bg-white">
                      <table className="w-full text-left text-xs min-w-[1180px]">
                        <thead>
                          <tr className="bg-slate-100 text-slate-700 border-b border-slate-200">
                            <th className="p-2 min-w-[210px] text-[11px] font-semibold align-bottom">
                              Liability category
                            </th>
                            <th className="p-2 min-w-[130px] text-[11px] font-semibold align-bottom">
                              Source country / multilateral
                            </th>
                            <th className="p-2 min-w-[150px] text-[11px] font-semibold align-bottom">
                              Relationship
                            </th>
                            <th className="p-2 min-w-[90px] text-[11px] font-semibold align-bottom">
                              Maturity
                            </th>
                            {LIABILITY_COLUMNS.map((c) => (
                              <th
                                key={c.key}
                                className="p-2 text-right whitespace-nowrap align-bottom"
                              >
                                <span className="font-bold text-slate-800">{c.letter}</span>
                                <span className="block font-normal text-[10px] text-slate-500">
                                  {c.label}
                                </span>
                                {c.key.startsWith('other_changes') && (
                                  <span className="block text-[9px] font-semibold text-amber-600">
                                    official use only
                                  </span>
                                )}
                              </th>
                            ))}
                            <th className="p-2 w-16" />
                          </tr>
                        </thead>
                        <tbody>
                          {liabilities.map((rec: any, idx: number) => {
                            const expectedE =
                              num(rec.previous_year_closing_A) +
                              num(rec.amount_received_B) -
                              num(rec.principal_repayment_C) +
                              num(rec.other_changes_price_D1) +
                              num(rec.other_changes_exchange_rate_D2) +
                              num(rec.other_changes_volume_D3);
                            const rowPath = `part_c_liabilities[${idx}]`;
                            const rowNa = isNa(rowPath);
                            return (
                              <tr
                                key={idx}
                                className={`border-b border-slate-100 last:border-b-0 align-top ${
                                  rowNa ? 'bg-slate-50 opacity-70' : ''
                                }`}
                              >
                                <td className="p-1.5">
                                  <SelectInput
                                    value={
                                      rec.liability_category ||
                                      'Loans (Including Financial Leases, Repos)'
                                    }
                                    onChange={(v) => updateLiability(idx, 'liability_category', v)}
                                    options={LIABILITY_CATEGORY_OPTIONS}
                                    className="w-full px-2 py-1 rounded text-[11px]"
                                    title={`part_c_liabilities[${idx}].liability_category`}
                                  />
                                </td>
                                <td className="p-1.5">
                                  <input
                                    type="text"
                                    value={rec.source_country_or_multilateral || ''}
                                    onChange={(e) =>
                                      updateLiability(
                                        idx,
                                        'source_country_or_multilateral',
                                        e.target.value,
                                      )
                                    }
                                    className="w-32 px-2 py-1 bg-white border border-slate-300 rounded text-xs"
                                    title={`part_c_liabilities[${idx}].source_country_or_multilateral`}
                                  />
                                </td>
                                <td className="p-1.5">
                                  <SelectInput
                                    value={rec.relationship || 'OTHER'}
                                    onChange={(v) => updateLiability(idx, 'relationship', v)}
                                    options={RELATIONSHIP_OPTIONS}
                                    className="w-full px-2 py-1 rounded text-[11px]"
                                    title={`part_c_liabilities[${idx}].relationship`}
                                  />
                                </td>
                                <td className="p-1.5">
                                  <SelectInput
                                    value={rec.original_maturity || 'LT'}
                                    onChange={(v) => updateLiability(idx, 'original_maturity', v)}
                                    options={MATURITY_OPTIONS}
                                    className="w-full px-2 py-1 rounded text-[11px]"
                                    title={`part_c_liabilities[${idx}].original_maturity`}
                                  />
                                </td>
                                {LIABILITY_COLUMNS.map((col) => (
                                  <td key={col.key} className="p-1.5">
                                    <div className="flex items-center justify-end gap-0.5">
                                      <NumberInput
                                        value={num(rec[col.key])}
                                        onChange={(v) => updateLiability(idx, col.key, v)}
                                        disabled={rowNa}
                                        className="w-24 px-2 py-1 bg-white border border-slate-300 rounded text-xs font-mono text-right focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                                        title={`${rowPath}.${col.key}`}
                                      />
                                      {col.key === 'reporting_year_closing_E' && !rowNa && (
                                        <ReconNote
                                          expected={expectedE}
                                          actual={num(rec.reporting_year_closing_E)}
                                          label="Column E"
                                        />
                                      )}
                                    </div>
                                  </td>
                                ))}
                                <td className="p-1.5">
                                  <div className="flex items-center justify-center gap-2">
                                    <NaToggle
                                      checked={rowNa}
                                      onChange={(on) => setNa(rowPath, on)}
                                      title={`Mark this record as N/A (${rowPath})`}
                                    />
                                    <button
                                      onClick={() => removeLiability(idx)}
                                      title="Remove this record"
                                      className="p-1 text-slate-400 hover:text-red-600 hover:bg-red-50 rounded transition"
                                    >
                                      <TrashIcon className="w-4 h-4" />
                                    </button>
                                  </div>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}

                  {/* Memorandum — the guidelines (page 4) define "Standardised
                      Guarantee" at length, but Table C1 has no row for it.
                      Off-balance-sheet guarantees are a classic unrecorded
                      exposure, so the figure gets somewhere to live pending
                      confirmation of where BoT/NBS expects it. */}
                  <div className="bg-slate-50 border border-slate-200 rounded-xl p-3">
                    <h4 className="text-[11px] font-bold uppercase tracking-wider text-slate-600 mb-2">
                      Memorandum — items defined in the guidelines with no C1 row
                    </h4>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label
                          className="block text-[11px] font-semibold text-slate-600 mb-1"
                          title="Guarantees not provided by means of a financial derivative, for which the probability of default can be well established (e.g. government export-credit or student-loan guarantees)."
                        >
                          Standardised guarantees
                        </label>
                        <NumberInput
                          value={num(payload.part_c_memorandum?.standardised_guarantees)}
                          onChange={(v) =>
                            handlePayloadFieldChange(
                              ['part_c_memorandum', 'standardised_guarantees'],
                              v,
                            )
                          }
                          className="w-full px-2 py-1 bg-white border border-slate-300 rounded text-xs font-mono text-right focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                          title="part_c_memorandum.standardised_guarantees"
                        />
                      </div>
                      <div>
                        <label className="block text-[11px] font-semibold text-slate-600 mb-1">
                          Memorandum note
                        </label>
                        <input
                          type="text"
                          value={payload.part_c_memorandum?.memorandum_note || ''}
                          onChange={(e) =>
                            handlePayloadFieldChange(
                              ['part_c_memorandum', 'memorandum_note'],
                              e.target.value,
                            )
                          }
                          placeholder="Where this figure came from, if not a printed C1 row"
                          className="w-full px-2 py-1 bg-white border border-slate-300 rounded text-xs"
                        />
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* ══════════════════════ PART D TAB ══════════════════════ */}
              {activeTab === 'partD' && (
                <div className="space-y-4">
                  <div className="flex items-center justify-between border-b pb-2 gap-3">
                    <h3 className="text-sm font-bold text-slate-800 flex items-center">
                      <UsersIcon className="w-4 h-4 mr-1.5 text-emerald-700" />
                      Part D — FATS Statistics
                    </h3>
                    <span className="text-[10px] text-slate-500 font-mono">
                      Items 1–7 in {ccy} · 8–10 headcount · 11–14 {ccy}
                    </span>
                  </div>

                  <div className="overflow-x-auto border border-slate-200 rounded-xl bg-white">
                    <table className="w-full text-left text-xs min-w-[520px]">
                      <thead>
                        <tr className="bg-slate-100 text-slate-700 border-b border-slate-200">
                          <th className="p-2 text-[11px] font-semibold">Item</th>
                          <th className="p-2 text-right text-[11px] font-semibold w-36">
                            T-1 ({prevYear})
                          </th>
                          <th className="p-2 text-right text-[11px] font-semibold w-36">
                            T ({repYear})
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {FATS_ITEMS.map((item, i) => {
                          const node = partD[item.key] || {};
                          const k = fatsKeys(item.kind);
                          const prevVal = num(node[k.prev]);
                          const repVal = num(node[k.rep]);

                          const itemPath = `part_d_fats.${item.key}`;
                          const itemNa = isNa(itemPath);

                          const expectedNetWorth =
                            num(partD.total_assets_5?.[k.rep]) -
                            num(partD.total_liabilities_6?.[k.rep]);

                          // The printed "o/w" (of which) relationships. Items 9 and
                          // 10 must sum to item 8; items 12, 13 and 14 must sum to
                          // item 11. A mismatch is an audit finding.
                          const expectedEmployees =
                            num(partD.professionals_count_9?.[k.rep]) +
                            num(partD.non_professionals_count_10?.[k.rep]);
                          const expectedCompensation =
                            num(partD.compensation_short_term_foreign_12?.[k.rep]) +
                            num(partD.compensation_long_term_foreign_13?.[k.rep]) +
                            num(partD.compensation_local_14?.[k.rep]);
                          const isOweOf =
                            item.key === 'total_number_of_employees_8' ||
                            item.key === 'total_employee_compensation_11';

                          return (
                            <React.Fragment key={item.key}>
                              {(i === 0 || FATS_ITEMS[i - 1].group !== item.group) && (
                                <tr className="bg-slate-50">
                                  <td
                                    colSpan={3}
                                    className="px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-slate-500"
                                  >
                                    {item.group}
                                  </td>
                                </tr>
                              )}
                              <tr
                                className={`border-b border-slate-100 last:border-b-0 ${
                                  itemNa ? 'bg-slate-50' : ''
                                }`}
                                title={itemPath}
                              >
                                <td className="p-2">
                                  <div className="flex items-center justify-between gap-2">
                                    <span>
                                      <span className="text-[11px] text-slate-800">{item.label}</span>
                                      {item.kind === 'count' && (
                                        <span className="ml-1.5 text-[9px] text-slate-400 font-mono">
                                          count
                                        </span>
                                      )}
                                    </span>
                                    <NaToggle
                                      checked={itemNa}
                                      onChange={(on) => setNa(itemPath, on)}
                                      title={`Mark ${item.label} as N/A (${itemPath})`}
                                    />
                                  </div>
                                </td>
                                <td className="p-1.5">
                                  <NumberInput
                                    integer={item.kind === 'count'}
                                    value={prevVal}
                                    disabled={itemNa}
                                    onChange={(v) =>
                                      handlePayloadFieldChange(
                                        ['part_d_fats', item.key, k.prev],
                                        v,
                                      )
                                    }
                                    className="w-32 px-2 py-1 bg-white border border-slate-300 rounded text-xs font-mono text-right focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                                    title={`${itemPath}.${k.prev}`}
                                  />
                                </td>
                                <td className="p-1.5">
                                  <div className="flex items-center justify-end gap-0.5">
                                    <NumberInput
                                      integer={item.kind === 'count'}
                                      value={repVal}
                                      disabled={itemNa}
                                      onChange={(v) =>
                                        handlePayloadFieldChange(
                                          ['part_d_fats', item.key, k.rep],
                                          v,
                                        )
                                      }
                                      className="w-32 px-2 py-1 bg-white border border-slate-300 rounded text-xs font-mono text-right focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                                      title={`${itemPath}.${k.rep}`}
                                    />
                                    {!itemNa && item.key === 'net_worth_7' && (
                                      <ReconNote
                                        expected={expectedNetWorth}
                                        actual={repVal}
                                        label="Net worth"
                                      />
                                    )}
                                    {!itemNa &&
                                      isOweOf &&
                                      (expectedEmployees > 0 || expectedCompensation > 0) && (
                                        <ReconNote
                                          expected={
                                            item.key === 'total_number_of_employees_8'
                                              ? expectedEmployees
                                              : expectedCompensation
                                          }
                                          actual={repVal}
                                          label={
                                            item.key === 'total_number_of_employees_8'
                                              ? 'Total vs 9+10'
                                              : 'Total vs 12+13+14'
                                          }
                                        />
                                      )}
                                  </div>
                                </td>
                              </tr>
                            </React.Fragment>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

            </div>
          </div>
        </div>
      </div>
    </div>
  );
}