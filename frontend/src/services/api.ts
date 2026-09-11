import axios from 'axios';

// Dynamically use the browser's current host IP address or fallback to localhost
const HOST_IP = typeof window !== 'undefined' ? window.location.hostname : 'localhost';
const API_BASE_URL = `http://${HOST_IP}:8000`;

export const api = axios.create({
  baseURL: API_BASE_URL,
});

/**
 * Automatically attach JWT Access Token to all outgoing HTTP requests
 */
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('pcf_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
}, (error) => {
  return Promise.reject(error);
});

/**
 * Authenticate Investor using TRA TIN and Passcode
 */
export const loginInvestor = async (credentials: { tin_number: string; password: string }) => {
  const response = await api.post('/api/v1/auth/login', credentials);
  if (response.data.access_token) {
    localStorage.setItem('pcf_token', response.data.access_token);
    localStorage.setItem('pcf_company', response.data.company_name);
    localStorage.setItem('pcf_tin', response.data.tin_number);
  }
  return response.data;
};

/**
 * Upload statutory return PDF/image from Investor Web Portal
 */
export const uploadPCFDocument = async (file: File) => {
  const formData = new FormData();
  formData.append('file', file);

  const response = await api.post('/api/v1/ingest/upload', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });

  return response.data;
};

/**
 * Fetch processed statutory submissions for Economist Web Dashboard
 */
export const getEconomistSubmissions = async () => {
  const response = await api.get('/api/v1/economist/submissions');
  return response.data.data;
};

/**
 * Persist economist audit decision, manual overrides, and approval status
 */
export const commitAuditDecision = async (
  submissionId: string,
  auditData: {
    company_name: string;
    tin_number: string;
    bpm6_category: string;
    economist_notes: string;
    status: string;
  }
) => {
  const response = await api.put(
    `/api/v1/economist/submissions/${submissionId}/audit`,
    auditData
  );
  return response.data;
};

/**
 * Wipe all test submissions from backend memory session
 */
export const clearSubmissions = async () => {
  const response = await api.delete('/api/v1/economist/submissions/clear');
  return response.data;
};

// Function alias ensuring backwards compatibility with Overview.tsx imports
export const getSubmissions = getEconomistSubmissions;

export default api;