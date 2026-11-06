// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChangePasswordForm } from "@/components/auth/change-password-form";

const navigation = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => navigation }));

const CURRENT = "current-password-1";
const NEW = "different-password-2";

afterEach(() => {
  cleanup();
  navigation.replace.mockReset();
  vi.unstubAllGlobals();
  document.cookie = "nexus_csrf=; Max-Age=0; Path=/";
});

async function fill(newPassword = NEW, confirmation = newPassword) {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Current password"), CURRENT);
  await user.type(screen.getByLabelText("New password"), newPassword);
  await user.type(screen.getByLabelText("Confirm new password"), confirmation);
  return user;
}

describe("password change", () => {
  it("posts credentials with CSRF, clears fields, and returns to login", async () => {
    document.cookie = "nexus_csrf=test-csrf; Path=/";
    const fetchImpl = vi.fn(async () => Response.json({ status: "ok" }));
    vi.stubGlobal("fetch", fetchImpl);
    render(<ChangePasswordForm />);
    expect(screen.getByText(/signs you out on every device/)).toBeTruthy();
    const user = await fill();
    await user.click(screen.getByRole("button", { name: "Change password" }));
    expect(fetchImpl).toHaveBeenCalledWith("/api/v1/auth/change-password", {
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: { "content-type": "application/json", "x-csrf-token": "test-csrf" },
      body: JSON.stringify({ current_password: CURRENT, new_password: NEW }),
    });
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("/login?password_changed=1"));
    for (const label of ["Current password", "New password", "Confirm new password"]) {
      expect((screen.getByLabelText(label) as HTMLInputElement).value).toBe("");
    }
  });

  it.each([[NEW, "mismatched-password", "do not match"], [CURRENT, CURRENT, "different from your current password"]])(
    "validates passwords before posting (%s)", async (password, confirmation, message) => {
      const fetchImpl = vi.fn();
      vi.stubGlobal("fetch", fetchImpl);
      render(<ChangePasswordForm />);
      const user = await fill(password, confirmation);
      await user.click(screen.getByRole("button", { name: "Change password" }));
      expect((await screen.findByRole("alert")).textContent).toContain(message);
      expect(fetchImpl).not.toHaveBeenCalled();
    },
  );

  it.each([
    [400, "Your current password is incorrect."],
    [422, "Use 12–128 characters"],
    [403, "Reload this page"],
    [429, "Try again in 60 seconds."],
    [503, "unavailable"],
  ])("shows actionable API errors (%s) without signing out", async (status, message) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status, headers: { "retry-after": "60" } })));
    render(<ChangePasswordForm />);
    const user = await fill();
    await user.click(screen.getByRole("button", { name: "Change password" }));
    expect((await screen.findByRole("alert")).textContent).toContain(message);
    expect(navigation.replace).not.toHaveBeenCalled();
    expect((screen.getByLabelText("New password") as HTMLInputElement).value).toBe(NEW);
  });

  it("returns an expired session to login with a safe Account return path", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 401 })));
    render(<ChangePasswordForm />);
    const user = await fill();
    await user.click(screen.getByRole("button", { name: "Change password" }));
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("/login?next=%2Faccount"));
    expect((screen.getByLabelText("Current password") as HTMLInputElement).value).toBe("");
  });

  it("blocks duplicate submissions and allows retry after a network failure", async () => {
    let rejectRequest!: (reason: Error) => void;
    const fetchImpl = vi.fn(() => new Promise<Response>((_resolve, reject) => { rejectRequest = reject; }));
    vi.stubGlobal("fetch", fetchImpl);
    render(<ChangePasswordForm />);
    const user = await fill();
    await user.click(screen.getByRole("button", { name: "Change password" }));
    const button = screen.getByRole("button", { name: "Changing password…" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    await user.click(button);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    rejectRequest(new Error("network failure"));
    expect((await screen.findByRole("alert")).textContent).toContain("unavailable");
    expect((screen.getByRole("button", { name: "Change password" }) as HTMLButtonElement).disabled).toBe(false);
  });
});
