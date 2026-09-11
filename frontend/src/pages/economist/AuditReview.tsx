import { useState } from 'react';
import { CheckCircle, XCircle, AlertTriangle, Eye, ArrowLeft, Save } from 'lucide-react';
import { Link } from 'react-router-dom';

export default function AuditReview() {
  // Sample extracted data (This will come from FastAPI/PostgreSQL later)
  const [formData, setFormData] = useState({
    investor_name: 'Kilimanjaro Mining Ltd',
    registration_no: 'TZ-BRELA-98412',
    capital_category: 'Foreign Direct Investment (FDI)',
    amount: 1500000,
    currency: 'USD',
    confidence_score: 0.84, // 84% triggers manual audit queue
    anomaly_flag: true,
    anomaly_reason: 'Mismatch: Reported FX transfer exceeds commercial bank SWIFT log by 12%',
  });

  const [isApproved, setIsApproved] = useState<boolean | null>(null);

  return (
    <div className="h-full flex flex-col space-y-4 min-h-0 p-6 overflow-hidden">
      {/* Top Action Header */}
      <div className="bg-white p-4 rounded-xl shadow-sm border border-slate-200 flex justify-between items-center shrink-0">
        <div className="flex items-center space-x-3">
          <Link to="/economist" className="p-2 hover:bg-slate-100 rounded-lg text-slate-600 transition">
            <ArrowLeft className="h-5 w-5" />
          </Link>
          <div>
            <h2 className="text-lg font-bold text-slate-900">Audit Review: Filing #PCF-2026-0042</h2>
            <p className="text-xs text-slate-500">Submitted by {formData.investor_name}</p>
          </div>
        </div>

        {/* Action Controls */}
        <div className="flex items-center space-x-3">
          <button
            onClick={() => setIsApproved(false)}
            className="px-4 py-2 rounded-lg text-sm font-medium border border-red-300 text-red-700 hover:bg-red-50 flex items-center space-x-2 transition"
          >
            <XCircle className="h-4 w-4" />
            <span>Reject / Request Clarification</span>
          </button>
          <button
            onClick={() => setIsApproved(true)}
            className="px-4 py-2 rounded-lg text-sm font-medium bg-emerald-600 hover:bg-emerald-700 text-white flex items-center space-x-2 shadow-sm transition"
          >
            <CheckCircle className="h-4 w-4" />
            <span>Approve & Sync to BOP</span>
          </button>
        </div>
      </div>

      {/* Anomaly Banner */}
      {formData.anomaly_flag && (
        <div className="bg-amber-50 border border-amber-300 p-4 rounded-xl flex items-start space-x-3 shrink-0">
          <AlertTriangle className="h-5 w-5 text-amber-600 shrink-0 mt-0.5" />
          <div className="text-sm text-amber-900">
            <span className="font-semibold block">Risk Engine Alert (XGBoost):</span>
            {formData.anomaly_reason}
          </div>
        </div>
      )}

      {/* Split-Screen Workspace (Fills Remaining Vertical Space) */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 flex-1 min-h-0 overflow-hidden">
        {/* LEFT COLUMN: Original PDF Viewer */}
        <div className="bg-slate-800 rounded-xl p-4 flex flex-col justify-between border border-slate-700 text-slate-300 min-h-0">
          <div className="flex justify-between items-center border-b border-slate-700 pb-3 mb-3 shrink-0">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-400 flex items-center space-x-2">
              <Eye className="h-4 w-4" />
              <span>Original PDF Document</span>
            </span>
            <span className="text-xs bg-slate-700 px-2.5 py-1 rounded text-slate-300">Audited_Financials_2025.pdf</span>
          </div>

          {/* Embedded PDF View */}
          <div className="flex-1 bg-slate-900 rounded-lg flex items-center justify-center border border-slate-700 text-slate-500 text-sm overflow-hidden">
            [ PDF Document Preview Screen ]
          </div>
        </div>

        {/* RIGHT COLUMN: VLM Extracted Fields & Override Form */}
        <div className="bg-white rounded-xl p-6 shadow-sm border border-slate-200 overflow-y-auto space-y-5 min-h-0">
          <div className="flex justify-between items-center border-b border-slate-100 pb-3">
            <h3 className="font-bold text-slate-900">VLM Parsed Fields (Editable)</h3>
            <span className="text-xs font-semibold px-2.5 py-1 rounded-full bg-amber-100 text-amber-800">
              VLM Confidence: {(formData.confidence_score * 100).toFixed(0)}%
            </span>
          </div>

          <form className="space-y-4 text-sm">
            <div>
              <label className="block font-medium text-slate-700 mb-1">Investor Name</label>
              <input
                type="text"
                value={formData.investor_name}
                onChange={(e) => setFormData({ ...formData, investor_name: e.target.value })}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-emerald-500 focus:outline-none"
              />
            </div>

            <div>
              <label className="block font-medium text-slate-700 mb-1">BPM6 Category</label>
              <select
                value={formData.capital_category}
                onChange={(e) => setFormData({ ...formData, capital_category: e.target.value })}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-emerald-500 focus:outline-none"
              >
                <option>Foreign Direct Investment (FDI)</option>
                <option>Portfolio Investment</option>
                <option>Other Investment (Intercompany Loan)</option>
              </select>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block font-medium text-slate-700 mb-1">Amount</label>
                <input
                  type="number"
                  value={formData.amount}
                  onChange={(e) => setFormData({ ...formData, amount: Number(e.target.value) })}
                  className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-emerald-500 focus:outline-none font-semibold"
                />
              </div>

              <div>
                <label className="block font-medium text-slate-700 mb-1">Currency</label>
                <input
                  type="text"
                  value={formData.currency}
                  onChange={(e) => setFormData({ ...formData, currency: e.target.value })}
                  className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-emerald-500 focus:outline-none"
                />
              </div>
            </div>

            <div>
              <label className="block font-medium text-slate-700 mb-1">Economist Audit Notes</label>
              <textarea
                rows={3}
                placeholder="Enter verification comments or reason for override..."
                className="w-full px-3 py-2 border border-slate-300 rounded-lg focus:ring-2 focus:ring-emerald-500 focus:outline-none text-xs"
              />
            </div>

            <button
              type="button"
              className="w-full py-2.5 bg-slate-900 text-white rounded-lg font-medium text-sm hover:bg-slate-800 transition flex items-center justify-center space-x-2"
            >
              <Save className="h-4 w-4" />
              <span>Save Economist Corrections (Trains Active Learning)</span>
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}