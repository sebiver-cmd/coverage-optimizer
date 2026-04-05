/**
 * API client — all backend communication goes through here.
 *
 * The backend base URL is read from NEXT_PUBLIC_API_URL
 * (defaults to http://localhost:8000 in development).
 */

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/* ------------------------------------------------------------------ */
/*  Generic helpers                                                    */
/* ------------------------------------------------------------------ */

function authHeaders(token: string | null): Record<string, string> {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (token) h["Authorization"] = `Bearer ${token}`;
  return h;
}

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(
  method: string,
  path: string,
  token: string | null,
  body?: unknown,
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: authHeaders(token),
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail ?? JSON.stringify(j);
    } catch {
      /* ignore parse error */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

/* ------------------------------------------------------------------ */
/*  Auth                                                               */
/* ------------------------------------------------------------------ */

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export interface UserMe {
  id: string;
  tenant_id: string;
  email: string;
  role: "owner" | "admin" | "operator" | "viewer";
  created_at: string;
}

export function signup(
  tenant_name: string,
  email: string,
  password: string,
): Promise<TokenResponse> {
  return request("POST", "/auth/signup", null, {
    tenant_name,
    email,
    password,
  });
}

export function login(
  email: string,
  password: string,
): Promise<TokenResponse> {
  return request("POST", "/auth/login", null, { email, password });
}

export function refreshToken(token: string): Promise<TokenResponse> {
  return request("POST", "/auth/refresh", token);
}

export function getMe(token: string): Promise<UserMe> {
  return request("GET", "/auth/me", token);
}

/* ------------------------------------------------------------------ */
/*  Credentials (vault)                                                */
/* ------------------------------------------------------------------ */

export interface Credential {
  id: string;
  label: string;
  api_username: string;
  created_at: string;
}

export function listCredentials(token: string): Promise<Credential[]> {
  return request("GET", "/credentials", token);
}

export function createCredential(
  token: string,
  label: string,
  api_username: string,
  api_password: string,
  site_id: number,
): Promise<Credential> {
  return request("POST", "/credentials", token, {
    label,
    api_username,
    api_password,
    site_id,
  });
}

export function deleteCredential(
  token: string,
  id: string,
): Promise<void> {
  return request("DELETE", `/credentials/${id}`, token);
}

/* ------------------------------------------------------------------ */
/*  Brands                                                             */
/* ------------------------------------------------------------------ */

export interface Brand {
  id: number;
  name: string;
}

export function listBrands(token: string): Promise<Brand[]> {
  return request("GET", "/brands", token);
}

/* ------------------------------------------------------------------ */
/*  Optimization (async jobs)                                          */
/* ------------------------------------------------------------------ */

export interface OptimizePayload {
  credential_id?: string;
  brand_filter?: string[];
  only_active?: boolean;
  min_coverage_rate?: number;
  beautify_digit?: number | null;
  include_buy_price?: boolean;
}

export interface JobResponse {
  job_id: string;
}

export interface JobStatus {
  job_id: string;
  status: "queued" | "running" | "complete" | "failed";
  result?: OptimizeResult;
  error?: string;
  created_at?: string;
  finished_at?: string;
}

export interface OptimizeResult {
  products: ProductRow[];
  summary: OptimizeSummary;
}

export interface ProductRow {
  PRODUCT_NUMBER: string;
  PRODUCT_NAME: string;
  BRAND_NAME?: string;
  BUY_PRICE?: number;
  PRICE_EX_VAT: number;
  SUGGESTED_PRICE_EX_VAT: number;
  CURRENT_COVERAGE_RATE: number;
  NEW_COVERAGE_RATE: number;
  CHANGE_PERCENT: number;
  VARIANT_ITEMNUMBER?: string;
  [key: string]: unknown;
}

export interface OptimizeSummary {
  total_products: number;
  adjusted_count: number;
  avg_coverage_before: number;
  avg_coverage_after: number;
  top_increases: ProductRow[];
  top_decreases: ProductRow[];
}

export function enqueueOptimize(
  token: string,
  payload: OptimizePayload,
): Promise<JobResponse> {
  return request("POST", "/jobs/optimize", token, payload);
}

export function getJobStatus(
  token: string,
  jobId: string,
): Promise<JobStatus> {
  return request("GET", `/jobs/${jobId}`, token);
}

/* ------------------------------------------------------------------ */
/*  Apply Prices                                                       */
/* ------------------------------------------------------------------ */

export interface DryRunResponse {
  batch_id: string;
  changes: ChangeRow[];
  summary: ChangeSummary;
}

export interface ChangeRow {
  product_number: string;
  product_name?: string;
  old_price: number;
  new_price: number;
  change_percent: number;
}

export interface ChangeSummary {
  total: number;
  increases: number;
  decreases: number;
  unchanged: number;
}

export interface ApplyResponse {
  batch_id: string;
  applied_count: number;
  failed: FailedRow[];
  started_at: string;
  finished_at: string;
}

export interface FailedRow {
  product_number: string;
  reason: string;
}

export function dryRun(
  token: string,
  payload: OptimizePayload,
): Promise<DryRunResponse> {
  return request("POST", "/apply-prices/dry-run", token, payload);
}

export function createManifest(
  token: string,
  payload: OptimizePayload,
): Promise<DryRunResponse> {
  return request("POST", "/apply-prices/create-manifest", token, payload);
}

export function applyPrices(
  token: string,
  batchId: string,
  confirm: boolean,
): Promise<ApplyResponse> {
  return request("POST", "/apply-prices/apply", token, {
    batch_id: batchId,
    confirm,
  });
}

export function getBatch(
  token: string,
  batchId: string,
): Promise<DryRunResponse> {
  return request("GET", `/apply-prices/batch/${batchId}`, token);
}

/* ------------------------------------------------------------------ */
/*  History (list endpoints)                                           */
/* ------------------------------------------------------------------ */

export interface JobListItem {
  job_id: string;
  status: string;
  created_at: string;
  finished_at?: string;
  error?: string;
}

export interface BatchListItem {
  batch_id: string;
  mode: string;
  status: string;
  created_at: string;
  finished_at?: string;
}

export interface AuditEvent {
  id: string;
  event_type: string;
  created_at: string;
  meta?: Record<string, string>;
}

export function listJobs(
  token: string,
  params?: { status?: string; limit?: number },
): Promise<JobListItem[]> {
  const q = new URLSearchParams();
  if (params?.status) q.set("status", params.status);
  if (params?.limit) q.set("limit", String(params.limit));
  const qs = q.toString();
  return request("GET", `/jobs${qs ? `?${qs}` : ""}`, token);
}

export function listBatches(
  token: string,
  params?: { status?: string; mode?: string; limit?: number },
): Promise<BatchListItem[]> {
  const q = new URLSearchParams();
  if (params?.status) q.set("status", params.status);
  if (params?.mode) q.set("mode", params.mode);
  if (params?.limit) q.set("limit", String(params.limit));
  const qs = q.toString();
  return request("GET", `/apply-prices/batches${qs ? `?${qs}` : ""}`, token);
}

export function listAuditEvents(
  token: string,
  params?: { event_type?: string; limit?: number },
): Promise<AuditEvent[]> {
  const q = new URLSearchParams();
  if (params?.event_type) q.set("event_type", params.event_type);
  if (params?.limit) q.set("limit", String(params.limit));
  const qs = q.toString();
  return request("GET", `/audit${qs ? `?${qs}` : ""}`, token);
}

/* ------------------------------------------------------------------ */
/*  Plans & Usage                                                      */
/* ------------------------------------------------------------------ */

export interface PlanInfo {
  name: string;
  daily_optimize_jobs_limit: number;
  daily_apply_limit: number;
  daily_optimize_sync_limit: number;
}

export interface TenantPlan {
  plan: string;
  limits: PlanInfo;
}

export interface UsageInfo {
  daily_optimize_jobs: number;
  daily_apply: number;
  daily_optimize_sync: number;
  limits: PlanInfo;
}

export function listPlans(token: string): Promise<PlanInfo[]> {
  return request("GET", "/plans", token);
}

export function getTenantPlan(token: string): Promise<TenantPlan> {
  return request("GET", "/tenant/plan", token);
}

export function getUsage(token: string): Promise<UsageInfo> {
  return request("GET", "/usage", token);
}

/* ------------------------------------------------------------------ */
/*  Billing                                                            */
/* ------------------------------------------------------------------ */

export interface BillingStatus {
  billing_enabled: boolean;
  billing_status: string;
  stripe_customer_id?: string;
  stripe_subscription_id?: string;
}

export interface CheckoutResponse {
  checkout_url: string;
}

export function getBillingStatus(token: string): Promise<BillingStatus> {
  return request("GET", "/billing/status", token);
}

export function createCheckout(
  token: string,
  plan: string,
  successUrl: string,
  cancelUrl: string,
): Promise<CheckoutResponse> {
  return request("POST", "/billing/checkout", token, {
    plan,
    success_url: successUrl,
    cancel_url: cancelUrl,
  });
}

export interface InvoiceItem {
  id: string;
  event_type: string;
  created_at: string;
  description: string;
  meta?: Record<string, unknown>;
}

export interface InvoicesResponse {
  total: number;
  items: InvoiceItem[];
}

export function getBillingInvoices(
  token: string,
  opts?: { limit?: number; offset?: number },
): Promise<InvoicesResponse> {
  const params = new URLSearchParams();
  if (opts?.limit != null) params.set("limit", String(opts.limit));
  if (opts?.offset != null) params.set("offset", String(opts.offset));
  const qs = params.toString();
  return request("GET", `/billing/invoices${qs ? `?${qs}` : ""}`, token);
}

/* ------------------------------------------------------------------ */
/*  Health                                                             */
/* ------------------------------------------------------------------ */

export interface HealthResponse {
  status: string;
  version: string;
  apply_enabled: boolean;
  timestamp: string;
}

export async function getHealth(): Promise<HealthResponse> {
  const res = await fetch(`${BASE}/health`);
  return res.json();
}
