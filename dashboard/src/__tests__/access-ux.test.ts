import { describe, expect, it } from "vitest";

import { PERMISSION_DEFINITIONS, resolvePermissions, roleDefaultPermissions } from "@/lib/permissions";

describe("Staff & Access language", () => {
  it("provides a human-readable label for every permission", () => {
    expect(PERMISSION_DEFINITIONS.find((item) => item.permission === "appointment.view_all")?.label).toBe("View all clinic appointments");
    expect(PERMISSION_DEFINITIONS.find((item) => item.permission === "queue.update_all")?.label).toBe("Manage all patient queues");
    expect(PERMISSION_DEFINITIONS.every((item) => item.label !== item.permission)).toBe(true);
  });

  it("distinguishes inherited defaults from explicit exceptions", () => {
    expect(roleDefaultPermissions("receptionist")).toContain("patient.view_all");
    expect(resolvePermissions("receptionist", [{ permission: "patient.view_all", allowed: false }])).not.toContain("patient.view_all");
    expect(resolvePermissions("receptionist", [])).toContain("patient.view_all");
  });
});
