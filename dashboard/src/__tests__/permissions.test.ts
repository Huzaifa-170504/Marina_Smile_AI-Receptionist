import { describe, expect, it } from "vitest";

import { KNOWN_PERMISSIONS, resolvePermissions, roleDefaultPermissions } from "@/lib/permissions";

describe("role permissions", () => {
  it("gives owners every known permission", () => {
    expect(resolvePermissions("owner", [])).toEqual(expect.arrayContaining(KNOWN_PERMISSIONS));
  });

  it("keeps doctor access scoped by default", () => {
    const permissions = roleDefaultPermissions("doctor");
    expect(permissions).toContain("queue.view_own");
    expect(permissions).not.toContain("queue.view_all");
    expect(permissions).not.toContain("payment.view_proof");
  });

  it("applies explicit overrides without copying role defaults", () => {
    const permissions = resolvePermissions("receptionist", [
      { permission: "appointment.create", allowed: false },
      { permission: "payment.view_proof", allowed: true },
    ]);
    expect(permissions).not.toContain("appointment.create");
    expect(permissions).toContain("payment.view_proof");
    expect(permissions).toContain("patient.view_all");
  });
});
