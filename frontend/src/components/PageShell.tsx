"use client";

import type { ReactNode } from "react";

interface Props {
  title: string;
  children: ReactNode;
}

export default function PageShell({ title, children }: Props) {
  return (
    <main id="main-content" className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <h1 className="text-2xl font-bold mb-6">{title}</h1>
      {children}
    </main>
  );
}
