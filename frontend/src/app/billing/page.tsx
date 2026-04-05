"use client";

import RequireAuth from "@/components/RequireAuth";
import PageShell from "@/components/PageShell";
import { useAuth } from "@/lib/auth-context";
import { useCallback, useState } from "react";
import {
  getBillingStatus,
  getBillingInvoices,
  getTenantPlan,
  createCheckout,
  type BillingStatus,
  type TenantPlan,
  type InvoicesResponse,
  ApiError,
} from "@/lib/api";
import { useCachedFetch } from "@/lib/use-cached-fetch";
import { SkeletonText, SkeletonTable } from "@/components/Skeleton";
import VirtualTable, { type ColumnDef } from "@/components/VirtualTable";
import type { InvoiceItem } from "@/lib/api";

const invoiceColumns: ColumnDef<InvoiceItem>[] = [
  { header: "Date", cellClassName: "text-gray-500", render: (i) => fmtDate(i.created_at) },
  { header: "Description", render: (i) => i.description },
  { header: "Event Type", cellClassName: "text-gray-500", render: (i) => i.event_type },
];

function fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function BillingContent() {
  const { token, user } = useAuth();

  const planFetcher = useCallback(() => getTenantPlan(token!), [token]);
  const billingFetcher = useCallback(() => getBillingStatus(token!), [token]);
  const invoiceFetcher = useCallback(() => getBillingInvoices(token!, { limit: 50 }), [token]);

  const { data: plan, loading: planLoading } =
    useCachedFetch<TenantPlan>(planFetcher, "/tenant/plan", token);
  const { data: billing, loading: billingLoading } =
    useCachedFetch<BillingStatus>(billingFetcher, "/billing/status", token);
  const { data: invoices, loading: invoicesLoading, error: invoicesError } =
    useCachedFetch<InvoicesResponse>(invoiceFetcher, "/billing/invoices", token);

  const [selectedPlan, setSelectedPlan] = useState("pro");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

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
          {planLoading ? (
            <SkeletonText lines={3} />
          ) : plan ? (
            <>
              <p className="text-2xl font-bold capitalize mb-2">{plan.plan}</p>
              <div className="space-y-1 text-sm text-gray-600">
                <p>Daily optimize jobs: {plan.limits.daily_optimize_jobs_limit}</p>
                <p>Daily applies: {plan.limits.daily_apply_limit}</p>
                <p>Daily sync optimizations: {plan.limits.daily_optimize_sync_limit}</p>
              </div>
            </>
          ) : null}
        </div>

        {/* Billing Status */}
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-sm font-medium text-gray-500 mb-3">Billing Status</h2>
          {billingLoading ? (
            <SkeletonText lines={3} />
          ) : billing ? (
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
          ) : null}
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

      {/* Billing History */}
      <div className="bg-white rounded-lg shadow p-6 mt-6">
        <h2 className="text-lg font-semibold mb-3">Billing History</h2>
        {billing && !billing.billing_enabled ? (
          <p className="text-sm text-gray-500">
            Billing history is not available when billing is disabled.
          </p>
        ) : invoicesLoading ? (
          <SkeletonTable rows={4} cols={3} />
        ) : invoicesError ? (
          <p className="text-sm text-gray-500">
            Billing history is not available.
          </p>
        ) : invoices && invoices.items.length > 0 ? (
          <>
            <VirtualTable
              rows={invoices.items}
              columns={invoiceColumns}
              ariaLabel="Billing history"
              getRowKey={(i) => i.id}
              maxHeight={400}
              emptyMessage="No billing history"
            />
            {invoices.total > invoices.items.length && (
              <p className="text-xs text-gray-400 mt-2">
                Showing {invoices.items.length} of {invoices.total} events
              </p>
            )}
          </>
        ) : (
          <p className="text-sm text-gray-500">No billing events yet.</p>
        )}
      </div>
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
