"use client";

import RequireAuth from "@/components/RequireAuth";
import PageShell from "@/components/PageShell";
import { useAuth } from "@/lib/auth-context";
import { useCallback, useMemo, useState } from "react";
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
import VirtualTable, { type ColumnDef } from "@/components/VirtualTable";

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
              <VirtualTable
                rows={jobs ?? []}
                columns={jobColumns}
                ariaLabel="Optimisation jobs"
                getRowKey={(j) => j.job_id}
                emptyMessage="No jobs found"
              />
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
              <VirtualTable
                rows={batches ?? []}
                columns={batchColumns}
                ariaLabel="Apply batches"
                getRowKey={(b) => b.batch_id}
                emptyMessage="No batches found"
              />
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
              <VirtualTable
                rows={audit ?? []}
                columns={auditColumns}
                ariaLabel="Audit events"
                getRowKey={(a) => a.id}
                emptyMessage="No audit events found"
              />
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

/* Column definitions for VirtualTable */

const jobColumns: ColumnDef<JobListItem>[] = [
  { header: "Job ID", cellClassName: "font-mono", render: (j) => `${j.job_id.slice(0, 8)}…` },
  { header: "Status", render: (j) => <StatusBadge status={j.status} /> },
  { header: "Created", cellClassName: "text-gray-500", render: (j) => fmtDate(j.created_at) },
  { header: "Finished", cellClassName: "text-gray-500", render: (j) => (j.finished_at ? fmtDate(j.finished_at) : "—") },
  { header: "Error", cellClassName: "text-red-500 truncate max-w-[200px]", render: (j) => j.error ?? "" },
];

const batchColumns: ColumnDef<BatchListItem>[] = [
  { header: "Batch ID", cellClassName: "font-mono", render: (b) => `${b.batch_id.slice(0, 8)}…` },
  { header: "Mode", render: (b) => b.mode },
  { header: "Status", render: (b) => <StatusBadge status={b.status} /> },
  { header: "Created", cellClassName: "text-gray-500", render: (b) => fmtDate(b.created_at) },
  { header: "Finished", cellClassName: "text-gray-500", render: (b) => (b.finished_at ? fmtDate(b.finished_at) : "—") },
];

const auditColumns: ColumnDef<AuditEvent>[] = [
  { header: "ID", cellClassName: "font-mono", render: (a) => `${a.id.slice(0, 8)}…` },
  { header: "Event Type", render: (a) => a.event_type },
  { header: "Created", cellClassName: "text-gray-500", render: (a) => fmtDate(a.created_at) },
  {
    header: "Meta",
    cellClassName: "text-gray-400 truncate max-w-[300px]",
    render: (a) =>
      a.meta
        ? Object.entries(a.meta)
            .map(([k, v]) => `${k}=${v}`)
            .join(", ")
        : "",
  },
];

export default function HistoryPage() {
  return (
    <RequireAuth>
      <HistoryContent />
    </RequireAuth>
  );
}
