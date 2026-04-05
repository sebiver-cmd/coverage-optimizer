"use client";

import { useState, useMemo } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useAuth } from "@/lib/auth-context";
import { signup, ApiError } from "@/lib/api";

/* ------------------------------------------------------------------ */
/*  Password strength helper                                           */
/* ------------------------------------------------------------------ */

type Strength = "weak" | "medium" | "strong";

function getPasswordStrength(pw: string): Strength {
  if (pw.length < 8) return "weak";
  const hasUpper = /[A-Z]/.test(pw);
  const hasLower = /[a-z]/.test(pw);
  const hasDigit = /\d/.test(pw);
  const passed = [hasUpper, hasLower, hasDigit].filter(Boolean).length;
  if (passed === 3) return "strong";
  if (passed >= 2) return "medium";
  return "weak";
}

const strengthLabel: Record<Strength, string> = {
  weak: "Weak",
  medium: "Medium",
  strong: "Strong",
};

const strengthColor: Record<Strength, string> = {
  weak: "text-red-600",
  medium: "text-yellow-600",
  strong: "text-green-600",
};

const strengthBar: Record<Strength, string> = {
  weak: "bg-red-500 w-1/3",
  medium: "bg-yellow-500 w-2/3",
  strong: "bg-green-500 w-full",
};

export default function SignupPage() {
  const { setToken } = useAuth();
  const router = useRouter();
  const [tenantName, setTenantName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const strength = useMemo(() => getPasswordStrength(password), [password]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await signup(tenantName, email, password);
      setToken(res.access_token);
      router.push("/dashboard");
    } catch (err) {
      if (err instanceof ApiError) setError(err.detail);
      else setError("Signup failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex items-center justify-center min-h-screen bg-gray-50">
      <div className="w-full max-w-md bg-white rounded-lg shadow-md p-8">
        <h1 className="text-2xl font-bold text-center mb-6">Create a new tenant</h1>

        {error && (
          <div role="alert" aria-live="polite" className="bg-red-50 text-red-700 border border-red-200 rounded p-3 mb-4 text-sm">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="tenant" className="block text-sm font-medium text-gray-700 mb-1">
              Tenant name
            </label>
            <input
              id="tenant"
              type="text"
              required
              value={tenantName}
              onChange={(e) => setTenantName(e.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="My Company"
            />
          </div>

          <div>
            <label htmlFor="email" className="block text-sm font-medium text-gray-700 mb-1">
              Email
            </label>
            <input
              id="email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="you@example.com"
            />
          </div>

          <div>
            <label htmlFor="password" className="block text-sm font-medium text-gray-700 mb-1">
              Password (min 8 characters)
            </label>
            <input
              id="password"
              type="password"
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="••••••••"
            />
            {password.length > 0 && (
              <div className="mt-1.5 space-y-1">
                <div className="w-full bg-gray-200 rounded-full h-1.5">
                  <div className={`h-1.5 rounded-full transition-all ${strengthBar[strength]}`} />
                </div>
                <p className={`text-xs font-medium ${strengthColor[strength]}`}>
                  Password strength: {strengthLabel[strength]}
                </p>
              </div>
            )}
          </div>

          <button
            type="submit"
            disabled={loading || strength === "weak"}
            className="w-full bg-blue-600 text-white rounded-md py-2 text-sm font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {loading ? "Creating…" : "Create tenant"}
          </button>
        </form>

        <p className="mt-4 text-center text-sm text-gray-500">
          Already have an account?{" "}
          <Link href="/login" className="text-blue-600 hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
