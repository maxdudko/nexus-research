// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RegisterForm } from "@/components/auth/register-form";

const navigation = vi.hoisted(() => ({ replace: vi.fn(), next: null as string | null }));
vi.mock("next/navigation", () => ({
  useRouter: () => navigation,
  useSearchParams: () => ({ get: () => navigation.next }),
}));
const IDENTITY = { id: "11111111-1111-4111-8111-111111111111", email: "ada@example.com", role: "user" };

afterEach(() => {
  cleanup();
  navigation.replace.mockReset();
  navigation.next = null;
  vi.unstubAllGlobals();
});

async function fill(confirmation = "correct-horse-1") {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Email"), "ada@example.com");
  await user.type(screen.getByLabelText("Password"), "correct-horse-1");
  await user.type(screen.getByLabelText("Confirm password"), confirmation);
  return user;
}

describe("registration form", () => {
  it("creates an account, confirms the session, clears secrets and preserves next", async () => {
    navigation.next = "/history";
    let checks = 0;
    const fetchImpl = vi.fn(async (url: string) => {
      if (url.endsWith("/me")) return ++checks === 1 ? new Response("{}", { status: 401 }) : Response.json(IDENTITY);
      return Response.json(IDENTITY, { status: 201 });
    });
    vi.stubGlobal("fetch", fetchImpl);
    render(<RegisterForm />);
    const user = await fill();
    expect(screen.getByRole("link", { name: "Sign in" }).getAttribute("href")).toBe("/login?next=%2Fhistory");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("/history"));
    expect(fetchImpl).toHaveBeenCalledWith("/api/v1/auth/register", {
      method: "POST", credentials: "same-origin", headers: { "content-type": "application/json" },
      body: JSON.stringify({ email: "ada@example.com", password: "correct-horse-1" }),
    });
    expect(checks).toBe(2);
    expect((screen.getByLabelText("Password") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("Confirm password") as HTMLInputElement).value).toBe("");
  });

  it("rejects mismatched confirmation without posting", async () => {
    const fetchImpl = vi.fn(async () => new Response("{}", { status: 401 }));
    vi.stubGlobal("fetch", fetchImpl);
    render(<RegisterForm />);
    const user = await fill("different-password");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Passwords do not match.");
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it.each([
    [409, "This email is unavailable."],
    [422, "Enter a valid email"],
    [429, "Try again in 60 seconds."],
    [503, "The research service is unavailable."],
  ])("handles registration failure %s", async (status, message) => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response("{}", {
      status: url.endsWith("/me") ? 401 : status, headers: { "retry-after": "60" },
    })));
    render(<RegisterForm />);
    const user = await fill();
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect((await screen.findByRole("alert")).textContent).toContain(message);
    expect(navigation.replace).not.toHaveBeenCalled();
  });

  it("disables submission while pending and reports network failure", async () => {
    let rejectRequest!: (reason: Error) => void;
    const fetchImpl = vi.fn((url: string) => url.endsWith("/me")
      ? Promise.resolve(new Response("{}", { status: 401 }))
      : new Promise<Response>((_resolve, reject) => { rejectRequest = reject; }));
    vi.stubGlobal("fetch", fetchImpl);
    render(<RegisterForm />);
    const user = await fill();
    const button = screen.getByRole("button", { name: "Create account" });
    await user.click(button);
    expect((button as HTMLButtonElement).disabled).toBe(true);
    await user.click(button);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    rejectRequest(new Error("offline"));
    expect((await screen.findByRole("alert")).textContent).toContain("unavailable");
    expect((button as HTMLButtonElement).disabled).toBe(false);
  });

  it.each([401, 503])("does not redirect if the new session cannot be confirmed (%s)", async (status) => {
    let checks = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.endsWith("/me")) return new Response("{}", { status: ++checks === 1 ? 401 : status });
      return Response.json(IDENTITY, { status: 201 });
    }));
    render(<RegisterForm />);
    const user = await fill();
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect((await screen.findByRole("alert")).textContent).toContain(status === 401 ? "Please sign in" : "unavailable");
    expect(navigation.replace).not.toHaveBeenCalled();
  });

  it.each([["/history", "/history"], ["https://evil.example", "/"]])("redirects an existing session safely (%s)", async (next, target) => {
    navigation.next = next;
    vi.stubGlobal("fetch", vi.fn(async () => Response.json(IDENTITY)));
    render(<RegisterForm />);
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith(target));
    expect(screen.queryByLabelText("Email")).toBeNull();
  });

  it("drops unsafe next from the login link and new-session redirect", async () => {
    navigation.next = "//evil.example";
    let checks = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => url.endsWith("/me") && ++checks === 1
      ? new Response("{}", { status: 401 }) : Response.json(IDENTITY)));
    render(<RegisterForm />);
    const user = await fill();
    expect(screen.getByRole("link", { name: "Sign in" }).getAttribute("href")).toBe("/login");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("/"));
  });
});
