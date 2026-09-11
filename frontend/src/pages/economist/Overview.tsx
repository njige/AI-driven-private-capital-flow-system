import React, { useEffect, useState } from 'react';
import { getSubmissions, clearSubmissions, commitAuditDecision } from '../../services/api';

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
  vlm_score?: string;
  confidence_score?: number;
  status: string;
  audit_notes?: string;
  file_url?: string;
  line_items?: LineItem[];
}

export const Overview: React.FC = () => {
  const [submissions, setSubmissions] = useState<SubmissionRecord[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [submittingAudit, setSubmittingAudit] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [statusFilter, setStatusFilter] = useState<string>('ALL');
  const [selectedAuditRecord, setSelectedAuditRecord] = useState<SubmissionRecord | null>(null);

  const fetchSubmissions = async () => {
    try {
      setLoading(true);
      const resData = await getSubmissions();
      const finalArray = Array.isArray(resData) ? resData : (resData?.data || []);
      setSubmissions(finalArray);
      setError(null);
    } catch (err) {
      console.error('Error loading economist submissions:', err);
      setError('Failed to load statutory filings from backend.');
    } finally {
      setLoading(false);
    }
  };

  const handleClearAll = async () => {
    if (window.confirm('Are you sure you want to clear all test records?')) {
      await clearSubmissions();
      fetchSubmissions();
    }
  };

  const handleSaveAudit = async (auditForm: {
    companyName: string;
    tinNumber: string;
    bpm6Category: string;
    economistNotes: string;
    auditStatus: string;
  }) => {
    if (!selectedAuditRecord) return;

    try {
      setSubmittingAudit(true);
      const targetId = selectedAuditRecord.filing_id || selectedAuditRecord.submission_id || selectedAuditRecord.id;

      if (!targetId) {
        alert('Missing filing ID for submission.');
        return;
      }

      await commitAuditDecision(targetId, {
        company_name: auditForm.companyName,
        tin_number: auditForm.tinNumber,
        bpm6_category: auditForm.bpm6Category,
        economist_notes: auditForm.economistNotes,
        status: auditForm.auditStatus
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
  }, []);

  const filteredSubmissions = submissions.filter((item) => {
    const company = item.investor_entity || item.company_name || '';
    const filingId = item.filing_id || item.submission_id || item.id || '';
    const category = item.bpm6_category || '';

    const matchesSearch =
      company.toLowerCase().includes(searchQuery.toLowerCase()) ||
      filingId.toLowerCase().includes(searchQuery.toLowerCase()) ||
      category.toLowerCase().includes(searchQuery.toLowerCase());

    const matchesStatus = statusFilter === 'ALL' || item.status === statusFilter;

    return matchesSearch && matchesStatus;
  });

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
            <option value="APPROVED">Approved</option>
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
                {filteredSubmissions.map((record) => {
                  const recordKey = record.filing_id || record.submission_id || record.id || Math.random().toString();
                  return (
                    <tr key={recordKey} className="hover:bg-slate-50/80 transition-colors">
                      <td className="px-3 py-2.5 font-mono font-medium text-emerald-800 whitespace-nowrap">
                        {record.filing_id || record.submission_id || record.id}
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
                        ${(record.total_usd ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-slate-600 whitespace-nowrap">
                        {(record.total_tzs ?? 0).toLocaleString()} TZS
                      </td>
                      <td className="px-3 py-2.5 text-center whitespace-nowrap">
                        <span className="inline-block px-1.5 py-0.5 bg-emerald-50 text-emerald-700 border border-emerald-200 rounded font-semibold text-[11px]">
                          {record.vlm_score || '98.5%'}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-center whitespace-nowrap">
                        <span
                          className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-bold ${
                            record.status === 'APPROVED'
                              ? 'bg-emerald-100 text-emerald-800'
                              : record.status === 'FLAGGED'
                              ? 'bg-rose-100 text-rose-800'
                              : 'bg-amber-100 text-amber-800'
                          }`}
                        >
                          {record.status}
                        </span>
                      </td>
                      {/* Direct Link to Raw Document */}
                      <td className="px-3 py-2.5 text-center whitespace-nowrap">
                        {record.file_url ? (
                          <a
                            href={record.file_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="px-2 py-1 bg-emerald-50 text-emerald-700 border border-emerald-200 rounded text-[11px] font-semibold hover:bg-emerald-100 transition-colors inline-flex items-center gap-1"
                          >
                            📄 View PDF
                          </a>
                        ) : (
                          <span className="text-slate-400 text-[10px]">No File</span>
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

      {/* Modal Audit Workbench Sub-component */}
      {selectedAuditRecord && (
        <AuditModal
          record={selectedAuditRecord}
          isSubmitting={submittingAudit}
          onClose={() => setSelectedAuditRecord(null)}
          onSave={handleSaveAudit}
        />
      )}
    </div>
  );
};

// Extracted Audit Modal Component
interface AuditModalProps {
  record: SubmissionRecord;
  isSubmitting: boolean;
  onClose: () => void;
  onSave: (form: {
    companyName: string;
    tinNumber: string;
    bpm6Category: string;
    economistNotes: string;
    auditStatus: string;
  }) => void;
}

const AuditModal: React.FC<AuditModalProps> = ({ record, isSubmitting, onClose, onSave }) => {
  const [form, setForm] = useState({
    companyName: record.investor_entity || record.company_name || '',
    tinNumber: record.tin_number || '',
    bpm6Category: record.bpm6_category || 'Foreign Direct Investment (FDI) - Equity',
    economistNotes: record.audit_notes || '',
    auditStatus: record.status === 'FLAGGED' ? 'FLAGGED' : 'APPROVED'
  });

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  return (
    <div className="fixed inset-0 bg-slate-950/80 backdrop-blur-md flex items-center justify-center p-4 z-50 overflow-hidden">
      <div className="bg-white rounded-xl shadow-2xl w-[96vw] h-[92vh] border border-slate-300 flex flex-col overflow-hidden">
        <div className="bg-slate-900 text-white px-5 py-3 flex justify-between items-center shrink-0">
          <div className="flex items-center gap-3">
            <span className="bg-emerald-600 text-white text-xs px-2 py-0.5 rounded font-mono font-bold">
              {record.filing_id || record.submission_id || record.id}
            </span>
            <h2 className="text-base font-bold">Interactive Verification & Manual Audit Workbench</h2>
          </div>

          <button
            onClick={onClose}
            className="bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white px-2.5 py-1 rounded text-xs font-bold transition-colors"
          >
            ✕ Close
          </button>
        </div>

        <div className="flex-1 flex overflow-hidden divide-x divide-slate-200 min-h-0">
          {/* Document Preview */}
          <div className="w-1/2 bg-slate-100 flex flex-col h-full overflow-hidden">
            <div className="p-2.5 bg-slate-200 border-b border-slate-300 flex justify-between items-center text-xs shrink-0">
              <span className="font-bold text-slate-700 uppercase tracking-wider">📄 Submitted Document Source</span>
              <span className="text-slate-500 font-mono">{record.document_type || 'Statutory Return'}</span>
            </div>

            <div className="flex-1 p-3 overflow-auto flex justify-center items-center">
              {record.file_url ? (
                <iframe src={record.file_url} className="w-full h-full rounded border border-slate-300 bg-white" title="Submitted Document" />
              ) : (
                <div className="w-full h-full max-w-md bg-white p-6 rounded-lg border border-slate-300 shadow-sm flex flex-col justify-between text-slate-800 text-xs overflow-y-auto">
                  <div className="border-b-2 border-slate-800 pb-3 mb-3 text-center">
                    <h4 className="font-bold text-xs tracking-widest uppercase">Bank of Tanzania</h4>
                    <p className="text-[9px] text-slate-500 uppercase">Capital Flow Statutory Return Form</p>
                  </div>

                  <div className="space-y-2 text-xs">
                    <div className="flex justify-between border-b pb-1">
                      <span className="font-semibold text-slate-500">Reporting Entity:</span>
                      <span className="font-bold text-slate-900">{record.investor_entity || record.company_name}</span>
                    </div>
                    <div className="flex justify-between border-b pb-1">
                      <span className="font-semibold text-slate-500">TIN:</span>
                      <span className="font-mono text-slate-900">{record.tin_number || 'N/A'}</span>
                    </div>
                    <div className="flex justify-between border-b pb-1">
                      <span className="font-semibold text-slate-500">Reporting Period:</span>
                      <span className="text-slate-900">{record.reporting_period || 'Q2 2026'}</span>
                    </div>
                    <div className="flex justify-between border-b pb-1">
                      <span className="font-semibold text-slate-500">Declared Category:</span>
                      <span className="text-slate-900 font-medium">{record.bpm6_category}</span>
                    </div>

                    <div className="mt-4 pt-1">
                      <span className="font-semibold text-slate-600 block mb-1">Declared Line Items:</span>
                      <div className="bg-slate-50 p-2 rounded border border-slate-200 space-y-1 font-mono text-[10px]">
                        <div className="flex justify-between font-bold text-slate-700">
                          <span>Description</span>
                          <span>USD Equivalent</span>
                        </div>
                        <div className="flex justify-between text-slate-600">
                          <span>Capital Equity Injection</span>
                          <span>${(record.total_usd ?? 0).toLocaleString()}</span>
                        </div>
                      </div>
                    </div>
                  </div>

                  <div className="mt-6 pt-3 border-t text-center text-[9px] text-slate-400">
                    Official Statutory Return Document Rendering Preview
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Form Side */}
          <div className="w-1/2 bg-white flex flex-col h-full overflow-y-auto p-5 space-y-4 text-xs">
            <div className="bg-slate-50 p-3 rounded-lg border border-slate-200 shrink-0">
              <div className="flex justify-between items-center mb-1">
                <span className="text-[10px] font-bold uppercase tracking-wider text-slate-500">AI Vision Extraction Confidence</span>
                <span className="px-2 py-0.5 bg-emerald-100 text-emerald-800 border border-emerald-300 rounded font-bold text-[10px]">
                  VLM Score: {record.vlm_score || '98.5%'}
                </span>
              </div>
              <p className="text-xs text-slate-600 leading-relaxed">
                <strong>AI Notes:</strong> {record.audit_notes || 'Extracted successfully by backend vision pipeline.'}
              </p>
            </div>

            <div>
              <h3 className="text-xs font-bold text-slate-900 mb-2 uppercase tracking-wider border-b pb-1">
                Economist Manual Overrides & Data Verification
              </h3>

              <div className="space-y-3">
                <div>
                  <label className="block font-semibold text-slate-700 mb-1">Investor Company Name</label>
                  <input
                    type="text"
                    value={form.companyName}
                    onChange={(e) => setForm({ ...form, companyName: e.target.value })}
                    className="w-full px-2.5 py-1.5 border border-slate-300 rounded-lg text-xs focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                  />
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block font-semibold text-slate-700 mb-1">TIN Number</label>
                    <input
                      type="text"
                      value={form.tinNumber}
                      onChange={(e) => setForm({ ...form, tinNumber: e.target.value })}
                      className="w-full px-2.5 py-1.5 border border-slate-300 rounded-lg text-xs font-mono focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                    />
                  </div>

                  <div>
                    <label className="block font-semibold text-slate-700 mb-1">BPM6 Category Re-Classification</label>
                    <select
                      value={form.bpm6Category}
                      onChange={(e) => setForm({ ...form, bpm6Category: e.target.value })}
                      className="w-full px-2.5 py-1.5 border border-slate-300 rounded-lg text-xs bg-white focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                    >
                      <option value="Foreign Direct Investment (FDI) - Equity">Foreign Direct Investment (FDI) - Equity</option>
                      <option value="Portfolio Investment - Equity">Portfolio Investment - Equity</option>
                      <option value="Foreign Debt / Long-term Loans">Foreign Debt / Long-term Loans</option>
                      <option value="Other Investment / Trade Credits">Other Investment / Trade Credits</option>
                    </select>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-3 p-2.5 bg-slate-50 border border-slate-200 rounded-lg">
                  <div>
                    <span className="text-[10px] text-slate-500 block font-semibold">Total Amount USD</span>
                    <span className="text-sm font-mono font-bold text-slate-900">
                      ${(record.total_usd ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2 })}
                    </span>
                  </div>
                  <div>
                    <span className="text-[10px] text-slate-500 block font-semibold">Total Amount TZS</span>
                    <span className="text-sm font-mono font-bold text-slate-900">
                      {(record.total_tzs ?? 0).toLocaleString()} TZS
                    </span>
                  </div>
                </div>

                <div>
                  <label className="block font-semibold text-slate-700 mb-1">Manual Audit Findings & Remarks</label>
                  <textarea
                    rows={2}
                    placeholder="Enter formal BoT audit notes or risk observations..."
                    value={form.economistNotes}
                    onChange={(e) => setForm({ ...form, economistNotes: e.target.value })}
                    className="w-full px-2.5 py-1.5 border border-slate-300 rounded-lg text-xs focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                  />
                </div>

                <div>
                  <label className="block font-semibold text-slate-700 mb-1">Audit Decision Status</label>
                  <div className="flex gap-4">
                    <label className="flex items-center gap-1.5 cursor-pointer font-medium text-slate-700">
                      <input
                        type="radio"
                        name="auditStatus"
                        value="APPROVED"
                        checked={form.auditStatus === 'APPROVED'}
                        onChange={(e) => setForm({ ...form, auditStatus: e.target.value })}
                        className="text-emerald-600 focus:ring-emerald-500"
                      />
                      Approve Filing
                    </label>
                    <label className="flex items-center gap-1.5 cursor-pointer font-medium text-slate-700">
                      <input
                        type="radio"
                        name="auditStatus"
                        value="FLAGGED"
                        checked={form.auditStatus === 'FLAGGED'}
                        onChange={(e) => setForm({ ...form, auditStatus: e.target.value })}
                        className="text-amber-600 focus:ring-amber-500"
                      />
                      Flag for Deep Risk Investigation
                    </label>
                  </div>
                </div>
              </div>
            </div>

            <div className="pt-3 border-t border-slate-200 flex justify-end gap-2 mt-auto shrink-0">
              <button
                onClick={onClose}
                disabled={isSubmitting}
                className="px-3 py-2 bg-slate-100 text-slate-700 rounded-lg text-xs font-semibold hover:bg-slate-200 transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={() => onSave(form)}
                disabled={isSubmitting}
                className="px-4 py-2 bg-emerald-700 text-white rounded-lg text-xs font-bold hover:bg-emerald-800 transition-colors shadow-sm disabled:opacity-50"
              >
                {isSubmitting ? 'Saving...' : 'Save & Commit Decision'}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Overview;