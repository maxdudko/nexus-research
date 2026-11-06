// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ForgotPasswordForm } from "@/components/auth/forgot-password-form";
import { ResetPasswordForm } from "@/components/auth/reset-password-form";

const navigation = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => navigation }));
const TOKEN = "a".repeat(43);
const PASSWORD = "different-password";

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
  navigation.replace.mockReset();
  vi.unstubAllGlobals();
});

async function fillReset(confirmation = PASSWORD) {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("New password"), PASSWORD);
  await user.type(screen.getByLabelText("Confirm new password"), confirmation);
  return user;
}

function renderReset() {
  window.history.replaceState(null, "", `/reset-password#token=${TOKEN}`);
  render(<StrictMode><ResetPasswordForm /></StrictMode>);
}

describe("forgot password", () => {
  it("requests recovery and always shows a generic confirmation", async () => {
    const fetchImpl = vi.fn(async () => new Response("{}", { status: 202 }));
    vi.stubGlobal("fetch", fetchImpl);
    render(<ForgotPasswordForm />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Email"), "ada@example.com");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));
    expect((await screen.findByRole("status")).textContent).toBe("If an eligible account exists, you’ll receive a password-reset link.");
    expect(fetchImpl).toHaveBeenCalledWith("/api/v1/auth/forgot-password", {
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: { "content-type": "application/json" }, body: JSON.stringify({ email: "ada@example.com" }),
    });
    expect(screen.getByRole("link", { name: "Back to login" }).getAttribute("href")).toBe("/login");
  });

  it.each([[422, "valid email"], [429, "Too many reset requests"], [503, "unavailable"]])("handles request error %s", async (status, message) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status })));
    render(<ForgotPasswordForm />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Email"), "ada@example.com");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));
    expect((await screen.findByRole("alert")).textContent).toContain(message);
  });

  it("blocks duplicate requests and recovers from network failure", async () => {
    let rejectRequest!: (error: Error) => void;
    const fetchImpl = vi.fn(() => new Promise<Response>((_resolve, reject) => { rejectRequest = reject; }));
    vi.stubGlobal("fetch", fetchImpl);
    render(<ForgotPasswordForm />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Email"), "ada@example.com");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));
    const pending = screen.getByRole("button", { name: "Sending…" });
    expect((pending as HTMLButtonElement).disabled).toBe(true);
    await user.click(pending);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    rejectRequest(new Error("offline"));
    expect((await screen.findByRole("alert")).textContent).toContain("unavailable");
    expect(screen.getByRole("button", { name: "Send reset link" })).toBeTruthy();
  });
});

describe("reset password", () => {
  it("keeps the token through Strict Mode, removes the fragment, and only posts on submit", async () => {
    const fetchImpl = vi.fn(async () => Response.json({ status: "ok" }));
    vi.stubGlobal("fetch", fetchImpl);
    renderReset();
    const user = await fillReset();
    expect(window.location.hash).toBe("");
    expect(window.location.pathname).toBe("/reset-password");
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain(TOKEN);
    await user.click(screen.getByRole("button", { name: "Reset password" }));
    expect(fetchImpl).toHaveBeenCalledWith("/api/v1/auth/reset-password", {
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: { "content-type": "application/json" }, body: JSON.stringify({ token: TOKEN, new_password: PASSWORD }),
    });
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("/login?password_changed=1"));
  });

  it.each(["", "#token=bad", "?token=" + TOKEN])("rejects a missing or malformed fragment (%s)", async (suffix) => {
    const fetchImpl = vi.fn();
    vi.stubGlobal("fetch", fetchImpl);
    window.history.replaceState(null, "", "/reset-password" + suffix);
    render(<ResetPasswordForm />);
    expect((await screen.findByRole("alert")).textContent).toContain("invalid or expired");
    expect(screen.getByRole("link", { name: "Request a new reset link" }).getAttribute("href")).toBe("/forgot-password");
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("checks confirmation before posting", async () => {
    const fetchImpl = vi.fn();
    vi.stubGlobal("fetch", fetchImpl);
    renderReset();
    const user = await fillReset("mismatched-password");
    await user.click(screen.getByRole("button", { name: "Reset password" }));
    expect((await screen.findByRole("alert")).textContent).toContain("do not match");
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it.each([[400, "invalid or expired"], [422, "12–128 characters"], [429, "Too many reset attempts"], [503, "unavailable"]])("handles reset error %s", async (status, message) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status })));
    renderReset();
    const user = await fillReset();
    await user.click(screen.getByRole("button", { name: "Reset password" }));
    expect((await screen.findByRole("alert")).textContent).toContain(message);
    expect(navigation.replace).not.toHaveBeenCalled();
  });

  it("blocks duplicate submissions and allows retry after a failed request", async () => {
    let rejectRequest!: (error: Error) => void;
    const fetchImpl = vi.fn(() => new Promise<Response>((_resolve, reject) => { rejectRequest = reject; }));
    vi.stubGlobal("fetch", fetchImpl);
    renderReset();
    const user = await fillReset();
    await user.click(screen.getByRole("button", { name: "Reset password" }));
    const pending = screen.getByRole("button", { name: "Resetting…" });
    expect((pending as HTMLButtonElement).disabled).toBe(true);
    await user.click(pending);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    rejectRequest(new Error("offline"));
    expect((await screen.findByRole("alert")).textContent).toContain("unavailable");
    expect(screen.getByRole("button", { name: "Reset password" })).toBeTruthy();
  });
});
