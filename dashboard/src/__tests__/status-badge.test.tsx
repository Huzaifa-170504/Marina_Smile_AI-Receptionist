import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBadge } from "@/components/status-badge";

describe("StatusBadge", () => {
  it("renders lifecycle labels readably", () => {
    render(<StatusBadge status="awaiting_slot_selection" />);
    expect(screen.getByText("awaiting slot selection")).toHaveClass("status-info");
  });

  it("marks rejection as a danger state", () => {
    render(<StatusBadge status="rejected" />);
    expect(screen.getByText("rejected")).toHaveClass("status-danger");
  });
});
