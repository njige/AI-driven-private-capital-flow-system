import React, { useState } from 'react';
import { loginInvestor } from '../../services/api';
import botLogo from '../../assets/bot-logo.png';

interface LoginProps {
  onLoginSuccess: (token: string, companyName: string) => void;
}

export const Login: React.FC<LoginProps> = ({ onLoginSuccess }) => {
  const [tinNumber, setTinNumber] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!tinNumber || !password) return;

    setLoading(true);
    setError(null);

    try {
      const data = await loginInvestor({
        tin_number: tinNumber.trim(),
        password: password,
      });

      onLoginSuccess(data.access_token, data.company_name);
    } catch (err: any) {
      console.error('Login Error:', err);
      const msg = err.response?.data?.detail || 'Invalid TRA TIN Number or Passcode.';
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 flex items-center justify-center py-12 px-4 sm:px-6 lg:px-8">
      <div className="max-w-md w-full bg-white rounded-xl shadow-lg border border-slate-200 overflow-hidden">
        
        {/* Header & Logo */}
        <div className="p-6 text-center border-b border-slate-100 bg-white">
          <img 
            src={botLogo} 
            alt="Bank of Tanzania Seal" 
            className="h-28 w-auto mx-auto mb-3 object-contain drop-shadow-md"
          />
          <h2 className="text-xl font-bold text-slate-900 tracking-tight">
            Investor Portal Authentication
          </h2>
          <p className="mt-1 text-xs text-slate-500">
            Bank of Tanzania • Private Capital Flow System
          </p>
        </div>

        {/* Login Form */}
        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          <div>
            <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1">
              TRA TIN Number
            </label>
            <input
              type="text"
              required
              placeholder="e.g. 118-492-705"
              value={tinNumber}
              onChange={(e) => setTinNumber(e.target.value)}
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wider mb-1">
              Portal Passcode
            </label>
            <input
              type="password"
              required
              placeholder="••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
            />
          </div>

          {error && (
            <div className="p-3 bg-red-50 border-l-4 border-red-500 rounded-r-md flex items-start space-x-2">
              <span className="text-red-500 text-sm">⚠️</span>
              <p className="text-xs text-red-700 font-medium">{error}</p>
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full py-2.5 bg-emerald-600 hover:bg-emerald-700 text-white font-medium text-xs rounded-lg shadow-md transition-all flex items-center justify-center space-x-2 disabled:opacity-50"
          >
            {loading ? (
              <>
                <svg className="animate-spin h-4 w-4 text-white" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                <span>Verifying Credentials...</span>
              </>
            ) : (
              <span>Sign In to Submit Returns</span>
            )}
          </button>
        </form>

      </div>
    </div>
  );
};

export default Login;