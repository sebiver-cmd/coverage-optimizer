"use client";

import RequireAuth from "@/components/RequireAuth";
import PageShell from "@/components/PageShell";
import { useAuth } from "@/lib/auth-context";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  listBrands,
  listCredentials,
  enqueueOptimize,
  getJobStatus,
  dryRun,
  applyPrices,
  type Brand,
  type Credential,
  type OptimizePayload,
  type OptimizeResult,
  type ProductRow,
  type DryRunResponse,
  type ApplyResponse,
  ApiError,
} from "@/lib/api";

/* ------------------------------------------------------------------ */
/*  Sub-components                                                     */
/* ------------------------------------------------------------------ */

function StatusBadge({ status }: { status: string }) {
  const color: Record<string, string> = {
    queued: "bg-yellow-100 text-yellow-800",
    running: "bg-blue-100 text-blue-800",
    complete: "bg-green-100 text-green-800",
    failed: "bg-red-100 text-red-800",
  };
  return (
    <span className={`text-xs font-medium px-2 py-0.5 rounded ${color[status] ?? "bg-gray-100"}`}>
      {status}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  Risk View                                                          */
/* ------------------------------------------------------------------ */

function RiskView({ products }: { products: ProductRow[] }) {
  const largestDecreases = useMemo(
    () => [...products].sort((a, b) => a.CHANGE_PERCENT - b.CHANGE_PERCENT).slice(0, 10),
    [products],
  );

  const nearCost = useMemo(
    () => products.filter((p) => p.NEW_COVERAGE_RATE < 0.15).slice(0, 10),
    [products],
  );

  /* Histogram bins for CHANGE_PERCENT */
  const histogram = useMemo(() => {
    const bins: Record<string, number> = {
      "< -20%": 0,
      "-20% to -10%": 0,
      "-10% to 0%": 0,
      "0%": 0,
      "0% to 10%": 0,
      "10% to 20%": 0,
      "> 20%": 0,
    };
    for (const p of products) {
      const c = p.CHANGE_PERCENT * 100;
      if (c < -20) bins["< -20%"]++;
      else if (c < -10) bins["-20% to -10%"]++;
      else if (c < 0) bins["-10% to 0%"]++;
      else if (c === 0) bins["0%"]++;
      else if (c < 10) bins["0% to 10%"]++;
      else if (c < 20) bins["10% to 20%"]++;
      else bins["> 20%"]++;
    }
    return bins;
  }, [products]);

  const maxBin = Math.max(...Object.values(histogram), 1);

  return (
    <div className="space-y-6">
      {/* Histogram */}
      <div>
        <h3 className="font-semibold text-sm mb-2">Price Change Distribution</h3>
        <div className="space-y-1">
          {Object.entries(histogram).map(([label, count]) => (
            <div key={label} className="flex items-center gap-2 text-xs">
              <span className="w-28 text-right text-gray-500">{label}</span>
              <div className="flex-1 bg-gray-100 rounded h-4">
                <div
                  className="bg-blue-500 h-4 rounded"
                  style={{ width: `${(count / maxBin) * 100}%` }}
                />
              </div>
              <span className="w-8 text-gray-600">{count}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Largest decreases */}
      <div>
        <h3 className="font-semibold text-sm mb-2">Largest Price Decreases</h3>
        {largestDecreases.length === 0 ? (
          <p className="text-xs text-gray-400">None</p>
        ) : (
          <MiniTable rows={largestDecreases} />
        )}
      </div>

      {/* Near-cost */}
      <div>
        <h3 className="font-semibold text-sm mb-2">Near-Cost (coverage &lt; 15%)</h3>
        {nearCost.length === 0 ? (
          <p className="text-xs text-gray-400">None</p>
        ) : (
          <MiniTable rows={nearCost} />
        )}
      </div>
    </div>
  );
}

function MiniTable({ rows }: { rows: ProductRow[] }) {
  return (
    <div className="overflow-x-auto">
      <table aria-label="Risk view products" className="w-full text-xs">
        <thead>
          <tr className="text-left text-gray-500 border-b">
            <th className="pb-1 pr-2">Product</th>
            <th className="pb-1 pr-2">Name</th>
            <th className="pb-1 pr-2 text-right">Old Price</th>
            <th className="pb-1 pr-2 text-right">New Price</th>
            <th className="pb-1 text-right">Change %</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.PRODUCT_NUMBER + (r.VARIANT_ITEMNUMBER ?? "")} className="border-b last:border-0">
              <td className="py-1 pr-2 font-mono">{r.PRODUCT_NUMBER}</td>
              <td className="py-1 pr-2 truncate max-w-[200px]">{r.PRODUCT_NAME}</td>
              <td className="py-1 pr-2 text-right">{r.PRICE_EX_VAT.toFixed(2)}</td>
              <td className="py-1 pr-2 text-right">{r.SUGGESTED_PRICE_EX_VAT.toFixed(2)}</td>
              <td
                className={`py-1 text-right font-medium ${r.CHANGE_PERCENT < 0 ? "text-red-600" : "text-green-600"}`}
              >
                {(r.CHANGE_PERCENT * 100).toFixed(1)}%
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main Optimizer page                                                */
/* ------------------------------------------------------------------ */

type Tab = "all" | "adjusted" | "risk" | "apply";

function OptimizerContent() {
  const { token } = useAuth();

  /* ---- data state ---- */
  const [credentials, setCredentials] = useState<Credential[]>([]);
  const [selectedCred, setSelectedCred] = useState<string>("");
  const [brands, setBrands] = useState<Brand[]>([]);
  const [selectedBrands, setSelectedBrands] = useState<string[]>([]);
  const [onlyActive, setOnlyActive] = useState(true);
  const [minCoverage, setMinCoverage] = useState(50);
  const [beautifyDigit, setBeautifyDigit] = useState<number | null>(9);
  const [includeBuyPrice, setIncludeBuyPrice] = useState(true);

  /* ---- job state ---- */
  const [jobId, setJobId] = useState<string | null>(null);
  const [jobStatus, setJobStatus] = useState<string | null>(null);
  const [result, setResult] = useState<OptimizeResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  /* ---- apply state ---- */
  const [dryRunResult, setDryRunResult] = useState<DryRunResponse | null>(null);
  const [applyResult, setApplyResult] = useState<ApplyResponse | null>(null);
  const [confirmText, setConfirmText] = useState("");
  const [applyLoading, setApplyLoading] = useState(false);

  /* ---- UI ---- */
  const [tab, setTab] = useState<Tab>("all");
  const [filter, setFilter] = useState("");

  /* Load credentials on mount */
  useEffect(() => {
    if (!token) return;
    listCredentials(token).then((c) => {
      setCredentials(c);
      if (c.length > 0) setSelectedCred(c[0].id);
    }).catch((e) => setError(e instanceof Error ? e.message : "Failed to load credentials"));
  }, [token]);

  /* Load brands when a credential is selected */
  useEffect(() => {
    if (!token || !selectedCred) return;
    listBrands(token).then(setBrands).catch((e) => setError(e instanceof Error ? e.message : "Failed to load brands"));
  }, [token, selectedCred]);

  /* Poll job status */
  useEffect(() => {
    if (!token || !jobId) return;
    const interval = setInterval(async () => {
      try {
        const s = await getJobStatus(token, jobId);
        setJobStatus(s.status);
        if (s.status === "complete" && s.result) {
          setResult(s.result);
          setJobId(null);
          setLoading(false);
        } else if (s.status === "failed") {
          setError(s.error ?? "Job failed");
          setJobId(null);
          setLoading(false);
        }
      } catch {
        /* keep polling */
      }
    }, 2000);
    return () => clearInterval(interval);
  }, [token, jobId]);

  /* ---- actions ---- */
  const buildPayload = useCallback((): OptimizePayload => ({
    credential_id: selectedCred || undefined,
    brand_filter: selectedBrands.length > 0 ? selectedBrands : undefined,
    only_active: onlyActive,
    min_coverage_rate: minCoverage / 100,
    beautify_digit: beautifyDigit,
    include_buy_price: includeBuyPrice,
  }), [selectedCred, selectedBrands, onlyActive, minCoverage, beautifyDigit, includeBuyPrice]);

  async function runOptimize() {
    if (!token) return;
    setError(null);
    setResult(null);
    setDryRunResult(null);
    setApplyResult(null);
    setLoading(true);
    try {
      const res = await enqueueOptimize(token, buildPayload());
      setJobId(res.job_id);
      setJobStatus("queued");
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Failed to start optimisation");
      setLoading(false);
    }
  }

  async function runDryRun() {
    if (!token) return;
    setError(null);
    setApplyResult(null);
    setApplyLoading(true);
    try {
      const res = await dryRun(token, buildPayload());
      setDryRunResult(res);
      setTab("apply");
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Dry-run failed");
    } finally {
      setApplyLoading(false);
    }
  }

  async function runApply() {
    if (!token || !dryRunResult) return;
    setApplyLoading(true);
    setError(null);
    try {
      const res = await applyPrices(token, dryRunResult.batch_id, true);
      setApplyResult(res);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Apply failed");
    } finally {
      setApplyLoading(false);
    }
  }

  /* ---- CSV export ---- */
  function exportCSV() {
    if (!result) return;
    const rows = result.products;
    const headers = [
      "PRODUCT_NUMBER",
      "PRODUCT_NAME",
      "BRAND_NAME",
      "PRICE_EX_VAT",
      "SUGGESTED_PRICE_EX_VAT",
      "CURRENT_COVERAGE_RATE",
      "NEW_COVERAGE_RATE",
      "CHANGE_PERCENT",
    ];
    const csv = [
      headers.join(","),
      ...rows.map((r) =>
        headers
          .map((h) => {
            const v = r[h as keyof ProductRow];
            if (typeof v === "string" && v.includes(",")) return `"${v}"`;
            return v ?? "";
          })
          .join(","),
      ),
    ].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "optimization_results.csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  /* ---- filtered data ---- */
  const filteredProducts = useMemo(() => {
    if (!result) return [];
    let list = result.products;
    if (tab === "adjusted") list = list.filter((p) => p.CHANGE_PERCENT !== 0);
    if (filter) {
      const lc = filter.toLowerCase();
      list = list.filter(
        (p) =>
          p.PRODUCT_NUMBER.toLowerCase().includes(lc) ||
          p.PRODUCT_NAME.toLowerCase().includes(lc) ||
          (p.BRAND_NAME ?? "").toLowerCase().includes(lc),
      );
    }
    return list;
  }, [result, tab, filter]);

  return (
    <PageShell title="Price Optimizer">
      {/* ---- Controls ---- */}
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
          {/* Credential selector */}
          <div>
            <label htmlFor="opt-credential" className="block text-xs font-medium text-gray-500 mb-1">Credential Profile</label>
            <select
              id="opt-credential"
              value={selectedCred}
              onChange={(e) => setSelectedCred(e.target.value)}
              className="w-full border rounded px-2 py-1.5 text-sm"
            >
              <option value="">— select —</option>
              {credentials.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.label} ({c.api_username})
                </option>
              ))}
            </select>
          </div>

          {/* Brand filter */}
          <div>
            <label htmlFor="opt-brand" className="block text-xs font-medium text-gray-500 mb-1">Brand Filter</label>
            <select
              id="opt-brand"
              multiple
              value={selectedBrands}
              onChange={(e) =>
                setSelectedBrands(Array.from(e.target.selectedOptions, (o) => o.value))
              }
              className="w-full border rounded px-2 py-1.5 text-sm h-20"
            >
              {brands.map((b) => (
                <option key={b.id} value={b.name}>
                  {b.name}
                </option>
              ))}
            </select>
          </div>

          {/* Min coverage */}
          <div>
            <label htmlFor="opt-min-coverage" className="block text-xs font-medium text-gray-500 mb-1">
              Min Coverage Rate (%)
            </label>
            <input
              id="opt-min-coverage"
              type="number"
              min={0}
              max={100}
              value={minCoverage}
              onChange={(e) => setMinCoverage(Number(e.target.value))}
              className="w-full border rounded px-2 py-1.5 text-sm"
            />
          </div>

          {/* Beautify digit */}
          <div>
            <label htmlFor="opt-beautify" className="block text-xs font-medium text-gray-500 mb-1">Beautify Digit</label>
            <select
              id="opt-beautify"
              value={beautifyDigit ?? ""}
              onChange={(e) =>
                setBeautifyDigit(e.target.value === "" ? null : Number(e.target.value))
              }
              className="w-full border rounded px-2 py-1.5 text-sm"
            >
              <option value="">None</option>
              <option value="9">9</option>
              <option value="5">5</option>
              <option value="0">0</option>
            </select>
          </div>
        </div>

        <div className="flex items-center gap-4 mb-4">
          <label className="flex items-center gap-1.5 text-sm">
            <input
              type="checkbox"
              checked={onlyActive}
              onChange={(e) => setOnlyActive(e.target.checked)}
            />
            Only active products
          </label>
          <label className="flex items-center gap-1.5 text-sm">
            <input
              type="checkbox"
              checked={includeBuyPrice}
              onChange={(e) => setIncludeBuyPrice(e.target.checked)}
            />
            Include BUY_PRICE
          </label>
        </div>

        <div className="flex gap-3">
          <button
            onClick={runOptimize}
            disabled={loading || !selectedCred}
            className="bg-blue-600 text-white px-4 py-2 rounded text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
          >
            {loading ? "Running…" : "Run Optimisation"}
          </button>
          {result && (
            <>
              <button
                onClick={runDryRun}
                disabled={applyLoading}
                className="bg-yellow-500 text-white px-4 py-2 rounded text-sm font-medium hover:bg-yellow-600 disabled:opacity-50"
              >
                Dry-Run Preview
              </button>
              <button
                onClick={exportCSV}
                className="bg-gray-200 text-gray-800 px-4 py-2 rounded text-sm font-medium hover:bg-gray-300"
              >
                Export CSV
              </button>
            </>
          )}
        </div>
      </div>

      {/* ---- Status / Error ---- */}
      {loading && jobStatus && (
        <div className="bg-blue-50 border border-blue-200 rounded p-3 mb-4 flex items-center gap-2">
          <span className="animate-spin text-blue-500">⏳</span>
          <span className="text-sm">
            Job <code className="text-xs">{jobId}</code> — <StatusBadge status={jobStatus} />
          </span>
        </div>
      )}

      {error && (
        <div role="alert" aria-live="polite" className="bg-red-50 border border-red-200 text-red-700 rounded p-3 mb-4 text-sm">
          {error}
        </div>
      )}

      {/* ---- Results ---- */}
      {result && (
        <>
          {/* Summary */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <Metric label="Total Products" value={result.summary.total_products} />
            <Metric
              label="Adjusted"
              value={`${result.summary.adjusted_count} (${((result.summary.adjusted_count / Math.max(result.summary.total_products, 1)) * 100).toFixed(0)}%)`}
            />
            <Metric
              label="Avg Coverage Before"
              value={`${(result.summary.avg_coverage_before * 100).toFixed(1)}%`}
            />
            <Metric
              label="Avg Coverage After"
              value={`${(result.summary.avg_coverage_after * 100).toFixed(1)}%`}
            />
          </div>

          {/* Tabs */}
          <div className="flex gap-1 mb-4">
            {(
              [
                ["all", "All Products"],
                ["adjusted", "Adjusted Only"],
                ["risk", "Risk View"],
                ["apply", "Apply"],
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
            {/* Search filter */}
            {(tab === "all" || tab === "adjusted") && (
              <>
                <label htmlFor="opt-product-filter" className="sr-only">Filter products</label>
                <input
                  id="opt-product-filter"
                  type="text"
                  placeholder="Filter by product number, name, or brand…"
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                  className="w-full border rounded px-3 py-2 text-sm mb-4"
                />
              </>
            )}

            {/* Tab content */}
            {tab === "risk" ? (
              <RiskView products={result.products} />
            ) : tab === "apply" ? (
              <ApplySection
                dryRunResult={dryRunResult}
                applyResult={applyResult}
                confirmText={confirmText}
                setConfirmText={setConfirmText}
                applyLoading={applyLoading}
                onApply={runApply}
                error={error}
              />
            ) : (
              <ProductTable rows={filteredProducts} />
            )}
          </div>
        </>
      )}
    </PageShell>
  );
}

/* ---- Apply Section ---- */

function ApplySection({
  dryRunResult,
  applyResult,
  confirmText,
  setConfirmText,
  applyLoading,
  onApply,
  error,
}: {
  dryRunResult: DryRunResponse | null;
  applyResult: ApplyResponse | null;
  confirmText: string;
  setConfirmText: (v: string) => void;
  applyLoading: boolean;
  onApply: () => void;
  error: string | null;
}) {
  if (applyResult) {
    return (
      <div className="space-y-3">
        <div className="bg-green-50 border border-green-200 rounded p-4">
          <p className="font-semibold text-green-800">
            ✅ Applied {applyResult.applied_count} price changes
          </p>
          <p className="text-xs text-green-600 mt-1">
            Batch: {applyResult.batch_id} · {applyResult.started_at} → {applyResult.finished_at}
          </p>
        </div>
        {applyResult.failed.length > 0 && (
          <div className="bg-yellow-50 border border-yellow-200 rounded p-4">
            <p className="font-semibold text-yellow-800 mb-2">
              ⚠️ {applyResult.failed.length} rows failed
            </p>
            <ul className="text-xs space-y-1">
              {applyResult.failed.map((f) => (
                <li key={f.product_number}>
                  <code>{f.product_number}</code> — {f.reason}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    );
  }

  if (!dryRunResult) {
    return (
      <p className="text-sm text-gray-500">
        Click &quot;Dry-Run Preview&quot; above to see what changes would be applied.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Metric label="Total Changes" value={dryRunResult.summary.total} />
        <Metric label="Increases" value={dryRunResult.summary.increases} />
        <Metric label="Decreases" value={dryRunResult.summary.decreases} />
        <Metric label="Unchanged" value={dryRunResult.summary.unchanged} />
      </div>

      <div className="overflow-x-auto max-h-64">
        <table aria-label="Dry run price changes" className="w-full text-xs">
          <thead>
            <tr className="text-left text-gray-500 border-b">
              <th className="pb-1 pr-2">Product</th>
              <th className="pb-1 pr-2">Name</th>
              <th className="pb-1 pr-2 text-right">Old Price</th>
              <th className="pb-1 pr-2 text-right">New Price</th>
              <th className="pb-1 text-right">Change %</th>
            </tr>
          </thead>
          <tbody>
            {dryRunResult.changes.slice(0, 100).map((c) => (
              <tr key={c.product_number} className="border-b last:border-0">
                <td className="py-1 pr-2 font-mono">{c.product_number}</td>
                <td className="py-1 pr-2 truncate max-w-[200px]">{c.product_name}</td>
                <td className="py-1 pr-2 text-right">{c.old_price.toFixed(2)}</td>
                <td className="py-1 pr-2 text-right">{c.new_price.toFixed(2)}</td>
                <td
                  className={`py-1 text-right font-medium ${c.change_percent < 0 ? "text-red-600" : "text-green-600"}`}
                >
                  {(c.change_percent * 100).toFixed(1)}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {error && (
        <div role="alert" aria-live="polite" className="bg-red-50 border border-red-200 text-red-700 rounded p-3 text-sm">
          {error}
        </div>
      )}

      <div className="border-t pt-4">
        <p className="text-sm text-gray-600 mb-2">
          Type <strong>APPLY</strong> to confirm writing {dryRunResult.summary.total} price changes to DanDomain:
        </p>
        <div className="flex gap-3">
          <div>
            <label htmlFor="apply-confirm" className="sr-only">Confirmation text</label>
            <input
              id="apply-confirm"
              type="text"
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              placeholder="Type APPLY"
              className="border rounded px-3 py-1.5 text-sm w-32"
            />
          </div>
          <button
            onClick={onApply}
            disabled={confirmText !== "APPLY" || applyLoading}
            className="bg-red-600 text-white px-4 py-1.5 rounded text-sm font-medium hover:bg-red-700 disabled:opacity-50"
          >
            {applyLoading ? "Applying…" : "Apply to Shop"}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ---- Product Table ---- */

function ProductTable({ rows }: { rows: ProductRow[] }) {
  if (rows.length === 0) {
    return <p className="text-sm text-gray-400">No products to display.</p>;
  }

  return (
    <div className="overflow-x-auto max-h-[500px]">
      <table aria-label="Optimised products" className="w-full text-xs">
        <thead className="sticky top-0 bg-white">
          <tr className="text-left text-gray-500 border-b">
            <th className="pb-1 pr-2">Product #</th>
            <th className="pb-1 pr-2">Name</th>
            <th className="pb-1 pr-2">Brand</th>
            <th className="pb-1 pr-2 text-right">Buy Price</th>
            <th className="pb-1 pr-2 text-right">Current Price</th>
            <th className="pb-1 pr-2 text-right">Suggested Price</th>
            <th className="pb-1 pr-2 text-right">Coverage Before</th>
            <th className="pb-1 pr-2 text-right">Coverage After</th>
            <th className="pb-1 text-right">Change %</th>
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 500).map((r, i) => (
            <tr
              key={r.PRODUCT_NUMBER + (r.VARIANT_ITEMNUMBER ?? "") + i}
              className="border-b last:border-0 hover:bg-gray-50"
            >
              <td className="py-1 pr-2 font-mono">{r.PRODUCT_NUMBER}</td>
              <td className="py-1 pr-2 truncate max-w-[200px]">{r.PRODUCT_NAME}</td>
              <td className="py-1 pr-2 text-gray-500">{r.BRAND_NAME ?? "—"}</td>
              <td className="py-1 pr-2 text-right">{r.BUY_PRICE?.toFixed(2) ?? "—"}</td>
              <td className="py-1 pr-2 text-right">{r.PRICE_EX_VAT.toFixed(2)}</td>
              <td className="py-1 pr-2 text-right">{r.SUGGESTED_PRICE_EX_VAT.toFixed(2)}</td>
              <td className="py-1 pr-2 text-right">{(r.CURRENT_COVERAGE_RATE * 100).toFixed(1)}%</td>
              <td className="py-1 pr-2 text-right">{(r.NEW_COVERAGE_RATE * 100).toFixed(1)}%</td>
              <td
                className={`py-1 text-right font-medium ${
                  r.CHANGE_PERCENT < 0
                    ? "text-red-600"
                    : r.CHANGE_PERCENT > 0
                      ? "text-green-600"
                      : "text-gray-400"
                }`}
              >
                {(r.CHANGE_PERCENT * 100).toFixed(1)}%
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 500 && (
        <p className="text-xs text-gray-400 mt-2">Showing first 500 of {rows.length} products</p>
      )}
    </div>
  );
}

/* ---- Metric card ---- */

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="bg-gray-50 rounded p-3">
      <p className="text-xs text-gray-500">{label}</p>
      <p className="text-lg font-semibold">{value}</p>
    </div>
  );
}

/* ---- Page wrapper ---- */

export default function OptimizerPage() {
  return (
    <RequireAuth>
      <OptimizerContent />
    </RequireAuth>
  );
}
