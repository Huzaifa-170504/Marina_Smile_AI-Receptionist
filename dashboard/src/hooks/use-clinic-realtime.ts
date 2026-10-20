"use client";

import { useEffect, useRef } from "react";

import { createClient } from "@/lib/supabase/browser";

const OPERATIONAL_TABLES = new Set([
  "appointments", "appointment_visits", "booking_requests", "payments",
  "schedule_overrides", "appointment_change_requests", "handoffs",
]);

export function useClinicRealtime(
  clinicId: string,
  tables: string[],
  onChange: () => void,
) {
  const callbackRef = useRef(onChange);
  const tableKey = [...new Set(tables)].sort().join(",");

  useEffect(() => {
    callbackRef.current = onChange;
  }, [onChange]);

  useEffect(() => {
    const selected = tableKey.split(",").filter((table) => OPERATIONAL_TABLES.has(table));
    if (!clinicId || selected.length === 0) return;
    const supabase = createClient();
    let channel = supabase.channel(`clinic-${clinicId}-${selected.join("-")}`);
    selected.forEach((table) => {
      channel = channel.on(
        "postgres_changes",
        { event: "*", schema: "public", table, filter: `clinic_id=eq.${clinicId}` },
        () => callbackRef.current(),
      );
    });
    channel.subscribe();
    return () => { void supabase.removeChannel(channel); };
  }, [clinicId, tableKey]);
}
