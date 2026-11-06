// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChangeEmailForm } from "@/components/auth/change-email-form";
import { SessionBoundary } from "@/components/auth/session-boundary";

const navigation = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => navigation, usePathname: () => "/account" }));
const ADA = { id: "11111111-1111-4111-8111-111111111111", email: "ada@example.com", role: "user" };
const NEW = { ...ADA, email: "new@example.com" };

beforeEach(() => {
  Object.defineProperty(window, "matchMedia", { configurable: true, value: () => ({
    matches: true, media: "(min-width: 768px)", addEventListener() {}, removeEventListener() {},
  }) });
});
afterEach(() => {
  cleanup(); navigation.replace.mockReset(); vi.unstubAllGlobals(); localStorage.clear();
  document.cookie = "nexus_csrf=; Max-Age=0; Path=/";
});

async function fill() {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("New email"), NEW.email);
  await user.type(screen.getByLabelText("Current password"), "correct-horse-1");
  return user;
}

function mount() { render(<SessionBoundary><ChangeEmailForm /></SessionBoundary>); }

describe("email change", () => {
  it("rotates credentials and refreshes the shared identity and header", async () => {
    let checks = 0;
    const fetchImpl = vi.fn(async (url: string) => {
      if (url.endsWith("/me")) return Response.json(++checks === 1 ? ADA : NEW);
      return Response.json(NEW);
    });
    vi.stubGlobal("fetch", fetchImpl);
    document.cookie = "nexus_csrf=csrf-value; Path=/";
    mount();
    const user = await fill();
    expect(screen.getByText(/without verification/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Show current password for email change" }));
    expect((screen.getByLabelText("Current password") as HTMLInputElement).type).toBe("text");
    await user.click(screen.getByRole("button", { name: "Change email" }));
    expect((await screen.findByRole("status")).textContent).toContain("Your email was changed");
    await waitFor(() => expect(screen.getByRole("link", { name: "Account" }).getAttribute("title")).toBe(`Account (${NEW.email})`));
    expect(screen.getByText(`Current email: ${NEW.email}`)).toBeTruthy();
    expect(screen.queryByTitle(`Account (${ADA.email})`)).toBeNull();
    expect(screen.queryByLabelText("Current password")).toBeNull();
    expect(fetchImpl).toHaveBeenCalledWith("/api/v1/auth/change-email", {
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: { "content-type": "application/json", "x-csrf-token": "csrf-value" },
      body: JSON.stringify({ new_email: NEW.email, current_password: "correct-horse-1" }),
    });
    expect(navigation.replace).not.toHaveBeenCalled();
  });

  it("retries identity refresh without repeating the email mutation", async () => {
    let checks = 0;
    const fetchImpl = vi.fn(async (url: string) => {
      if (url.endsWith("/me")) {
        checks++;
        if (checks === 2) return new Response("{}", { status: 503 });
        return Response.json(checks === 1 ? ADA : NEW);
      }
      return Response.json(NEW);
    });
    vi.stubGlobal("fetch", fetchImpl);
    mount();
    const user = await fill();
    await user.click(screen.getByRole("button", { name: "Change email" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Your email was changed");
    expect(screen.queryByRole("button", { name: "Change email" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Refresh account details" }));
    await waitFor(() => expect(screen.getByRole("link", { name: "Account" }).getAttribute("title")).toBe(`Account (${NEW.email})`));
    expect(fetchImpl.mock.calls.filter(([url]) => url.endsWith("/change-email"))).toHaveLength(1);
  });

  it.each([
    [400, "current password is incorrect"], [409, "email is unavailable"],
    [422, "valid new email"], [403, "Reload this page"], [429, "60 seconds"], [503, "unavailable"],
  ])("shows mutation error %s", async (status, message) => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => url.endsWith("/me")
      ? Response.json(ADA) : new Response("{}", { status, headers: { "retry-after": "60" } })));
    mount();
    const user = await fill();
    await user.click(screen.getByRole("button", { name: "Change email" }));
    expect((await screen.findByRole("alert")).textContent).toContain(message);
    expect(navigation.replace).not.toHaveBeenCalled();
    expect((screen.getByLabelText("New email") as HTMLInputElement).value).toBe(NEW.email);
  });

  it.each(["mutation", "refresh"])("redirects an expired session during %s", async (failure) => {
    let checks = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.endsWith("/me")) return ++checks === 1 ? Response.json(ADA) : new Response("{}", { status: 401 });
      return failure === "mutation" ? new Response("{}", { status: 401 }) : Response.json(NEW);
    }));
    mount();
    const user = await fill();
    await user.click(screen.getByRole("button", { name: "Change email" }));
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("/login?next=%2Faccount"));
  });

  it("prevents duplicate submissions and allows retry after network failure", async () => {
    let rejectRequest!: (reason: Error) => void;
    const fetchImpl = vi.fn((url: string) => url.endsWith("/me") ? Promise.resolve(Response.json(ADA))
      : new Promise<Response>((_resolve, reject) => { rejectRequest = reject; }));
    vi.stubGlobal("fetch", fetchImpl);
    mount();
    const user = await fill();
    await user.click(screen.getByRole("button", { name: "Change email" }));
    const pending = screen.getByRole("button", { name: "Changing email…" });
    expect((pending as HTMLButtonElement).disabled).toBe(true);
    await user.click(pending);
    expect(fetchImpl.mock.calls.filter(([url]) => url.endsWith("/change-email"))).toHaveLength(1);
    rejectRequest(new Error("offline"));
    expect((await screen.findByRole("alert")).textContent).toContain("unavailable");
    expect((screen.getByRole("button", { name: "Change email" }) as HTMLButtonElement).disabled).toBe(false);
  });
});
