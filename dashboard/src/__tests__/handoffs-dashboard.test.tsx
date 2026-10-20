import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { HandoffsDashboard } from "@/components/handoffs-dashboard";

vi.mock("@/components/staff-context", () => ({ useStaff: () => ({ id: "staff-1", clinicId: "clinic-1" }) }));
vi.mock("@/hooks/use-clinic-realtime", () => ({ useClinicRealtime: vi.fn() }));
vi.mock("@/lib/api", () => ({ apiRequest: vi.fn().mockResolvedValue([]) }));
vi.mock("@/lib/supabase/browser", () => ({ createClient: () => ({ from: () => ({ select: () => ({ order: () => Promise.resolve({ data: [], error: null }) }) }) }) }));

describe("HandoffsDashboard", () => {
  it("explains when automation transfers responsibility to staff", () => {
    render(<HandoffsDashboard />);
    expect(screen.getByText("When a request needs a person")).toBeInTheDocument();
    expect(screen.getByText(/payment dispute/)).toBeInTheDocument();
    expect(screen.getByText(/AI uncertainty/)).toBeInTheDocument();
  });
});
