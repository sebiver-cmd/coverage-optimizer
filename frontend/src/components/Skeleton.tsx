"use client";

/* ------------------------------------------------------------------ */
/*  Reusable skeleton placeholders (Task 13.1)                         */
/* ------------------------------------------------------------------ */

/**
 * Base animated pulse block.  All skeletons extend this with
 * specific width/height/layout.
 */
function SkeletonBlock({ className = "" }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={`animate-pulse rounded bg-gray-200 ${className}`}
    />
  );
}

/* ------------------------------------------------------------------ */
/*  SkeletonText                                                       */
/* ------------------------------------------------------------------ */

export function SkeletonText({
  lines = 1,
  className = "",
}: {
  lines?: number;
  className?: string;
}) {
  return (
    <div className={`space-y-2 ${className}`} aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => (
        <SkeletonBlock
          key={i}
          className={`h-3 ${i === lines - 1 && lines > 1 ? "w-2/3" : "w-full"}`}
        />
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  SkeletonCard                                                       */
/* ------------------------------------------------------------------ */

export function SkeletonCard({ className = "" }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={`border rounded-lg p-4 space-y-3 ${className}`}
    >
      <SkeletonBlock className="h-4 w-1/3" />
      <SkeletonBlock className="h-3 w-full" />
      <SkeletonBlock className="h-3 w-2/3" />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  SkeletonTable                                                      */
/* ------------------------------------------------------------------ */

export function SkeletonTable({
  rows = 5,
  cols = 4,
  className = "",
}: {
  rows?: number;
  cols?: number;
  className?: string;
}) {
  return (
    <div className={`space-y-2 ${className}`} aria-hidden="true">
      {/* Header row */}
      <div className="flex gap-3">
        {Array.from({ length: cols }, (_, c) => (
          <SkeletonBlock key={`h-${c}`} className="h-3 flex-1" />
        ))}
      </div>
      {/* Data rows */}
      {Array.from({ length: rows }, (_, r) => (
        <div key={`r-${r}`} className="flex gap-3">
          {Array.from({ length: cols }, (_, c) => (
            <SkeletonBlock key={`r-${r}-c-${c}`} className="h-3 flex-1" />
          ))}
        </div>
      ))}
    </div>
  );
}
