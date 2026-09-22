import axios from 'axios';

// Dynamically use the browser's current host IP address, or an explicit override.
// VITE_API_BASE_URL is worth setting in any environment that is not localhost: the
// fallback hardcodes both the protocol and the port, so a deployment served over
// HTTPS would try to call http:// and be blocked as mixed content.
const HOST_IP = typeof window !== 'undefined' ? window.location.hostname : 'localhost';
const ENV_BASE_URL = (import.meta as any)?.env?.VITE_API_BASE_URL as string | undefined;
const API_BASE_URL = ENV_BASE_URL || `http://${HOST_IP}:8000`;

export const api = axios.create({
  baseURL: API_BASE_URL,
});

/**
 * Automatically attach JWT Access Token to all outgoing HTTP requests.
 *
 * This is what makes the `Depends(get_current_user)` guards on the economist
 * routes work. Note the consequence: when the token expires the dashboard now
 * receives 401 and stops loading, where before it kept rendering. That is the
 * correct behaviour — it just needs the login flow to send the user back.
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

// --- Interfaces ---
export interface LineItem {
  description: string;
  amount_tzs: number;
  amount_usd: number;
}

export interface AuditDecisionPayload {
  company_name: string;
  tin_number: string;
  /**
   * @deprecated The backend reads `bpm6_category`. Kept only so any existing
   * import of this type still compiles.
   */
  bpm6Category?: string;
  bpm6_category?: string;
  economist_notes?: string;
  // The backend closes this to APPROVED | REJECTED | FLAGGED | PROCESSED_WITH_ALERTS.
  status: string;

  /**
   * CHANGED. The edited questionnaire from the review screen.
   *
   * This is what persists every inline edit — B1/B2/C1/D cells, the N/A toggles,
   * the survey-period years, the Table C2 rates, the A3 acknowledgement block.
   * Before this field existed the backend updated the six audit columns and left
   * `extracted_payload` untouched, so all of that was discarded on Approve.
   *
   * The backend re-validates it through `parse_questionnaire`, which re-runs the
   * arithmetic checks against the edited figures.
   */
  extracted_payload_override?: Record<string, any>;

  /**
   * The A5 activity rows or the A6 shareholding rows, whichever the screen shows.
   *
   * NOT `LineItem[]`. AuditReview sends objects shaped like
   * { activity, estimated_percentage_contribution } or
   * { source_country_or_multilateral, reporting_year_shareholding_pct }; the
   * backend places them into part_a.industrial_classifications or
   * part_a.shareholding_structure by inspecting their keys.
   */
  line_items?: any[];
}

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
  return response.data;
};

/**
 * Persist economist audit decision, manual overrides, and approval status
 */
export const commitAuditDecision = async (
  submissionId: string,
  auditData: AuditDecisionPayload
) => {
  const response = await api.put(
    `/api/v1/economist/submissions/${submissionId}/audit`,
    auditData
  );
  return response.data;
};

/**
 * Wipe all test submissions.
 *
 * The backend now requires a confirmation phrase AND an environment flag
 * (ALLOW_SUBMISSION_CLEAR=1, off by default). The phrase is sent here; the flag
 * is the real gate, and it is the one that keeps this button dead in production.
 * Overview.tsx already asks the user to confirm before calling this.
 */
export const clearSubmissions = async (confirmPhrase = 'DELETE-ALL-FILINGS') => {
  const response = await api.delete('/api/v1/economist/submissions/clear', {
    params: { confirm: confirmPhrase },
  });
  return response.data;
};

// Function alias ensuring backwards compatibility with Overview.tsx imports
export const getSubmissions = getEconomistSubmissions;

export default api;