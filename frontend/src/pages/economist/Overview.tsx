import React, { useEffect, useState, useCallback, useMemo } from 'react';
import { getSubmissions, clearSubmissions, commitAuditDecision } from '../../services/api';
import  AuditReview  from './AuditReview';

// ══════════════════════════════════════════════════════════════════════════
//  ⚙️  CHOOSE THE ENDPOINT THAT ACTUALLY SERVES THE PDF
//      'api'    ->  {base}/api/v1/ingest/document-file/{filingId}   (default)
//      'static' ->  {base}/files/{filename}
//  The modal was calling the 'api' route and got a meaningful JSON 404 back
//  ("...not found on server"), which means that route EXISTS — so 'api' is the
//  best first guess. If it 404s with a REAL id, switch to 'static'.
//  ⚠️ This constant must stay identical in Overview.tsx and AuditReview.tsx.
// ══════════════════════════════════════════════════════════════════════════
const DOCUMENT_ENDPOINT: 'api' | 'static' = 'api';

// --- Interfaces ---
export interface LineItem {
  description: string;
  amount_tzs: number;
  amount_usd: number;
}

export interface SubmissionRecord {
  id?: string;
  filing_id?: string;
  submission_id?: string;
  investor_entity?: string;
  company_name?: string;
  tin_number?: string;
  reporting_period?: string;
  document_type?: string;
  bpm6_category?: string;
  total_usd?: number;
  total_tzs?: number;
  total_assets_usd?: number;
  total_assets_tzs?: number;
  extracted_payload?: {
    total_usd?: number;
    total_tzs?: number;
    total_assets_usd?: number;
    total_assets_tzs?: number;
    filing_id?: string;
    [key: string]: any;
  };
  vlm_score?: string;
  confidence_score?: number;
  status: string;
  audit_notes?: string;
  file_url?: string;
  line_items?: LineItem[];
  // Added after the "undefined not found on server" bug — the modal was reading
  // `.id` only, while the API returns the identifier under filing_id.
  submission_ref?: string;
  document_name?: string;
}

/* ══════════════════════════════════════════════════════════════════════════
 * SHARED HELPERS
 * This block is duplicated verbatim in Overview.tsx and AuditReview.tsx so
 * both files are self-contained and paste-ready. If you later want a single
 * copy, move it to src/lib/filingDocument.ts and import it in both.
 * ⚠️ KEEP THE TWO COPIES IN SYNC.
 * ══════════════════════════════════════════════════════════════════════════ */

// Checked most-specific first.
const ID_KEYS = [
  'filing_id', 'filingId', 'submission_id', 'submissionId',
  'submission_ref', 'record_id', 'recordId', 'uuid', '_id', 'id',
] as const;

// Some APIs nest the identifier inside the extraction payload instead.
const NESTED_KEYS = [
  'extracted_payload', 'payload', 'metadata', 'filing', 'submission', 'record',
] as const;

const looksLikeId = (v: unknown): v is string =>
  typeof v === 'string' && v.trim().length > 0 && v !== 'undefined' && v !== 'null';

/**
 * Extract the filing identifier from whatever shape the API returns.
 * Returns null when nothing usable exists, so the literal string "undefined"
 * can never end up in a URL again.
 */
export function resolveFilingId(record: any): string | null {
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

  // Numeric ids: 0 is falsy, so check the type explicitly.
  for (const key of ID_KEYS) {
    const v = (record as any)[key];
    if (typeof v === 'number' && Number.isFinite(v)) return String(v);
  }

  return null;
}

const trimSlash = (s: string) => s.replace(/\/+$/, '');

/**
 * API base. Honours VITE_API_BASE_URL, otherwise targets the CURRENT hostname
 * on port 8000 — never a hardcoded 127.0.0.1, which would point at the
 * viewer's own machine when the dashboard is opened from anywhere else.
 */
export function resolveApiBase(): string {
  const env = (import.meta as any)?.env?.VITE_API_BASE_URL;
  if (env !== undefined && env !== null) return trimSlash(String(env));
  const host = typeof window !== 'undefined' ? window.location.hostname : 'localhost';
  return `http://${host}:8000`;
}

