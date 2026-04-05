"use client";

import type { ReactNode } from "react";
import { AuthProvider } from "@/lib/auth-context";
import Navbar from "@/components/Navbar";

export default function ClientProviders({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <Navbar />
      {children}
    </AuthProvider>
  );
}
