"use client";

import RequireAuth from "@/components/RequireAuth";
import PageShell from "@/components/PageShell";
import { useAuth } from "@/lib/auth-context";
import { useCallback, useState } from "react";
import {
  listJobs,
  listBatches,
  listAuditEvents,
  type JobListItem,
  type BatchListItem,
  type AuditEvent,
} from "@/lib/api";
import { useCachedFetch } from "@/lib/use-cached-fetch";
import { SkeletonTable } from "@/components/Skeleton";

type Tab = "jobs" | "batches" | "audit";

function HistoryContent() {
  const { token } = useAuth();
  const [tab, setTab] = useState<Tab>("jobs");

  /* ---- Jobs ---- */
  const [jobStatusFilter, setJobStatusFilter] = useState("");
  const [jobLimit, setJobLimit] = useState(50);
  const jobsFetcher = useCallback(
    () => listJobs(token!, { status: jobStatusFilter || undefined, limit: jobLimit }),
    [token, jobStatusFilter, jobLimit],
  );
  const { data: jobs, loading: jobsLoading, error: jobsError } = useCachedFetch<JobListItem[]>(
    jobsFetcher,
    `/jobs?status=${jobStatusFilter}&limit=${jobLimit}`,
    token,
    { ttl: 30_000, skip: tab !== "jobs" },
  );

  /* ---- Batches ---- */
  const [batchStatusFilter, setBatchStatusFilter] = useState("");
  const [batchModeFilter, setBatchModeFilter] = useState("");
  const [batchLimit, setBatchLimit] = useState(50);
  const batchesFetcher = useCallback(
    () =>
      listBatches(token!, {
        status: batchStatusFilter || undefined,
        mode: batchModeFilter || undefined,
        limit: batchLimit,
      }),
    [token, batchStatusFilter, batchModeFilter, batchLimit],
  );
  const { data: batches, loading: batchesLoading, error: batchesError } = useCachedFetch<BatchListItem[]>(
    batchesFetcher,
    `/batches?status=${batchStatusFilter}&mode=${batchModeFilter}&limit=${batchLimit}`,
    token,
    { ttl: 30_000, skip: tab !== "batches" },
  );

  /* ---- Audit ---- */
  const [eventTypeFilter, setEventTypeFilter] = useState("");
  const [auditLimit, setAuditLimit] = useState(50);
  const auditFetcher = useCallback(
    () =>
      listAuditEvents(token!, {
        event_type: eventTypeFilter || undefined,
        limit: auditLimit,
      }),
    [token, eventTypeFilter, auditLimit],
  );
  const { data: audit, loading: auditLoading, error: auditError } = useCachedFetch<AuditEvent[]>(
    auditFetcher,
    `/audit?event_type=${eventTypeFilter}&limit=${auditLimit}`,
    token,
    { ttl: 30_000, skip: tab !== "audit" },
  );

  const error = (tab === "jobs" && jobsError) || (tab === "batches" && batchesError) || (tab === "audit" && auditError) || null;

  return (
    <PageShell title="History">
      {error && (
        <div role="alert" aria-live="polite" className="bg-red-50 text-red-700 border border-red-200 rounded p-3 mb-4 text-sm">
          {error}
        </div>
      )}
      {/* Tabs */}
      <div className="flex gap-1 mb-4">
        {(
          [
            ["jobs", "Optimisation Jobs"],
            ["batches", "Apply Batches"],
            ["audit", "Audit Events"],
          ] as [Tab, string][]
        ).map(([t, label]) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-1.5 rounded-t text-sm font-medium ${
              tab === t ? "bg-white border-t border-x" : "bg-gray-100 text-gray-500 hover:bg-gray-200"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="bg-white rounded-b-lg rounded-r-lg shadow p-4">
        {/* ---- Jobs ---- */}
        {tab === "jobs" && (
          <>
            <div className="flex gap-3 mb-4">
              <div>
                <label htmlFor="job-status-filter" className="sr-only">Job status filter</label>
                <select
                  id="job-status-filter"
                  value={jobStatusFilter}
                  onChange={(e) => setJobStatusFilter(e.target.value)}
                  className="border rounded px-2 py-1 text-sm"
                >
                  <option value="">All statuses</option>
                  <option value="queued">Queued</option>
                  <option value="running">Running</option>
                  <option value="complete">Complete</option>
                  <option value="failed">Failed</option>
                </select>
              </div>
              <div>
                <label htmlFor="job-limit" className="sr-only">Rows per page</label>
                <select
                  id="job-limit"
                  value={jobLimit}
                  onChange={(e) => setJobLimit(Number(e.target.value))}
                  className="border rounded px-2 py-1 text-sm"
                >
                  {[25, 50, 100].map((n) => (
                    <option key={n} value={n}>
                      {n} rows
                    </option>
                  ))}
                </select>
              </div>
            </div>
            {jobsLoading ? (
              <SkeletonTable rows={5} cols={5} />
            ) : (
            <table aria-label="Optimisation jobs" className="w-full text-xs">
              <thead>
                <tr className="text-left text-gray-500 border-b">
                  <th className="pb-1 pr-2">Job ID</th>
                  <th className="pb-1 pr-2">Status</th>
                  <th className="pb-1 pr-2">Created</th>
                  <th className="pb-1 pr-2">Finished</th>
                  <th className="pb-1">Error</th>
                </tr>
              </thead>
              <tbody>
                {(jobs ?? []).map((j) => (
                  <tr key={j.job_id} className="border-b last:border-0">
                    <td className="py-1 pr-2 font-mono">{j.job_id.slice(0, 8)}…</td>
                    <td className="py-1 pr-2">
                      <StatusBadge status={j.status} />
                    </td>
                    <td className="py-1 pr-2 text-gray-500">{fmtDate(j.created_at)}</td>
                    <td className="py-1 pr-2 text-gray-500">{j.finished_at ? fmtDate(j.finished_at) : "—"}</td>
                    <td className="py-1 text-red-500 truncate max-w-[200px]">{j.error ?? ""}</td>
                  </tr>
                ))}
                {(jobs ?? []).length === 0 && (
                  <tr>
                    <td colSpan={5} className="py-4 text-center text-gray-400">
                      No jobs found
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
            )}
          </>
        )}

        {/* ---- Batches ---- */}
        {tab === "batches" && (
          <>
            <div className="flex gap-3 mb-4">
              <div>
                <label htmlFor="batch-status-filter" className="sr-only">Batch status filter</label>
                <select
                  id="batch-status-filter"
                  value={batchStatusFilter}
                  onChange={(e) => setBatchStatusFilter(e.target.value)}
                  className="border rounded px-2 py-1 text-sm"
                >
                  <option value="">All statuses</option>
                  <option value="pending">Pending</option>
                  <option value="applied">Applied</option>
                  <option value="failed">Failed</option>
                </select>
              </div>
              <div>
                <label htmlFor="batch-mode-filter" className="sr-only">Batch mode filter</label>
                <select
                  id="batch-mode-filter"
                  value={batchModeFilter}
                  onChange={(e) => setBatchModeFilter(e.target.value)}
                  className="border rounded px-2 py-1 text-sm"
                >
                  <option value="">All modes</option>
                  <option value="dry_run">Dry Run</option>
                  <option value="apply">Apply</option>
                  <option value="create_manifest">Create Manifest</option>
                </select>
              </div>
              <div>
                <label htmlFor="batch-limit" className="sr-only">Rows per page</label>
                <select
                  id="batch-limit"
                  value={batchLimit}
                  onChange={(e) => setBatchLimit(Number(e.target.value))}
                  className="border rounded px-2 py-1 text-sm"
                >
                  {[25, 50, 100].map((n) => (
                    <option key={n} value={n}>
                      {n} rows
                    </option>
                  ))}
                </select>
              </div>
            </div>
            {batchesLoading ? (
              <SkeletonTable rows={5} cols={5} />
            ) : (
            <table aria-label="Apply batches" className="w-full text-xs">
              <thead>
                <tr className="text-left text-gray-500 border-b">
                  <th className="pb-1 pr-2">Batch ID</th>
                  <th className="pb-1 pr-2">Mode</th>
                  <th className="pb-1 pr-2">Status</th>
                  <th className="pb-1 pr-2">Created</th>
                  <th className="pb-1">Finished</th>
                </tr>
              </thead>
              <tbody>
                {(batches ?? []).map((b) => (
                  <tr key={b.batch_id} className="border-b last:border-0">
                    <td className="py-1 pr-2 font-mono">{b.batch_id.slice(0, 8)}…</td>
                    <td className="py-1 pr-2">{b.mode}</td>
                    <td className="py-1 pr-2">
                      <StatusBadge status={b.status} />
                    </td>
                    <td className="py-1 pr-2 text-gray-500">{fmtDate(b.created_at)}</td>
                    <td className="py-1 text-gray-500">{b.finished_at ? fmtDate(b.finished_at) : "—"}</td>
                  </tr>
                ))}
                {(batches ?? []).length === 0 && (
                  <tr>
                    <td colSpan={5} className="py-4 text-center text-gray-400">
                      No batches found
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
            )}
          </>
        )}

        {/* ---- Audit ---- */}
        {tab === "audit" && (
          <>
            <div className="flex gap-3 mb-4">
              <div>
                <label htmlFor="audit-event-type" className="sr-only">Event type filter</label>
                <input
                  id="audit-event-type"
                  type="text"
                  placeholder="Filter by event type"
                  value={eventTypeFilter}
                  onChange={(e) => setEventTypeFilter(e.target.value)}
                  className="border rounded px-2 py-1 text-sm w-48"
                />
              </div>
              <div>
                <label htmlFor="audit-limit" className="sr-only">Rows per page</label>
                <select
                  id="audit-limit"
                  value={auditLimit}
                  onChange={(e) => setAuditLimit(Number(e.target.value))}
                  className="border rounded px-2 py-1 text-sm"
                >
                  {[25, 50, 100].map((n) => (
                    <option key={n} value={n}>
                      {n} rows
                    </option>
                  ))}
                </select>
              </div>
            </div>
            {auditLoading ? (
              <SkeletonTable rows={5} cols={4} />
            ) : (
            <table aria-label="Audit events" className="w-full text-xs">
              <thead>
                <tr className="text-left text-gray-500 border-b">
                  <th className="pb-1 pr-2">ID</th>
                  <th className="pb-1 pr-2">Event Type</th>
                  <th className="pb-1 pr-2">Created</th>
                  <th className="pb-1">Meta</th>
                </tr>
              </thead>
              <tbody>
                {(audit ?? []).map((a) => (
                  <tr key={a.id} className="border-b last:border-0">
                    <td className="py-1 pr-2 font-mono">{a.id.slice(0, 8)}…</td>
                    <td className="py-1 pr-2">{a.event_type}</td>
                    <td className="py-1 pr-2 text-gray-500">{fmtDate(a.created_at)}</td>
                    <td className="py-1 text-gray-400 truncate max-w-[300px]">
                      {a.meta
                        ? Object.entries(a.meta)
                            .map(([k, v]) => `${k}=${v}`)
                            .join(", ")
                        : ""}
                    </td>
                  </tr>
                ))}
                {(audit ?? []).length === 0 && (
                  <tr>
                    <td colSpan={4} className="py-4 text-center text-gray-400">
                      No audit events found
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
            )}
          </>
        )}
      </div>
    </PageShell>
  );
}

/* Helpers */

function StatusBadge({ status }: { status: string }) {
  const color: Record<string, string> = {
    queued: "bg-yellow-100 text-yellow-800",
    running: "bg-blue-100 text-blue-800",
    complete: "bg-green-100 text-green-800",
    applied: "bg-green-100 text-green-800",
    pending: "bg-yellow-100 text-yellow-800",
    failed: "bg-red-100 text-red-800",
  };
  return (
    <span className={`text-xs font-medium px-2 py-0.5 rounded ${color[status] ?? "bg-gray-100"}`}>
      {status}
    </span>
  );
}

function fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export default function HistoryPage() {
  return (
    <RequireAuth>
      <HistoryContent />
    </RequireAuth>
  );
}
