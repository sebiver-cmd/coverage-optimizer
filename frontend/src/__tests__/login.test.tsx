/**
 * Tests for the Login page (src/app/login/page.tsx).
 *
 * We mock the auth context and API to test:
 * - Form rendering
 * - Successful login flow
 * - Error display on failure
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LoginPage from "@/app/login/page";

/* ------------------------------------------------------------------ */
/*  Mocks                                                              */
/* ------------------------------------------------------------------ */

const mockSetToken = vi.fn();
const mockPush = vi.fn();

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({
    token: null,
    user: null,
    ready: true,
    setToken: mockSetToken,
    logout: vi.fn(),
  }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: vi.fn() }),
}));

vi.mock("@/lib/api", () => ({
  login: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number;
    detail: string;
    constructor(status: number, detail: string) {
      super(detail);
      this.status = status;
      this.detail = detail;
    }
  },
}));

import { login as apiLogin, ApiError } from "@/lib/api";
const mockLogin = vi.mocked(apiLogin);

beforeEach(() => {
  mockLogin.mockReset();
  mockSetToken.mockReset();
  mockPush.mockReset();
});

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe("LoginPage", () => {
  it("renders email and password fields and a submit button", () => {
    render(<LoginPage />);

    expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument();
  });

  it("renders a link to signup page", () => {
    render(<LoginPage />);

    const link = screen.getByRole("link", { name: /create tenant/i });
    expect(link).toHaveAttribute("href", "/signup");
  });

  it("calls login API and redirects on success", async () => {
    const user = userEvent.setup();
    mockLogin.mockResolvedValueOnce({
      access_token: "tok123",
      token_type: "bearer",
    });

    render(<LoginPage />);

    await user.type(screen.getByLabelText(/email/i), "test@example.com");
    await user.type(screen.getByLabelText(/password/i), "mypassword");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(mockLogin).toHaveBeenCalledWith("test@example.com", "mypassword");
    });
    expect(mockSetToken).toHaveBeenCalledWith("tok123");
    expect(mockPush).toHaveBeenCalledWith("/dashboard");
  });

  it("displays error message on ApiError", async () => {
    const user = userEvent.setup();
    mockLogin.mockRejectedValueOnce(new ApiError(401, "Invalid credentials"));

    render(<LoginPage />);

    await user.type(screen.getByLabelText(/email/i), "test@example.com");
    await user.type(screen.getByLabelText(/password/i), "wrong");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Invalid credentials");
    });
  });

  it("displays generic error on non-ApiError", async () => {
    const user = userEvent.setup();
    mockLogin.mockRejectedValueOnce(new Error("network"));

    render(<LoginPage />);

    await user.type(screen.getByLabelText(/email/i), "test@example.com");
    await user.type(screen.getByLabelText(/password/i), "pass");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Login failed");
    });
  });
});
