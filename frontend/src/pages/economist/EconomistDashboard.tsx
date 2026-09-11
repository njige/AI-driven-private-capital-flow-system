import React, { useState } from 'react';
import { FileText, BarChart3, FolderArchive } from 'lucide-react';
import Overview from './Overview';

export const EconomistDashboard: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'submissions' | 'superset' | 'documents'>('submissions');
  
  const SUPERSET_URL = "http://localhost:8088/superset/welcome/";
  // MinIO Console URL for object storage browsing
  const MINIO_URL = "http://localhost:9001"; 

  return (
    <div className="flex h-screen w-full bg-slate-900 text-white overflow-hidden">
      {/* SIDEBAR NAVIGATION */}
      <aside className="w-60 bg-slate-900 border-r border-slate-800 flex flex-col p-4 shrink-0">
        <div className="mb-6 px-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
            Economist Portal
          </h2>
        </div>

        <nav className="space-y-2 flex-1">
          <button
            onClick={() => setActiveTab('submissions')}
            className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition ${
              activeTab === 'submissions'
                ? 'bg-emerald-600 text-white shadow'
                : 'text-slate-300 hover:bg-slate-800'
            }`}
          >
            <FileText size={18} />
            <span>Submissions & Audits</span>
          </button>

          <button
            onClick={() => setActiveTab('superset')}
            className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition ${
              activeTab === 'superset'
                ? 'bg-emerald-600 text-white shadow'
                : 'text-slate-300 hover:bg-slate-800'
            }`}
          >
            <BarChart3 size={18} />
            <span>Superset Analytics</span>
          </button>

          {/* NEW RAW DOCUMENTS TAB */}
          <button
            onClick={() => setActiveTab('documents')}
            className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition ${
              activeTab === 'documents'
                ? 'bg-emerald-600 text-white shadow'
                : 'text-slate-300 hover:bg-slate-800'
            }`}
          >
            <FolderArchive size={18} />
            <span>Raw Documents</span>
          </button>
        </nav>
      </aside>

      {/* MAIN CONTENT AREA */}
      <main className="flex-1 overflow-auto bg-slate-100 text-slate-800 p-6 flex flex-col min-h-0">
        {activeTab === 'submissions' ? (
          <Overview />
        ) : activeTab === 'superset' ? (
          <div className="h-full flex flex-col space-y-4 min-h-0">
            <div className="shrink-0 bg-white p-4 rounded-xl shadow-sm border border-slate-200">
              <h2 className="text-xl font-bold text-slate-800">Macroeconomic Analytics</h2>
              <p className="text-slate-500 text-xs mt-0.5">
                Real-time cross-border capital flow analytics powered by Apache Superset.
              </p>
            </div>
            <div className="flex-1 w-full bg-white rounded-xl border border-slate-200 overflow-hidden shadow-sm min-h-0">
              <iframe
                src={SUPERSET_URL}
                title="Apache Superset Dashboard"
                className="w-full h-full border-0"
              />
            </div>
          </div>
        ) : (
          /* RAW DOCUMENTS STORAGE VIEW */
          <div className="h-full flex flex-col space-y-4 min-h-0">
            <div className="shrink-0 bg-white p-4 rounded-xl shadow-sm border border-slate-200 flex justify-between items-center">
              <div>
                <h2 className="text-xl font-bold text-slate-800">Raw Submitted Documents Repository</h2>
                <p className="text-slate-500 text-xs mt-0.5">
                  Direct access to uploaded statutory PDFs stored in MinIO object storage.
                </p>
              </div>
              <a
                href={MINIO_URL}
                target="_blank"
                rel="noreferrer"
                className="px-3 py-1.5 bg-slate-900 text-white rounded-lg text-xs font-medium hover:bg-slate-800 transition"
              >
                Open MinIO Console ↗
              </a>
            </div>
            <div className="flex-1 w-full bg-white rounded-xl border border-slate-200 overflow-hidden shadow-sm min-h-0">
              <iframe
                src={MINIO_URL}
                title="MinIO Raw Document Storage"
                className="w-full h-full border-0"
              />
            </div>
          </div>
        )}
      </main>
    </div>
  );
};

export default EconomistDashboard;