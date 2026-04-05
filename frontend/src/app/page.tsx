"use client";

import { useAuth } from "@/lib/auth-context";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

export default function Home() {
  const { token, ready } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!ready) return;
    router.replace(token ? "/dashboard" : "/login");
  }, [ready, token, router]);

  return (
    <div className="flex items-center justify-center min-h-screen">
      <p className="text-gray-400 animate-pulse">Redirecting…</p>
    </div>
  );
}
