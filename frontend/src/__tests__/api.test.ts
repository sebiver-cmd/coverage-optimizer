/**
 * Unit tests for the API client (src/lib/api.ts).
 *
 * We mock global `fetch` and verify request construction,
 * header generation, and error handling.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

/* ------------------------------------------------------------------ */
/*  We need to test internal helpers, so we re-export via the module.  */
/*  authHeaders is not exported, so we test it indirectly via calls.   */
/* ------------------------------------------------------------------ */

import { ApiError, login, signup, getMe, getHealth } from "@/lib/api";

/* ------------------------------------------------------------------ */
/*  Mock fetch globally                                                */
/* ------------------------------------------------------------------ */

const mockFetch = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", mockFetch);
  mockFetch.mockReset();
});

/* ------------------------------------------------------------------ */
/*  ApiError                                                           */
/* ------------------------------------------------------------------ */

describe("ApiError", () => {
  it("stores status and detail", () => {
    const err = new ApiError(401, "Unauthorized");
    expect(err.status).toBe(401);
    expect(err.detail).toBe("Unauthorized");
    expect(err.message).toBe("Unauthorized");
    expect(err).toBeInstanceOf(Error);
  });
});

/* ------------------------------------------------------------------ */
/*  Auth helpers (login, signup, getMe)                                */
/* ------------------------------------------------------------------ */

describe("login()", () => {
  it("sends POST /auth/login with JSON body and returns token", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ access_token: "tok123", token_type: "bearer" }),
    });

    const res = await login("a@b.com", "secret");
    expect(res.access_token).toBe("tok123");

    // Verify the request
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toContain("/auth/login");
    expect(opts.method).toBe("POST");
    expect(JSON.parse(opts.body)).toEqual({
      email: "a@b.com",
      password: "secret",
    });
    // No Authorization header for login
    expect(opts.headers["Authorization"]).toBeUndefined();
    expect(opts.headers["Content-Type"]).toBe("application/json");
  });

  it("throws ApiError on non-ok response", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 401,
      statusText: "Unauthorized",
      json: async () => ({ detail: "Bad credentials" }),
    });

    try {
      await login("a@b.com", "wrong");
      expect.unreachable("should have thrown");
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).status).toBe(401);
      expect((e as ApiError).detail).toBe("Bad credentials");
    }
  });
});

describe("signup()", () => {
  it("sends POST /auth/signup with tenant_name, email, password", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ access_token: "new_tok", token_type: "bearer" }),
    });

    const res = await signup("Acme", "a@b.com", "pass123");
    expect(res.access_token).toBe("new_tok");

    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toContain("/auth/signup");
    expect(JSON.parse(opts.body)).toEqual({
      tenant_name: "Acme",
      email: "a@b.com",
      password: "pass123",
    });
  });
});

describe("getMe()", () => {
  it("sends GET /auth/me with Authorization header", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        id: "u1",
        tenant_id: "t1",
        email: "a@b.com",
        role: "owner",
        created_at: "2025-01-01T00:00:00Z",
      }),
    });

    const user = await getMe("mytoken");
    expect(user.email).toBe("a@b.com");

    const [, opts] = mockFetch.mock.calls[0];
    expect(opts.headers["Authorization"]).toBe("Bearer mytoken");
  });
});

/* ------------------------------------------------------------------ */
/*  Health (public, no auth header)                                    */
/* ------------------------------------------------------------------ */

describe("getHealth()", () => {
  it("calls /health without auth and returns response", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        status: "ok",
        version: "1.0",
        apply_enabled: false,
        timestamp: "2025-01-01T00:00:00Z",
      }),
    });

    const health = await getHealth();
    expect(health.status).toBe("ok");

    const [url] = mockFetch.mock.calls[0];
    expect(url).toContain("/health");
  });
});

/* ------------------------------------------------------------------ */
/*  Error edge cases                                                   */
/* ------------------------------------------------------------------ */

describe("request error handling", () => {
  it("falls back to statusText when JSON parse fails", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
      json: async () => {
        throw new Error("not JSON");
      },
    });

    await expect(login("a@b.com", "x")).rejects.toThrow("Internal Server Error");
  });
});
