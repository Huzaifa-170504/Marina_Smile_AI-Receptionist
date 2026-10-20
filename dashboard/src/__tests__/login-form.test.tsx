import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "@/components/auth/login-form";

const replace = vi.fn();
const refresh = vi.fn();
const signInWithPassword = vi.fn();
const signOut = vi.fn();
const maybeSingle = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, refresh }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/lib/supabase/browser", () => ({
  createClient: () => ({
    auth: { signInWithPassword, signOut },
    from: () => ({ select: () => ({ eq: () => ({ maybeSingle }) }) }),
  }),
}));

describe("LoginForm", () => {
  beforeEach(() => {
    signInWithPassword.mockReset(); signOut.mockReset(); maybeSingle.mockReset(); replace.mockReset(); refresh.mockReset();
  });

  it("shows a safe invalid-credentials message", async () => {
    signInWithPassword.mockResolvedValue({ data: { user: null }, error: new Error("provider detail") });
    render(<LoginForm />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Work email"), "doctor@example.com");
    await user.type(screen.getByLabelText("Password"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "Sign in securely" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is incorrect.");
    expect(screen.queryByText("provider detail")).not.toBeInTheDocument();
  });

  it("shows a visible password recovery link", () => {
    render(<LoginForm />);
    expect(screen.getByRole("link", { name: "Forgot password?" })).toHaveAttribute("href", "/auth/forgot-password");
  });

  it("signs out an account without an active staff mapping", async () => {
    signInWithPassword.mockResolvedValue({ data: { user: { id: "user-1" } }, error: null });
    maybeSingle.mockResolvedValue({ data: null });
    render(<LoginForm />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Work email"), "inactive@example.com");
    await user.type(screen.getByLabelText("Password"), "valid-password");
    await user.click(screen.getByRole("button", { name: "Sign in securely" }));
    expect(signOut).toHaveBeenCalledOnce();
    expect(replace).toHaveBeenCalledWith("/account-disabled");
  });
});
