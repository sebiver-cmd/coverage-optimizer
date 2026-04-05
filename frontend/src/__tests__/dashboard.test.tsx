/**
 * Tests for the Dashboard page (src/app/dashboard/page.tsx).
 *
 * We mock auth context, RequireAuth, and API calls to test:
 * - Loading state
 * - Data display after API response
 * - Error display on failure
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import DashboardPage from "@/app/dashboard/page";

/* ------------------------------------------------------------------ */
/*  Mocks                                                              */
/* ------------------------------------------------------------------ */

const mockToken = "test_token";

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({
    token: mockToken,
    user: {
      id: "u1",
      tenant_id: "t1",
      email: "user@test.com",
      role: "owner",
      created_at: "2025-01-01T00:00:00Z",
    },
    ready: true,
    setToken: vi.fn(),
    logout: vi.fn(),
  }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("@/lib/api", () => ({
  getTenantPlan: vi.fn(),
  getUsage: vi.fn(),
  listCredentials: vi.fn(),
}));

import { getTenantPlan, getUsage, listCredentials } from "@/lib/api";
const mockGetPlan = vi.mocked(getTenantPlan);
const mockGetUsage = vi.mocked(getUsage);
const mockListCreds = vi.mocked(listCredentials);

beforeEach(() => {
  mockGetPlan.mockReset();
  mockGetUsage.mockReset();
  mockListCreds.mockReset();
});

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe("DashboardPage", () => {
  it("shows loading state initially before data resolves", () => {
    // Never-resolving promises to keep loading state
    mockGetPlan.mockReturnValue(new Promise(() => {}));
    mockGetUsage.mockReturnValue(new Promise(() => {}));
    mockListCreds.mockReturnValue(new Promise(() => {}));

    render(<DashboardPage />);

    expect(screen.getByText("Dashboard")).toBeInTheDocument();
    expect(screen.getAllByText("Loading…").length).toBeGreaterThan(0);
  });

  it("displays plan, usage, and user data after API calls resolve", async () => {
    mockGetPlan.mockResolvedValueOnce({
      plan: "professional",
      limits: {
        name: "professional",
        daily_optimize_jobs_limit: 50,
        daily_apply_limit: 10,
        daily_optimize_sync_limit: 5,
      },
    });
    mockGetUsage.mockResolvedValueOnce({
      daily_optimize_jobs: 3,
      daily_apply: 1,
      daily_optimize_sync: 0,
      limits: {
        name: "professional",
        daily_optimize_jobs_limit: 50,
        daily_apply_limit: 10,
        daily_optimize_sync_limit: 5,
      },
    });
    mockListCreds.mockResolvedValueOnce([
      {
        id: "c1",
        label: "Main Store",
        api_username: "shop_user",
        created_at: "2025-06-01T00:00:00Z",
      },
    ]);

    render(<DashboardPage />);

    await waitFor(() => {
      expect(screen.getByText("professional")).toBeInTheDocument();
    });
    expect(screen.getByText("user@test.com")).toBeInTheDocument();
    expect(screen.getByText("owner")).toBeInTheDocument();
    expect(screen.getByText("Main Store")).toBeInTheDocument();
    expect(screen.getByText("shop_user")).toBeInTheDocument();
  });

  it("displays empty credentials message when none exist", async () => {
    mockGetPlan.mockResolvedValueOnce({
      plan: "free",
      limits: {
        name: "free",
        daily_optimize_jobs_limit: 5,
        daily_apply_limit: 1,
        daily_optimize_sync_limit: 1,
      },
    });
    mockGetUsage.mockResolvedValueOnce({
      daily_optimize_jobs: 0,
      daily_apply: 0,
      daily_optimize_sync: 0,
      limits: {
        name: "free",
        daily_optimize_jobs_limit: 5,
        daily_apply_limit: 1,
        daily_optimize_sync_limit: 1,
      },
    });
    mockListCreds.mockResolvedValueOnce([]);

    render(<DashboardPage />);

    await waitFor(() => {
      expect(screen.getByText(/no credentials stored/i)).toBeInTheDocument();
    });
  });

  it("shows error alert when API call fails", async () => {
    mockGetPlan.mockRejectedValueOnce(new Error("Network failure"));
    mockGetUsage.mockResolvedValueOnce({
      daily_optimize_jobs: 0,
      daily_apply: 0,
      daily_optimize_sync: 0,
      limits: {
        name: "free",
        daily_optimize_jobs_limit: 5,
        daily_apply_limit: 1,
        daily_optimize_sync_limit: 1,
      },
    });
    mockListCreds.mockResolvedValueOnce([]);

    render(<DashboardPage />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.getByRole("alert")).toHaveTextContent(/network failure/i);
  });
});
