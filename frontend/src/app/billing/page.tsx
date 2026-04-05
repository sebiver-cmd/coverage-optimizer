"use client";

import RequireAuth from "@/components/RequireAuth";
import PageShell from "@/components/PageShell";
import { useAuth } from "@/lib/auth-context";
import { useEffect, useState } from "react";
import {
  getBillingStatus,
  getTenantPlan,
  createCheckout,
  type BillingStatus,
  type TenantPlan,
  ApiError,
} from "@/lib/api";

function BillingContent() {
  const { token, user } = useAuth();
  const [plan, setPlan] = useState<TenantPlan | null>(null);
  const [billing, setBilling] = useState<BillingStatus | null>(null);
  const [selectedPlan, setSelectedPlan] = useState("pro");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!token) return;
    getTenantPlan(token).then(setPlan).catch((e) => setError(e instanceof Error ? e.message : "Failed to load plan"));
    getBillingStatus(token).then(setBilling).catch((e) => setError(e instanceof Error ? e.message : "Failed to load billing status"));
  }, [token]);

  async function handleUpgrade() {
    if (!token) return;
    setError(null);
    setLoading(true);
    try {
      const successUrl = `${window.location.origin}/billing?success=1`;
      const cancelUrl = `${window.location.origin}/billing?cancelled=1`;
      const res = await createCheckout(token, selectedPlan, successUrl, cancelUrl);
      window.location.href = res.checkout_url;
    } catch (err) {
      if (err instanceof ApiError) setError(err.detail);
      else setError("Failed to create checkout session");
    } finally {
      setLoading(false);
    }
  }

  const canManage = user?.role === "owner" || user?.role === "admin";

  return (
    <PageShell title="Billing">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
        {/* Plan Info */}
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-sm font-medium text-gray-500 mb-3">Current Plan</h2>
          {plan ? (
            <>
              <p className="text-2xl font-bold capitalize mb-2">{plan.plan}</p>
              <div className="space-y-1 text-sm text-gray-600">
                <p>Daily optimize jobs: {plan.limits.daily_optimize_jobs_limit}</p>
                <p>Daily applies: {plan.limits.daily_apply_limit}</p>
                <p>Daily sync optimizations: {plan.limits.daily_optimize_sync_limit}</p>
              </div>
            </>
          ) : (
            <p className="text-sm text-gray-400">Loading…</p>
          )}
        </div>

        {/* Billing Status */}
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-sm font-medium text-gray-500 mb-3">Billing Status</h2>
          {billing ? (
            billing.billing_enabled ? (
              <div className="space-y-2 text-sm">
                <p>
                  <strong>Status:</strong>{" "}
                  <span
                    className={
                      billing.billing_status === "active"
                        ? "text-green-600 font-semibold"
                        : "text-yellow-600"
                    }
                  >
                    {billing.billing_status}
                  </span>
                </p>
                <p>
                  <strong>Stripe Customer:</strong>{" "}
                  {billing.stripe_customer_id ? "✅ Linked" : "❌ Not linked"}
                </p>
                <p>
                  <strong>Subscription:</strong>{" "}
                  {billing.stripe_subscription_id ? "✅ Active" : "❌ None"}
                </p>
              </div>
            ) : (
              <p className="text-sm text-gray-500">
                Billing is not enabled for this instance.
              </p>
            )
          ) : (
            <p className="text-sm text-gray-400">Loading…</p>
          )}
        </div>
      </div>

      {/* Upgrade */}
      {billing?.billing_enabled && canManage && (
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-lg font-semibold mb-3">Upgrade Plan</h2>

          {error && (
            <div role="alert" aria-live="polite" className="bg-red-50 text-red-700 border border-red-200 rounded p-3 mb-4 text-sm">
              {error}
            </div>
          )}

          <div className="flex gap-3 items-end">
            <div>
              <label htmlFor="billing-plan" className="block text-xs font-medium text-gray-500 mb-1">Target Plan</label>
              <select
                id="billing-plan"
                value={selectedPlan}
                onChange={(e) => setSelectedPlan(e.target.value)}
                className="border rounded px-3 py-1.5 text-sm"
              >
                <option value="pro">Pro</option>
                <option value="enterprise">Enterprise</option>
              </select>
            </div>
            <button
              onClick={handleUpgrade}
              disabled={loading}
              className="bg-blue-600 text-white px-4 py-1.5 rounded text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
            >
              {loading ? "Redirecting…" : "Upgrade via Stripe"}
            </button>
          </div>
        </div>
      )}
    </PageShell>
  );
}

export default function BillingPage() {
  return (
    <RequireAuth>
      <BillingContent />
    </RequireAuth>
  );
}
