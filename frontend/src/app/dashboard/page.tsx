"use client";

import RequireAuth from "@/components/RequireAuth";
import PageShell from "@/components/PageShell";
import { useAuth } from "@/lib/auth-context";
import { useEffect, useState } from "react";
import {
  getTenantPlan,
  getUsage,
  listCredentials,
  type TenantPlan,
  type UsageInfo,
  type Credential,
} from "@/lib/api";

function DashboardContent() {
  const { token, user } = useAuth();
  const [plan, setPlan] = useState<TenantPlan | null>(null);
  const [usage, setUsage] = useState<UsageInfo | null>(null);
  const [creds, setCreds] = useState<Credential[]>([]);

  useEffect(() => {
    if (!token) return;
    getTenantPlan(token).then(setPlan).catch(() => {});
    getUsage(token).then(setUsage).catch(() => {});
    listCredentials(token).then(setCreds).catch(() => {});
  }, [token]);

  return (
    <PageShell title="Dashboard">
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
          {plan ? (
            <>
              <p className="text-lg font-semibold capitalize">{plan.plan}</p>
              <p className="text-xs text-gray-500 mt-1">
                Jobs: {plan.limits.daily_optimize_jobs_limit}/day ·
                Applies: {plan.limits.daily_apply_limit}/day
              </p>
            </>
          ) : (
            <p className="text-sm text-gray-400">Loading…</p>
          )}
        </Card>

        <Card title="Today&rsquo;s Usage">
          {usage ? (
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
          ) : (
            <p className="text-sm text-gray-400">Loading…</p>
          )}
        </Card>
      </div>

      {/* Credentials */}
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="text-lg font-semibold mb-3">Credential Profiles</h2>
        {creds.length === 0 ? (
          <p className="text-sm text-gray-500">
            No credentials stored. Add one in the Price Optimizer to connect to DanDomain.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-500 border-b">
                <th className="pb-2">Label</th>
                <th className="pb-2">API Username</th>
                <th className="pb-2">Created</th>
              </tr>
            </thead>
            <tbody>
              {creds.map((c) => (
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
