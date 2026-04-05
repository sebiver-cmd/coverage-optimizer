"use client";

import RequireAuth from "@/components/RequireAuth";
import PageShell from "@/components/PageShell";
import { useAuth } from "@/lib/auth-context";
import { useCallback } from "react";
import {
  getTenantPlan,
  getUsage,
  listCredentials,
  type TenantPlan,
  type UsageInfo,
  type Credential,
} from "@/lib/api";
import { useCachedFetch } from "@/lib/use-cached-fetch";
import { SkeletonCard, SkeletonText } from "@/components/Skeleton";

function DashboardContent() {
  const { token, user } = useAuth();

  const planFetcher = useCallback(() => getTenantPlan(token!), [token]);
  const usageFetcher = useCallback(() => getUsage(token!), [token]);
  const credsFetcher = useCallback(() => listCredentials(token!), [token]);

  const { data: plan, loading: planLoading, error: planErr, refetch: refetchPlan } =
    useCachedFetch<TenantPlan>(planFetcher, "/tenant/plan", token);
  const { data: usage, loading: usageLoading, error: usageErr, refetch: refetchUsage } =
    useCachedFetch<UsageInfo>(usageFetcher, "/usage", token, { ttl: 30_000 });
  const { data: creds, loading: credsLoading, error: credsErr, refetch: refetchCreds } =
    useCachedFetch<Credential[]>(credsFetcher, "/credentials", token);

  const error = planErr ?? usageErr ?? credsErr;
  const loadData = useCallback(() => {
    refetchPlan();
    refetchUsage();
    refetchCreds();
  }, [refetchPlan, refetchUsage, refetchCreds]);

  return (
    <PageShell title="Dashboard">
      {error && (
        <div role="alert" aria-live="polite" className="bg-red-50 text-red-700 border border-red-200 rounded p-3 mb-4 text-sm flex items-center justify-between">
          <span>{error}</span>
          <button
            onClick={loadData}
            className="ml-4 text-red-700 underline text-sm font-medium hover:text-red-800 focus:outline-none focus:ring-2 focus:ring-red-500"
          >
            Retry
          </button>
        </div>
      )}
      {/* User info card */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
        <Card title="Account">
          <p className="text-sm text-gray-600">
            <strong>Email:</strong> {user?.email}
          </p>
          <p className="text-sm text-gray-600">
            <strong>Role:</strong> {user?.role}
          </p>
        </Card>

        <Card title="Plan">
          {planLoading ? (
            <SkeletonText lines={2} />
          ) : plan ? (
            <>
              <p className="text-lg font-semibold capitalize">{plan.plan}</p>
              <p className="text-xs text-gray-500 mt-1">
                Jobs: {plan.limits.daily_optimize_jobs_limit}/day ·
                Applies: {plan.limits.daily_apply_limit}/day
              </p>
            </>
          ) : null}
        </Card>

        <Card title="Today&rsquo;s Usage">
          {usageLoading ? (
            <SkeletonText lines={2} />
          ) : usage ? (
            <div className="space-y-1 text-sm">
              <UsageBar
                label="Optimize jobs"
                used={usage.daily_optimize_jobs}
                limit={usage.limits.daily_optimize_jobs_limit}
              />
              <UsageBar
                label="Applies"
                used={usage.daily_apply}
                limit={usage.limits.daily_apply_limit}
              />
            </div>
          ) : null}
        </Card>
      </div>

      {/* Credentials */}
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="text-lg font-semibold mb-3">Credential Profiles</h2>
        {credsLoading ? (
          <SkeletonText lines={3} />
        ) : creds && creds.length === 0 ? (
          <p className="text-sm text-gray-500">
            No credentials stored. Add one in the Price Optimizer to connect to DanDomain.
          </p>
        ) : (
          <table aria-label="Credential profiles" className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-500 border-b">
                <th className="pb-2">Label</th>
                <th className="pb-2">API Username</th>
                <th className="pb-2">Created</th>
              </tr>
            </thead>
            <tbody>
              {(creds ?? []).map((c) => (
                <tr key={c.id} className="border-b last:border-0">
                  <td className="py-2">{c.label}</td>
                  <td className="py-2 text-gray-600">{c.api_username}</td>
                  <td className="py-2 text-gray-400">{new Date(c.created_at).toLocaleDateString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </PageShell>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-lg shadow p-6">
      <h2 className="text-sm font-medium text-gray-500 mb-2">{title}</h2>
      {children}
    </div>
  );
}

function UsageBar({ label, used, limit }: { label: string; used: number; limit: number }) {
  const pct = limit > 0 ? Math.min(100, (used / limit) * 100) : 0;
  return (
    <div>
      <div className="flex justify-between text-xs text-gray-600 mb-0.5">
        <span>{label}</span>
        <span>
          {used} / {limit}
        </span>
      </div>
      <div className="w-full bg-gray-200 rounded-full h-2">
        <div
          className={`h-2 rounded-full ${pct > 90 ? "bg-red-500" : pct > 60 ? "bg-yellow-500" : "bg-blue-500"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export default function DashboardPage() {
  return (
    <RequireAuth>
      <DashboardContent />
    </RequireAuth>
  );
}
