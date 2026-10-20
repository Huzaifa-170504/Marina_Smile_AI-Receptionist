"use client";

import { createContext, useContext, type ReactNode } from "react";

import type { StaffProfile } from "@/types/domain";

const StaffContext = createContext<StaffProfile | null>(null);

export function StaffProvider({ staff, children }: { staff: StaffProfile; children: ReactNode }) {
  return <StaffContext.Provider value={staff}>{children}</StaffContext.Provider>;
}

export function useStaff(): StaffProfile {
  const staff = useContext(StaffContext);
  if (!staff) throw new Error("useStaff must be used inside StaffProvider");
  return staff;
}
