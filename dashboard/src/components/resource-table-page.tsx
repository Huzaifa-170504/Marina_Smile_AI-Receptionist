"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { EmptyState, ErrorState, LoadingState } from "@/components/async-state";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { useStaff } from "@/components/staff-context";
import { useClinicRealtime } from "@/hooks/use-clinic-realtime";
import { createClient } from "@/lib/supabase/browser";

export type ResourceColumn = {
  label: string;
  path: string;
  kind?: "text" | "status" | "date" | "money" | "boolean";
  currencyPath?: string;
};

export type ResourceConfig = {
  table: string;
  select: string;
  eyebrow: string;
  title: string;
  description: string;
  emptyTitle: string;
  emptyMessage: string;
  columns: ResourceColumn[];
  orderBy?: string;
  ascending?: boolean;
  realtime?: string[];
  backendPending?: string;
};

function readPath(record: Record<string, unknown>, path: string): unknown {
  return path.split(".").reduce<unknown>((value, key) => {
    const candidate = Array.isArray(value) ? value[0] : value;
    return candidate && typeof candidate === "object"
      ? (candidate as Record<string, unknown>)[key]
      : undefined;
  }, record);
}

function displayValue(record: Record<string, unknown>, column: ResourceColumn) {
  const value = readPath(record, column.path);
  if (column.kind === "status" && typeof value === "string") return <StatusBadge status={value} />;
  if (column.kind === "boolean") return <StatusBadge status={value ? "active" : "inactive"} />;
  if (column.kind === "date" && typeof value === "string") {
    return new Intl.DateTimeFormat("en-AE", { dateStyle: "medium", timeStyle: value.includes("T") ? "short" : undefined }).format(new Date(value));
  }
  if (column.kind === "money" && (typeof value === "number" || typeof value === "string")) {
    const currency = readPath(record, column.currencyPath ?? "currency");
    return `${typeof currency === "string" ? currency : "AED"} ${Number(value).toFixed(2)}`;
  }
  if (Array.isArray(value)) return value.map(String).join(", ");
  if (value === null || value === undefined || value === "") return "—";
  return String(value).replaceAll("_", " ");
}

export function ResourceTablePage({ config }: { config: ResourceConfig }) {
  const staff = useStaff();
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    const supabase = createClient();
    let query = supabase.from(config.table).select(config.select);
    if (config.orderBy) query = query.order(config.orderBy, { ascending: config.ascending ?? false });
    const { data, error: queryError } = await query.limit(250);
    if (queryError) setError("Clinic data could not be loaded with the current access policy.");
    else { setRows((data ?? []) as unknown as Record<string, unknown>[]); setError(null); }
    setLoading(false);
  }, [config]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);
  useClinicRealtime(staff.clinicId, config.realtime ?? [], load);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter((row) => JSON.stringify(row).toLowerCase().includes(needle));
  }, [rows, search]);

  return (
    <>
      <PageHeader
        eyebrow={config.eyebrow}
        title={config.title}
        description={config.description}
        actions={<input className="search-input" aria-label={`Search ${config.title}`} placeholder="Search this view" value={search} onChange={(event) => setSearch(event.target.value)} />}
      />
      {config.backendPending ? <p className="notice warning">{config.backendPending}</p> : null}
      <section className="panel">
        {loading ? <LoadingState /> : null}
        {!loading && error ? <ErrorState message={error} retry={load} /> : null}
        {!loading && !error && filtered.length === 0 ? <EmptyState title={config.emptyTitle} message={config.emptyMessage} /> : null}
        {!loading && !error && filtered.length > 0 ? (
          <div className="table-wrap">
            <table className="data-table">
              <thead><tr>{config.columns.map((column) => <th key={column.path}>{column.label}</th>)}</tr></thead>
              <tbody>{filtered.map((row, index) => (
                <tr key={String(row.id ?? index)}>{config.columns.map((column) => <td data-label={column.label} key={column.path}>{displayValue(row, column)}</td>)}</tr>
              ))}</tbody>
            </table>
          </div>
        ) : null}
      </section>
    </>
  );
}
