import React, { useState } from 'react';
import Login from './Login';
import { uploadPCFDocument } from '../../services/api';
import botLogo from '../../assets/bot-logo.png';

export const SubmitPCF: React.FC = () => {
  // Auth State
  const [token, setToken] = useState<string>(() => localStorage.getItem('pcf_token') || '');
  const [companyName, setCompanyName] = useState<string>(() => localStorage.getItem('pcf_company') || '');

  // Upload Form State
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isDragging, setIsDragging] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [result, setResult] = useState<any>(null);

  // --- Auth Handlers ---
  const handleLogout = () => {
    localStorage.removeItem('pcf_token');
    localStorage.removeItem('pcf_company');
    localStorage.removeItem('pcf_tin');
    setToken('');
    setCompanyName('');
    setSelectedFile(null);
    setResult(null);
  };

  // --- Drag & Drop File Handlers ---
  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);

    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      validateAndSetFile(e.dataTransfer.files[0]);
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      validateAndSetFile(e.target.files[0]);
    }
  };

  const validateAndSetFile = (file: File) => {
    const validTypes = ['application/pdf', 'image/png', 'image/jpeg', 'image/jpg'];
    if (!validTypes.includes(file.type)) {
      setErrorMsg('Invalid file format. Please upload a PDF, PNG, or JPG document.');
      setSelectedFile(null);
      return;
    }
    setErrorMsg(null);
    setResult(null);
    setSelectedFile(file);
  };

  const handleSubmit = async () => {
    if (!selectedFile) return;

    setLoading(true);
    setErrorMsg(null);

    try {
      const data = await uploadPCFDocument(selectedFile);
      const payload = data.data || data;

      setResult({
        submission_id: payload.filing_id || payload.submission_id || 'SUB-A1001',
        status: payload.status || 'PROCESSED BY AI'
      });
    } catch (err: any) {
      console.error('Submission processing error:', err);
      if (err.response?.status === 401) {
        handleLogout();
        return;
      }
      const msg = err.response?.data?.detail || 'Failed to process and analyze the uploaded document.';
      setErrorMsg(msg);
    } finally {
      setLoading(false);
    }
  };

  // 1. Render Standalone Login View if Unauthenticated
  if (!token) {
    return (
      <Login
        onLoginSuccess={(newToken, company) => {
          setToken(newToken);
          setCompanyName(company);
        }}
      />
    );
  }

  // 2. Render Ingestion View once Authenticated
  return (
    <div className="h-full w-full overflow-y-auto bg-slate-50 p-6 sm:p-8 flex flex-col justify-center items-center">
      <div className="w-full max-w-xl my-auto">
        <div className="bg-white rounded-xl shadow-lg border border-slate-200 overflow-hidden">
          
          {/* Header & Logo */}
          <div className="p-6 text-center border-b border-slate-100 bg-white relative">
            <button
              onClick={handleLogout}
              className="absolute top-4 right-4 text-xs bg-slate-100 hover:bg-slate-200 text-slate-600 px-3 py-1.5 rounded-md font-medium transition-colors"
            >
              Logout 🔒
            </button>

            <img 
              src={botLogo} 
              alt="Bank of Tanzania Seal" 
              className="h-28 w-auto mx-auto mb-2 object-contain drop-shadow-md"
            />
            <h1 className="text-xl font-bold text-slate-900 tracking-tight">
              Submit Private Capital Flow Return
            </h1>
            <p className="mt-1 text-xs text-emerald-700 font-semibold bg-emerald-50 inline-block px-3 py-1 rounded-full border border-emerald-200">
              Authenticated: {companyName}
            </p>
          </div>

          {/* Form Body */}
          <div className="p-6">
            <div
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              className={`relative border-2 border-dashed rounded-lg p-6 text-center transition-all duration-200 ease-in-out ${
                isDragging
                  ? 'border-emerald-500 bg-emerald-50/60 scale-[1.01]'
                  : selectedFile
                  ? 'border-emerald-400 bg-emerald-50/20'
                  : 'border-slate-300 hover:border-emerald-400 bg-slate-50/50'
              }`}
            >
              <input
                type="file"
                id="file-upload"
                onChange={handleFileSelect}
                accept=".pdf,.png,.jpg,.jpeg"
                className="hidden"
              />

              <div className="flex flex-col items-center justify-center space-y-2">
                <div className="w-10 h-10 rounded-full bg-emerald-100 flex items-center justify-center text-emerald-600 shadow-inner">
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                  </svg>
                </div>

                {selectedFile ? (
                  <div>
                    <p className="text-sm font-semibold text-slate-800">{selectedFile.name}</p>
                    <p className="text-[11px] text-slate-500 mt-0.5">
                      {(selectedFile.size / (1024 * 1024)).toFixed(2)} MB • Ready for audit validation
                    </p>
                  </div>
                ) : (
                  <div>
                    <label htmlFor="file-upload" className="cursor-pointer text-sm font-semibold text-emerald-600 hover:text-emerald-700 underline">
                      Click to upload
                    </label>
                    <span className="text-sm text-slate-600"> or drag and drop</span>
                    <p className="text-[11px] text-slate-400 mt-1">
                      Supports Audited Statements, Balance Sheets, Income Statements, and BPM6 Forms
                    </p>
                  </div>
                )}
              </div>
            </div>

            {/* Error Banner */}
            {errorMsg && (
              <div className="mt-3 p-3 bg-red-50 border-l-4 border-red-500 rounded-r-md flex items-start space-x-2">
                <span className="text-red-500 text-base">⚠️</span>
                <p className="text-xs text-red-700 font-medium">{errorMsg}</p>
              </div>
            )}

            {/* Submit Button */}
            <div className="mt-5 flex justify-end">
              <button
                onClick={handleSubmit}
                disabled={!selectedFile || loading}
                className={`w-full sm:w-auto px-5 py-2.5 rounded-lg font-medium text-xs flex items-center justify-center space-x-2 shadow-sm transition-all ${
                  !selectedFile || loading
                    ? 'bg-slate-200 text-slate-400 cursor-not-allowed'
                    : 'bg-emerald-600 hover:bg-emerald-700 text-white shadow-emerald-200 shadow-md'
                }`}
              >
                {loading ? (
                  <>
                    <svg className="animate-spin -ml-1 mr-2 h-3.5 w-3.5 text-white" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                    </svg>
                    <span>Extracting & Validating...</span>
                  </>
                ) : (
                  <>
                    <span>Submit for Audit Verification</span>
                    <span>→</span>
                  </>
                )}
              </button>
            </div>
          </div>

          {/* Verification Results Card */}
          {result && (
            <div className="border-t border-slate-100 bg-slate-50 p-4">
              <div className="flex items-center space-x-1.5 text-emerald-700 text-xs font-semibold mb-2">
                <span>✅</span>
                <span>Document Ingested & Stream Dispatched</span>
              </div>
              <div className="bg-white p-3 rounded-lg border border-slate-200 text-[11px] text-slate-600 space-y-1.5">
                <div className="flex justify-between">
                  <span className="font-medium text-slate-500">Submission ID:</span>
                  <span className="font-mono text-slate-800">{result.submission_id}</span>
                </div>
                <div className="flex justify-between">
                  <span className="font-medium text-slate-500">Status:</span>
                  <span className="bg-emerald-100 text-emerald-800 font-semibold px-1.5 py-0.5 rounded">
                    {result.status}
                  </span>
                </div>
              </div>
            </div>
          )}

        </div>
      </div>
    </div>
  );
};

export default SubmitPCF;