// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "@/components/auth/login-form";

const navigation = vi.hoisted(() => ({
  replace: vi.fn(),
  next: null as string | null,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: navigation.replace }),
  useSearchParams: () => ({ get: (key: string) => (key === "next" ? navigation.next : null) }),
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
  vi.unstubAllGlobals();
});

describe("login form", () => {
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

  it("redirects an existing session and ignores an off-site next", async () => {
    navigation.next = "https://evil.example";
    vi.stubGlobal("fetch", vi.fn(async () => Response.json(IDENTITY)));
    render(<LoginForm />);
    await waitFor(() => {
      expect(navigation.replace).toHaveBeenCalledWith("/");
    });
    expect(screen.queryByLabelText("Email")).toBeNull();
  });
});