/** Filename used by the 'static' route. */
function staticFilename(record: any, filingId: string): string {
  const raw = typeof record?.file_url === 'string' ? record.file_url.trim() : '';
  if (!raw) return `${filingId}_document.pdf`;
  const filename = raw.split('/').pop() || '';
  if (!filename) return `${filingId}_document.pdf`;
  return filename.startsWith(filingId) ? filename : `${filingId}_${filename}`;
}

/**
 * Build the document URL, or null when there is nothing safe to request.
 * An absolute file_url supplied by the API always wins.
 */
export function resolveDocumentUrl(
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

/** Backwards-compatible alias — other files may still import this name. */
export const resolvePdfUrl = (record: SubmissionRecord): string | null =>
  resolveDocumentUrl(record);

/* ══════════════════════════════════════════════════════════════════════════ */

export const Overview: React.FC = () => {
  const [submissions, setSubmissions] = useState<SubmissionRecord[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [submittingAudit, setSubmittingAudit] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [statusFilter, setStatusFilter] = useState<string>('ALL');
  const [selectedAuditRecord, setSelectedAuditRecord] = useState<SubmissionRecord | null>(null);

  const fetchSubmissions = useCallback(async () => {
    try {
      setLoading(true);
      const resData = await getSubmissions();
      const finalArray = Array.isArray(resData) ? resData : resData?.data || [];
      setSubmissions(finalArray);
      setError(null);
    } catch (err) {
      console.error('Error loading economist submissions:', err);
      setError('Failed to load statutory filings from backend.');
    } finally {
      setLoading(false);
    }
  }, []);

  const handleClearAll = async () => {
    if (window.confirm('Are you sure you want to clear all test records?')) {
      try {
        await clearSubmissions();
        await fetchSubmissions();
      } catch (err) {
        console.error('Failed to clear submissions:', err);
      }
    }
  };

  const handleSaveAudit = async (auditForm: {
    companyName: string;
    tinNumber: string;
    bpm6Category: string;
    economistNotes: string;
    auditStatus: string;
    // NOTE: was typed LineItem[], but AuditReview sends shareholders or
    // industrial classifications, which have different fields entirely.
    // The value is forwarded unchanged, exactly as before. Either map it to
    // { description, amount_tzs, amount_usd } on the way out, or change the
    // API contract to match reality — but do it deliberately.
    lineItems: any[];
    // ADDED. The full edited questionnaire from the review screen. Forwarded as
    // extracted_payload_override so the reviewer's edits are not discarded.
    payload: Record<string, any>;
  }) => {
    if (!selectedAuditRecord) return;

    try {
      setSubmittingAudit(true);
      const targetId = resolveFilingId(selectedAuditRecord);

      if (!targetId) {
        alert('Missing filing ID for submission.');
        return;
      }

      await commitAuditDecision(targetId, {
        company_name: auditForm.companyName,
        tin_number: auditForm.tinNumber,
        bpm6_category: auditForm.bpm6Category,
        economist_notes: auditForm.economistNotes,
        status: auditForm.auditStatus,
        line_items: auditForm.lineItems,
        // ADDED. Without this the backend updated the six audit columns and left
        // extracted_payload untouched, so every edit made in the review screen
        // was lost on Approve. The backend re-validates it before storing.
        extracted_payload_override: auditForm.payload,
      });

      setSelectedAuditRecord(null);
      await fetchSubmissions();
    } catch (err) {
      console.error('Failed to commit audit decision:', err);
      alert('Error persisting audit decision to backend server.');
    } finally {
      setSubmittingAudit(false);
    }
  };

  useEffect(() => {
    fetchSubmissions();
  }, [fetchSubmissions]);

  const filteredSubmissions = useMemo(() => {
    return submissions.filter((item) => {
      const company = item.investor_entity || item.company_name || '';
      const filingId = resolveFilingId(item) || '';
      const category = item.bpm6_category || '';

      const query = searchQuery.toLowerCase();
      const matchesSearch =
        company.toLowerCase().includes(query) ||
        filingId.toLowerCase().includes(query) ||
        category.toLowerCase().includes(query);

      const matchesStatus = statusFilter === 'ALL' || item.status === statusFilter;

      return matchesSearch && matchesStatus;
    });
  }, [submissions, searchQuery, statusFilter]);

  return (
    <div className="flex flex-col h-full space-y-4 overflow-hidden min-h-0">
      {/* Header Banner */}
      <div className="shrink-0 flex flex-col sm:flex-row sm:items-center justify-between gap-3 bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
        <div>
          <h1 className="text-xl font-bold text-slate-900 leading-tight">
            Economist Workspace
          </h1>
          <p className="text-slate-500 text-xs mt-0.5">
            Monitor incoming statutory capital flow filings, review VLM confidence scores, and audit risk anomalies.
          </p>
        </div>
        <div className="flex gap-2 shrink-0">
          <button
            onClick={handleClearAll}
            className="px-3 py-1.5 bg-rose-50 text-rose-700 border border-rose-200 rounded-lg hover:bg-rose-100 transition-colors text-xs font-medium"
          >
            Clear Test Data
          </button>
          <button
            onClick={fetchSubmissions}
            className="px-3 py-1.5 bg-emerald-700 text-white rounded-lg hover:bg-emerald-800 transition-colors text-xs font-medium"
          >
            Refresh Data
          </button>
        </div>
      </div>

      {/* Controls Bar */}
      <div className="shrink-0 flex flex-wrap gap-3 justify-between items-center bg-white p-3 rounded-xl border border-slate-200 shadow-sm">
        <div className="flex-1 min-w-[240px]">
          <input
            type="text"
            placeholder="Search by company name, filing ID, or category..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full px-3 py-1.5 border border-slate-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500 text-xs"
          />
        </div>

        <div className="flex items-center gap-2">
          <label className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">
            Status:
          </label>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="px-2.5 py-1.5 border border-slate-300 rounded-lg text-xs bg-white focus:outline-none focus:ring-2 focus:ring-emerald-500"
          >
            <option value="ALL">All Submissions</option>
            <option value="PROCESSED_BY_AI">Processed by AI</option>
            {/* The rules engine also emits PROCESSED_WITH_ALERTS — currently only
                for filings above the high-value threshold. Without this option
                those records appeared under "All Submissions" and nowhere else,
                so an economist filtering for work to review never saw them. */}
            <option value="PROCESSED_WITH_ALERTS">Processed with Alerts</option>
            <option value="APPROVED">Approved</option>
            {/* Reject sets this status in AuditReview, so it needs to be filterable */}
            <option value="REJECTED">Rejected</option>
            <option value="FLAGGED">Flagged for Audit</option>
          </select>
        </div>
      </div>

      {/* Table Container */}
      <div className="flex-1 bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden flex flex-col min-h-0">
        {loading ? (
          <div className="p-8 text-center text-slate-500 text-xs">Fetching latest statutory filings...</div>
        ) : error ? (
          <div className="p-8 text-center text-rose-600 text-xs font-medium">{error}</div>
        ) : filteredSubmissions.length === 0 ? (
          <div className="p-8 text-center text-slate-500 text-xs">No submissions found.</div>
        ) : (
          <div className="flex-1 overflow-auto">
            <table className="w-full text-left border-collapse text-xs">
              <thead className="sticky top-0 bg-slate-900 text-white text-[11px] font-semibold uppercase tracking-wider z-10 shadow-sm">
                <tr>
                  <th className="px-3 py-2.5 whitespace-nowrap">Filing ID</th>
                  <th className="px-3 py-2.5">Investor Entity</th>
                  <th className="px-3 py-2.5 whitespace-nowrap">Document Type</th>
                  <th className="px-3 py-2.5">BPM6 Category</th>
                  <th className="px-3 py-2.5 text-right whitespace-nowrap">Total USD</th>
                  <th className="px-3 py-2.5 text-right whitespace-nowrap">Total TZS</th>
                  <th className="px-3 py-2.5 text-center whitespace-nowrap">VLM Score</th>
                  <th className="px-3 py-2.5 text-center whitespace-nowrap">Status</th>
                  <th className="px-3 py-2.5 text-center whitespace-nowrap">Raw Doc</th>
                  <th className="px-3 py-2.5 text-center whitespace-nowrap">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 text-xs">
                {filteredSubmissions.map((record, index) => {
                  const realFilingId = resolveFilingId(record);
                  const displayId = realFilingId || `record_${index}`;
                  const pdfUrl = resolvePdfUrl(record);

                  const resolvedTotalUsd = record.total_usd ?? record.extracted_payload?.total_usd ?? record.total_assets_usd ?? 0;
                  const resolvedTotalTzs = record.total_tzs ?? record.extracted_payload?.total_tzs ?? record.total_assets_tzs ?? 0;

                  return (
                    <tr key={displayId} className="hover:bg-slate-50/80 transition-colors">
                      <td className="px-3 py-2.5 font-mono font-medium text-emerald-800 whitespace-nowrap">
                        {displayId}
                      </td>
                      <td className="px-3 py-2.5 max-w-xs">
                        <div className="font-semibold text-slate-900 truncate">
                          {record.investor_entity || record.company_name || 'N/A'}
                        </div>
                        <div className="text-[10px] font-normal text-slate-500">
                          TIN: {record.tin_number || 'N/A'}
                        </div>
                      </td>
                      <td className="px-3 py-2.5 text-slate-600 whitespace-nowrap">
                        {record.document_type || 'Statutory Return'}
                      </td>
                      <td className="px-3 py-2.5 max-w-xs font-medium text-slate-700 leading-snug">
                        {record.bpm6_category || 'Unclassified'}
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono font-medium text-slate-900 whitespace-nowrap">
                        ${resolvedTotalUsd.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-slate-600 whitespace-nowrap">
                        {resolvedTotalTzs.toLocaleString()} TZS
                      </td>

                      {/* VLM SCORE — was `{record.vlm_score || '98.5%'}`, which
                          displayed a hardcoded 98.5% on every filing whose score
                          was missing (including ones that failed extraction).
                          That manufactured false assurance, so an absent score
                          now says so. */}
                      <td className="px-3 py-2.5 text-center whitespace-nowrap">
                        {record.vlm_score ? (
                          <span className="inline-block px-1.5 py-0.5 bg-emerald-50 text-emerald-700 border border-emerald-200 rounded font-semibold text-[11px]">
                            {record.vlm_score}
                          </span>
                        ) : record.confidence_score ? (
                          <span className="inline-block px-1.5 py-0.5 bg-emerald-50 text-emerald-700 border border-emerald-200 rounded font-semibold text-[11px]">
                            {(record.confidence_score * 100).toFixed(1)}%
                          </span>
                        ) : (
                          <span className="inline-block px-1.5 py-0.5 bg-slate-100 text-slate-500 border border-slate-300 rounded font-semibold text-[11px]">
                            not scored
                          </span>
                        )}
                      </td>

                      <td className="px-3 py-2.5 text-center whitespace-nowrap">
                        <span
                          className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-bold ${
                            record.status === 'APPROVED'
                              ? 'bg-emerald-100 text-emerald-800'
                              : record.status === 'REJECTED'
                              ? 'bg-slate-200 text-slate-700'
                              : record.status === 'FLAGGED'
                              ? 'bg-rose-100 text-rose-800'
                              : 'bg-amber-100 text-amber-800'
                          }`}
                        >
                          {record.status}
                        </span>
                      </td>

                      {/* RAW DOC — only links when a real identifier exists,
                          otherwise a real request would go to .../undefined. */}
                      <td className="px-3 py-2.5 text-center whitespace-nowrap">
                        {pdfUrl ? (
                          <a
                            href={pdfUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="px-2 py-1 bg-emerald-50 text-emerald-700 border border-emerald-200 rounded text-[11px] font-semibold hover:bg-emerald-100 transition-colors inline-flex items-center gap-1"
                          >
                            📄 View PDF
                          </a>
                        ) : (
                          <span
                            title="This record has no filing_id / submission_id / id, so no document URL can be built."
                            className="px-2 py-1 bg-slate-100 text-slate-400 border border-slate-200 rounded text-[11px] font-semibold cursor-not-allowed inline-flex items-center gap-1"
                          >
                            📄 No doc
                          </span>
                        )}
                      </td>

                      <td className="px-3 py-2.5 text-center whitespace-nowrap">
                        <button
                          onClick={() => setSelectedAuditRecord(record)}
                          className="px-2.5 py-1 bg-slate-900 text-white rounded text-[11px] font-medium hover:bg-slate-800 transition-colors"
                        >
                          Audit
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Modal Audit Workbench */}
      {selectedAuditRecord && (
        <AuditReview
          record={selectedAuditRecord}
          isSubmitting={submittingAudit}
          onClose={() => setSelectedAuditRecord(null)}
          onSave={handleSaveAudit}
        />
      )}
    </div>
  );
};

export default Overview;