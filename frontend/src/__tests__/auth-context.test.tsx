/**
 * Tests for AuthProvider (src/lib/auth-context.tsx).
 *
 * We mock the API calls (getMe, refreshToken) and localStorage
 * to verify auth state management.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, act, waitFor } from "@testing-library/react";
import { AuthProvider, useAuth } from "@/lib/auth-context";

/* ------------------------------------------------------------------ */
/*  Mock api module                                                    */
/* ------------------------------------------------------------------ */

vi.mock("@/lib/api", () => ({
  getMe: vi.fn(),
  refreshToken: vi.fn(),
}));

import { getMe } from "@/lib/api";
const mockGetMe = vi.mocked(getMe);

/* ------------------------------------------------------------------ */
/*  Mock localStorage                                                  */
/* ------------------------------------------------------------------ */

const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: vi.fn((key: string) => store[key] ?? null),
    setItem: vi.fn((key: string, val: string) => {
      store[key] = val;
    }),
    removeItem: vi.fn((key: string) => {
      delete store[key];
    }),
    clear: vi.fn(() => {
      store = {};
    }),
  };
})();

beforeEach(() => {
  localStorageMock.clear();
  vi.stubGlobal("localStorage", localStorageMock);
  mockGetMe.mockReset();
});

/* ------------------------------------------------------------------ */
/*  Test consumer component                                            */
/* ------------------------------------------------------------------ */

function AuthConsumer() {
  const { token, user, ready, setToken, logout } = useAuth();
  return (
    <div>
      <span data-testid="ready">{String(ready)}</span>
      <span data-testid="token">{token ?? "null"}</span>
      <span data-testid="email">{user?.email ?? "none"}</span>
      <button onClick={() => setToken("new_tok")}>set-token</button>
      <button onClick={logout}>logout</button>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe("AuthProvider", () => {
  it("starts with ready=false, then becomes ready with no token when localStorage is empty", async () => {
    localStorageMock.getItem.mockReturnValue(null);

    render(
      <AuthProvider>
        <AuthConsumer />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("ready").textContent).toBe("true");
    });
    expect(screen.getByTestId("token").textContent).toBe("null");
    expect(screen.getByTestId("email").textContent).toBe("none");
  });

  it("hydrates from localStorage and calls getMe", async () => {
    localStorageMock.getItem.mockReturnValue("stored_tok");
    mockGetMe.mockResolvedValueOnce({
      id: "u1",
      tenant_id: "t1",
      email: "user@example.com",
      role: "owner",
      created_at: "2025-01-01T00:00:00Z",
    });

    render(
      <AuthProvider>
        <AuthConsumer />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("ready").textContent).toBe("true");
    });
    expect(screen.getByTestId("token").textContent).toBe("stored_tok");
    expect(screen.getByTestId("email").textContent).toBe("user@example.com");
    expect(mockGetMe).toHaveBeenCalledWith("stored_tok");
  });

  it("clears token when getMe rejects (expired token)", async () => {
    localStorageMock.getItem.mockReturnValue("expired_tok");
    mockGetMe.mockRejectedValueOnce(new Error("401"));

    render(
      <AuthProvider>
        <AuthConsumer />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("ready").textContent).toBe("true");
    });
    expect(screen.getByTestId("token").textContent).toBe("null");
    expect(localStorageMock.removeItem).toHaveBeenCalledWith("sb_token");
  });

  it("setToken() stores token and fetches user", async () => {
    localStorageMock.getItem.mockReturnValue(null);
    mockGetMe.mockResolvedValue({
      id: "u2",
      tenant_id: "t2",
      email: "new@example.com",
      role: "admin",
      created_at: "2025-06-01T00:00:00Z",
    });

    render(
      <AuthProvider>
        <AuthConsumer />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("ready").textContent).toBe("true");
    });

    await act(async () => {
      screen.getByText("set-token").click();
    });

    await waitFor(() => {
      expect(screen.getByTestId("email").textContent).toBe("new@example.com");
    });
    expect(localStorageMock.setItem).toHaveBeenCalledWith("sb_token", "new_tok");
  });

  it("logout() clears token and user", async () => {
    localStorageMock.getItem.mockReturnValue("tok");
    mockGetMe.mockResolvedValueOnce({
      id: "u1",
      tenant_id: "t1",
      email: "user@example.com",
      role: "viewer",
      created_at: "2025-01-01T00:00:00Z",
    });

    render(
      <AuthProvider>
        <AuthConsumer />
      </AuthProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("email").textContent).toBe("user@example.com");
    });

    await act(async () => {
      screen.getByText("logout").click();
    });

    expect(screen.getByTestId("token").textContent).toBe("null");
    expect(screen.getByTestId("email").textContent).toBe("none");
    expect(localStorageMock.removeItem).toHaveBeenCalledWith("sb_token");
  });
});

describe("useAuth outside provider", () => {
  it("throws when used outside AuthProvider", () => {
    // Suppress console.error for expected error
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});

    expect(() => {
      render(<AuthConsumer />);
    }).toThrow("useAuth must be used inside <AuthProvider>");

    spy.mockRestore();
  });
});
