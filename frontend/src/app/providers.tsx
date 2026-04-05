"use client";

import type { ReactNode } from "react";
import { AuthProvider } from "@/lib/auth-context";
import Navbar from "@/components/Navbar";
import ErrorBoundary from "@/components/ErrorBoundary";

export default function ClientProviders({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <Navbar />
      <ErrorBoundary>{children}</ErrorBoundary>
    </AuthProvider>
  );
}
