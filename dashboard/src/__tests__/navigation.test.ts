import { describe, expect, it } from "vitest";

import { navigationForRole } from "@/lib/navigation";

const labels = (role: Parameters<typeof navigationForRole>[0]) => navigationForRole(role).map((item) => item.label);

describe("role-aware navigation", () => {
  it("shows owner administration", () => {
    expect(labels("owner")).toEqual(expect.arrayContaining(["Staff & access", "Clinic settings", "Payments"]));
  });

  it("does not expose staff management to doctors", () => {
    expect(labels("doctor")).toContain("My queue");
    expect(labels("doctor")).not.toContain("Staff & access");
  });

  it("does not expose permissions to receptionists", () => {
    expect(labels("receptionist")).toContain("Patient queue");
    expect(labels("receptionist")).not.toContain("Staff & access");
  });

  it("routes payment verifiers only to their workflow", () => {
    expect(labels("payment_verifier")).toEqual(["Today", "Payments"]);
  });
});
