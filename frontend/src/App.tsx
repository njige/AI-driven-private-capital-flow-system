import React from 'react';
import { BrowserRouter as Router, Routes, Route, Link, useLocation } from 'react-router-dom';
import { ShieldCheck } from 'lucide-react';
import SubmitPCF from './pages/investor/SubmitPCF';
import EconomistDashboard from './pages/economist/EconomistDashboard';

// Navigation Bar Component with Role-Specific Headers
function Header() {
  const location = useLocation();
  const isEconomist = location.pathname.startsWith('/economist');

  return (
    <header className="bg-slate-900 text-white px-6 py-3 flex justify-between items-center shadow-lg border-b border-slate-800 shrink-0">
      <div className="flex items-center space-x-3">
        <ShieldCheck className="h-7 w-7 text-emerald-400" />
        <div>
          <h1 className="font-bold text-lg leading-tight">Bank of Tanzania</h1>
          <p className="text-xs text-slate-400">
            {isEconomist ? 'DERP Economist Workspace' : 'Private Capital Flow (PCF) Investor Portal'}
          </p>
        </div>
      </div>

      {/* Role-Specific Right Menu */}
      <nav className="flex space-x-4 text-sm font-medium">
        {isEconomist ? (
          <div className="flex items-center space-x-4">
            <Link to="/economist" className="text-slate-300 hover:text-white transition">
              Overview Dashboard
            </Link>
            <span className="bg-emerald-600/20 text-emerald-400 text-xs px-3 py-1 rounded-full border border-emerald-500/30">
              BoT Internal Staff
            </span>
          </div>
        ) : (
          <span className="bg-sky-600/20 text-sky-400 text-xs px-3 py-1 rounded-full border border-sky-500/30">
            External Investor Mode
          </span>
        )}
      </nav>
    </header>
  );
}

export default function App() {
  return (
    <Router>
      {/* LOCKED VIEWPORT CONTAINER */}
      <div className="h-screen w-screen overflow-hidden bg-slate-100 text-slate-800 flex flex-col font-sans">
        <Header />

        {/* MAIN CONTAINER PREVENTING GLOBAL OVERFLOW */}
        <main className="flex-1 overflow-hidden min-h-0 w-full">
          <Routes>
            {/* Investor Portal Route */}
            <Route path="/investor" element={<SubmitPCF />} />

            {/* Economist Workspace Route (Managed via EconomistDashboard side panel) */}
            <Route path="/economist/*" element={<EconomistDashboard />} />

            {/* Default fallback route to Investor Portal */}
            <Route path="*" element={<SubmitPCF />} />
          </Routes>
        </main>
      </div>
    </Router>
  );
}