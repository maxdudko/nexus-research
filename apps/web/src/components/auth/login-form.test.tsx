// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "@/components/auth/login-form";

const navigation = vi.hoisted(() => ({
  replace: vi.fn(),
  next: null as string | null,
  passwordChanged: null as string | null,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: navigation.replace }),
  useSearchParams: () => ({ get: (key: string) => (key === "next" ? navigation.next : key === "password_changed" ? navigation.passwordChanged : null) }),
}));

const IDENTITY = {
  id: "11111111-1111-4111-8111-111111111111",
  email: "ada@example.com",
  role: "user",
};

afterEach(() => {
  cleanup();
  navigation.replace.mockReset();
  navigation.next = null;
  navigation.passwordChanged = null;
  vi.unstubAllGlobals();
});

describe("login form", () => {
  it("shows the fixed password-change success message", async () => {
    navigation.passwordChanged = "1";
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 401 })));
    render(<LoginForm />);
    expect((await screen.findByText("Your password was changed. Sign in with your new password.")).getAttribute("role")).toBe("status");
  });

  it("posts credentials and redirects to the allowlisted path", async () => {
    navigation.next = "/history";
    let meCalls = 0;
    const fetchImpl = vi.fn(async (url: string) => {
      if (url === "/api/v1/auth/me") {
        meCalls += 1;
        if (meCalls === 1) return new Response("{}", { status: 401 });
        return Response.json(IDENTITY);
      }
      return new Response("{}", { status: 200 });
    });
    vi.stubGlobal("fetch", fetchImpl);
    const user = userEvent.setup();
    render(<LoginForm />);

    await user.type(await screen.findByLabelText("Email"), "ada@example.com");
    expect(screen.getByRole("link", { name: "Forgot password?" }).getAttribute("href")).toBe("/forgot-password");
    expect(screen.getByRole("link", { name: "Create an account" }).getAttribute("href")).toBe("/register?next=%2Fhistory");
    await user.type(screen.getByLabelText("Password"), "correct-horse-1");
    await user.click(screen.getByRole("button", { name: "Log in" }));

    expect(fetchImpl).toHaveBeenCalledWith("/api/v1/auth/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email: "ada@example.com", password: "correct-horse-1" }),
    });
    expect(fetchImpl).toHaveBeenCalledWith("/api/v1/auth/me", {
      credentials: "same-origin",
      cache: "no-store",
    });
    await waitFor(() => {
      expect(navigation.replace).toHaveBeenCalledWith("/history");
    });
    expect(screen.queryByRole("button", { name: "Log out" })).toBeNull();
  });

  it.each([null, "https://evil.example"])("redirects an existing session safely (%s)", async (next) => {
    navigation.next = next;
    vi.stubGlobal("fetch", vi.fn(async () => Response.json(IDENTITY)));
    render(<LoginForm />);
    await waitFor(() => {
      expect(navigation.replace).toHaveBeenCalledWith("/dashboard");
    });
    expect(screen.queryByLabelText("Email")).toBeNull();
  });
});
