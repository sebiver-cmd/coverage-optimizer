"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/auth-context";

const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/optimizer", label: "Price Optimizer" },
  { href: "/history", label: "History" },
  { href: "/billing", label: "Billing" },
];

export default function Navbar() {
  const { user, logout } = useAuth();
  const path = usePathname();

  if (!user) return null;

  return (
    <nav aria-label="Main navigation" className="bg-gray-900 text-white px-6 py-3 flex items-center gap-6 text-sm">
      <Link href="/dashboard" className="font-bold text-lg tracking-tight mr-4">
        SB‑Optima
      </Link>

      {NAV_ITEMS.map((n) => (
        <Link
          key={n.href}
          href={n.href}
          aria-current={path.startsWith(n.href) ? "page" : undefined}
          className={`hover:text-blue-300 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 focus:ring-offset-gray-900 rounded ${
            path.startsWith(n.href) ? "text-blue-400 font-semibold" : "text-gray-300"
          }`}
        >
          {n.label}
        </Link>
      ))}

      <div className="ml-auto flex items-center gap-4">
        <a
          href="https://sboptima.dk"
          target="_blank"
          rel="noopener noreferrer"
          aria-label="Visit SB-Optima website"
          className="text-gray-500 hover:text-gray-300 text-xs"
        >
          sboptima.dk
        </a>
        <span className="text-gray-400 text-xs">
          {user.email} ({user.role})
        </span>
        <button
          onClick={logout}
          className="text-gray-400 hover:text-white text-xs underline focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 focus:ring-offset-gray-900 rounded"
        >
          Sign out
        </button>
      </div>
    </nav>
  );
}
