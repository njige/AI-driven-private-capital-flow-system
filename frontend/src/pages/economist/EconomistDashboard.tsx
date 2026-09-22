import React, { useState } from 'react';
import { FileText, BarChart3, FolderArchive, ExternalLink } from 'lucide-react';
import { Outlet, useParams } from 'react-router-dom';
import Overview from './Overview';

type TabId = 'submissions' | 'superset' | 'documents';

/* Once a tab has been opened its panel stays in the DOM and is only hidden.
 *
 * It used to be unmounted on every switch, which meant the Superset frame was
 * re-created from scratch each time you came back to it — a full reload of the
 * Superset SPA, back to whatever page Superset opens on, losing the dashboard
 * you had navigated into. The submissions list also re-fetched all 500 rows and
 * dropped its filters. Hiding instead of unmounting keeps both. First open is
 * still lazy: Superset is not loaded until you click its tab. */
const PANE = 'h-full min-h-0 flex flex-col';
const PANE_HIDDEN = 'hidden';

/* Same pattern as src/services/api.ts: an explicit override wins, otherwise fall
 * back to the host the portal is being viewed from.
 *
 * Set VITE_SUPERSET_URL / VITE_MINIO_URL in .env whenever those two services do
 * not run on the same machine as the browser. The fallback hardcodes both the
 * protocol and the port, so a portal served over HTTPS would have both frames
 * refused as mixed content — the same trap the API client already documents. */
const HOST_IP = typeof window !== 'undefined' ? window.location.hostname : 'localhost';
const readEnv = (name: string): string | undefined => {
  const value = (import.meta as any)?.env?.[name];
  return typeof value === 'string' && value.trim() !== '' ? value.trim() : undefined;
};
const SUPERSET_URL = readEnv('VITE_SUPERSET_URL') || `http://${HOST_IP}:8088/superset/welcome/`;
const MINIO_URL = readEnv('VITE_MINIO_URL') || `http://${HOST_IP}:9001`;

export const EconomistDashboard: React.FC = () => {
  const [activeTab, setActiveTab] = useState<TabId>('submissions');
  const { id } = useParams<{ id?: string }>(); // set only if an audit sub-route (/economist/:id) is open

  const [mounted, setMounted] = useState<Record<TabId, boolean>>({
    submissions: true,
    superset: false,
    documents: false,
  });

  const openTab = (tab: TabId) => {
    setMounted((prev) => (prev[tab] ? prev : { ...prev, [tab]: true }));
    setActiveTab(tab);
  };

  return (
    /* h-full, NOT h-screen.
     *
     * App.tsx keeps a permanent header above this component and gives the rest
     * of the viewport to <main className="flex-1 min-h-0 overflow-hidden">. So
     * the space available here is 100vh MINUS that header (~68px), while
     * `h-screen` asks for the full 100vh — and `main` clips what does not fit.
     * The bottom ~68px of the sidebar, of the submissions list and of the
     * Superset frame sat outside the visible area. h-full takes exactly the
     * height App.tsx hands over. */
    <div className="flex h-full w-full bg-slate-900 text-white overflow-hidden">
      {/* SIDEBAR NAVIGATION */}
      <aside className="w-60 bg-slate-900 border-r border-slate-800 flex flex-col p-4 shrink-0">
        <div className="mb-6 px-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
            Economist Portal
          </h2>
        </div>

        <nav className="space-y-2 flex-1" aria-label="Economist portal sections">
          <button
            type="button"
            onClick={() => openTab('submissions')}
            aria-current={activeTab === 'submissions' ? 'page' : undefined}
            className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition ${
              activeTab === 'submissions'
                ? 'bg-emerald-600 text-white shadow'
                : 'text-slate-300 hover:bg-slate-800'
            }`}
          >
            <FileText size={18} />
            <span>Submissions &amp; Audits</span>
          </button>

          <button
            type="button"
            onClick={() => openTab('superset')}
            aria-current={activeTab === 'superset' ? 'page' : undefined}
            className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition ${
              activeTab === 'superset'
                ? 'bg-emerald-600 text-white shadow'
                : 'text-slate-300 hover:bg-slate-800'
            }`}
          >
            <BarChart3 size={18} />
            <span>Superset Analytics</span>
          </button>

          <button
            type="button"
            onClick={() => openTab('documents')}
            aria-current={activeTab === 'documents' ? 'page' : undefined}
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
      {/* Note: Remove p-6 when viewing an active audit detail page to prevent layout clipping */}
      <main className={`flex-1 overflow-hidden bg-slate-100 text-slate-800 flex flex-col min-h-0 ${id ? 'p-0' : 'p-6'}`}>
        {/* SUBMISSIONS & AUDITS
            Overview renders AuditReview itself, as a `fixed inset-0 z-50` overlay, when
            a row is clicked — so this pane holds the list only.
            The `id` branch is for a future /economist/:id route: if you add one, its
            element has to supply record / isSubmitting / onClose / onSave, because
            AuditReview takes the filing as a PROP and never reads the URL. */}
        <div className={activeTab === 'submissions' ? PANE : PANE_HIDDEN}>
          {id ? <Outlet /> : <Overview />}
        </div>

        {mounted.superset && (
          <div className={activeTab === 'superset' ? PANE : PANE_HIDDEN}>
            <div className="h-full flex flex-col space-y-4 min-h-0">
              <div className="shrink-0 bg-white p-4 rounded-xl shadow-sm border border-slate-200 flex justify-between items-center">
                <div>
                  <h2 className="text-xl font-bold text-slate-800">Macroeconomic Analytics</h2>
                  <p className="text-slate-500 text-xs mt-0.5">
                    Real-time cross-border capital flow analytics powered by Apache Superset.
                  </p>
                </div>
                <a
                  href={SUPERSET_URL}
                  target="_blank"
                  rel="noreferrer"
                  className="px-3 py-1.5 bg-emerald-600 text-white rounded-lg text-xs font-medium hover:bg-emerald-700 transition flex items-center gap-1.5"
                >
                  <span>Open Superset Tab</span>
                  <ExternalLink size={14} />
                </a>
              </div>
              {/* If this frame is blank, the browser refused to display it rather than
                  failing to reach it: Superset answers with X-Frame-Options: SAMEORIGIN
                  by default, which blocks being framed from another port. Open the
                  "Open Superset Tab" link — if that works, this is a header problem on
                  the Superset side, not a network problem. */}
              <div className="flex-1 w-full bg-white rounded-xl border border-slate-200 overflow-hidden shadow-sm min-h-0 relative">
                <iframe
                  src={SUPERSET_URL}
                  title="Apache Superset Dashboard"
                  className="w-full h-full border-0"
                />
              </div>
            </div>
          </div>
        )}

        {mounted.documents && (
          <div className={activeTab === 'documents' ? PANE : PANE_HIDDEN}>
            {/* RAW DOCUMENTS STORAGE VIEW */}
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
                  className="px-3 py-1.5 bg-slate-900 text-white rounded-lg text-xs font-medium hover:bg-slate-800 transition flex items-center gap-1.5"
                >
                  <span>Open MinIO Console</span>
                  <ExternalLink size={14} />
                </a>
              </div>
              {/* Same as above: if the frame is blank, check the MinIO console response
                  headers before suspecting the network. */}
              <div className="flex-1 w-full bg-white rounded-xl border border-slate-200 overflow-hidden shadow-sm min-h-0 relative">
                <iframe
                  src={MINIO_URL}
                  title="MinIO Raw Document Storage"
                  className="w-full h-full border-0"
                />
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
};

export default EconomistDashboard;
