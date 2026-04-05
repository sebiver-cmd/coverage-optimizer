"use client";

import { useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";

/* ------------------------------------------------------------------ */
/*  VirtualTable — generic virtualized table (Task 13.2)               */
/* ------------------------------------------------------------------ */

export interface ColumnDef<T> {
  /** Header text. */
  header: string;
  /** Header CSS classes (added to <th>). */
  headerClassName?: string;
  /** Cell CSS classes (added to <td>). */
  cellClassName?: string;
  /** Render the cell content for a given row. */
  render: (row: T, index: number) => React.ReactNode;
}

export interface VirtualTableProps<T> {
  /** Array of row data objects. */
  rows: T[];
  /** Column definitions. */
  columns: ColumnDef<T>[];
  /** Estimated height of each row in px (default 28). */
  estimateRowHeight?: number;
  /** Maximum visible height in px (default 600). */
  maxHeight?: number;
  /** Unique key extractor per row. Falls back to index if not provided. */
  getRowKey?: (row: T, index: number) => string | number;
  /** ARIA label for the table. */
  ariaLabel?: string;
  /** Message shown when rows is empty. */
  emptyMessage?: string;
}

export default function VirtualTable<T>({
  rows,
  columns,
  estimateRowHeight = 28,
  maxHeight = 600,
  getRowKey,
  ariaLabel,
  emptyMessage = "No data",
}: VirtualTableProps<T>) {
  const parentRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => estimateRowHeight,
    overscan: 20,
  });

  if (rows.length === 0) {
    return (
      <table aria-label={ariaLabel} className="w-full text-xs">
        <thead>
          <tr className="text-left text-gray-500 border-b">
            {columns.map((col, ci) => (
              <th key={ci} className={`pb-1 pr-2 ${col.headerClassName ?? ""}`}>
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr>
            <td colSpan={columns.length} className="py-4 text-center text-gray-400">
              {emptyMessage}
            </td>
          </tr>
        </tbody>
      </table>
    );
  }

  return (
    <div
      ref={parentRef}
      className="overflow-auto"
      style={{ maxHeight }}
    >
      <table aria-label={ariaLabel} className="w-full text-xs">
        <thead className="sticky top-0 bg-white z-10">
          <tr className="text-left text-gray-500 border-b">
            {columns.map((col, ci) => (
              <th key={ci} className={`pb-1 pr-2 ${col.headerClassName ?? ""}`}>
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {/* Top spacer */}
          {virtualizer.getVirtualItems().length > 0 && (
            <tr aria-hidden="true">
              <td
                colSpan={columns.length}
                style={{ height: virtualizer.getVirtualItems()[0]?.start ?? 0, padding: 0, border: "none" }}
              />
            </tr>
          )}
          {virtualizer.getVirtualItems().map((vRow) => {
            const row = rows[vRow.index];
            const key = getRowKey ? getRowKey(row, vRow.index) : vRow.index;
            return (
              <tr
                key={key}
                data-index={vRow.index}
                className="border-b last:border-0 hover:bg-gray-50"
              >
                {columns.map((col, ci) => (
                  <td key={ci} className={`py-1 pr-2 ${col.cellClassName ?? ""}`}>
                    {col.render(row, vRow.index)}
                  </td>
                ))}
              </tr>
            );
          })}
          {/* Bottom spacer */}
          {virtualizer.getVirtualItems().length > 0 && (
            <tr aria-hidden="true">
              <td
                colSpan={columns.length}
                style={{
                  height:
                    virtualizer.getTotalSize() -
                    (virtualizer.getVirtualItems().at(-1)?.end ?? 0),
                  padding: 0,
                  border: "none",
                }}
              />
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
