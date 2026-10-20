import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ForgotPasswordForm } from "@/components/auth/forgot-password-form";
import { PasswordSetupForm } from "@/components/auth/password-setup-form";

const replace = vi.fn();
const refresh = vi.fn();
const getUser = vi.fn();
const updateUser = vi.fn();
const signOut = vi.fn();
const resetPasswordForEmail = vi.fn();
let linkError = false;

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, refresh }),
  useSearchParams: () => new URLSearchParams(linkError ? "error=invalid_or_expired" : ""),
}));
vi.mock("@/lib/supabase/browser", () => ({ createClient: () => ({ auth: {
  getUser, updateUser, signOut, resetPasswordForEmail,
} }) }));

describe("password recovery", () => {
  beforeEach(() => {
    linkError = false; getUser.mockReset(); updateUser.mockReset(); signOut.mockReset();
    resetPasswordForEmail.mockReset(); replace.mockReset(); refresh.mockReset();
  });

  it("accepts an email and always returns the generic anti-enumeration response", async () => {
    resetPasswordForEmail.mockRejectedValue(new Error("provider detail"));
    render(<ForgotPasswordForm />); const user = userEvent.setup();
    await user.type(screen.getByLabelText("Email address"), "owner@example.com");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(await screen.findByRole("status")).toHaveTextContent("If an account exists for this email");
    expect(screen.queryByText("provider detail")).not.toBeInTheDocument();
  });

  it("validates matching passwords before updating Supabase Auth", async () => {
    getUser.mockResolvedValue({ data: { user: { id: "user-1" } }, error: null });
    render(<PasswordSetupForm mode="recovery" />); const user = userEvent.setup();
    await waitFor(() => expect(screen.getByLabelText("New password")).toBeEnabled());
    await user.type(screen.getByLabelText("New password"), "StrongPassword12");
    await user.type(screen.getByLabelText("Confirm password"), "DifferentPassword12");
    await user.click(screen.getByRole("button", { name: "Reset password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Passwords do not match");
    expect(updateUser).not.toHaveBeenCalled();
  });

  it("handles an expired recovery link without exposing tokens", async () => {
    linkError = true; getUser.mockResolvedValue({ data: { user: null }, error: new Error("expired") });
    render(<PasswordSetupForm mode="recovery" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("invalid or has expired");
    expect(screen.getByRole("button", { name: "Reset password" })).toBeDisabled();
  });

  it.each(["Owner", "Doctor", "Receptionist", "Payment Verifier"])("lets a %s configure a password without another Owner", async () => {
    getUser.mockResolvedValue({ data: { user: { id: "staff-user" } }, error: null });
    updateUser.mockResolvedValue({ data: { user: { id: "staff-user" } }, error: null });
    signOut.mockResolvedValue({ error: null });
    render(<PasswordSetupForm mode="invite" />); const user = userEvent.setup();
    await waitFor(() => expect(screen.getByLabelText("New password")).toBeEnabled());
    await user.type(screen.getByLabelText("New password"), "StrongPassword12");
    await user.type(screen.getByLabelText("Confirm password"), "StrongPassword12");
    await user.click(screen.getByRole("button", { name: "Set up account" }));
    expect(updateUser).toHaveBeenCalledWith({ password: "StrongPassword12" });
    expect(await screen.findByRole("status")).toHaveTextContent("Password saved");
  });
});
