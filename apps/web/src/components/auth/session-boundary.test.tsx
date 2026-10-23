// @vitest-environment jsdom

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useContext, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SessionBoundary } from "@/components/auth/session-boundary";
import { ResearchActivityContext, ResearchActivityProvider } from "@/components/research/research-activity";
import { AuthenticatedFrame } from "@/components/shell/app-header";

const navigation = vi.hoisted(() => ({
  replace: vi.fn(),
  push: vi.fn(),
  pathname: "/",
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: navigation.replace, push: navigation.push }),
  usePathname: () => navigation.pathname,
}));

const ADA = {
  id: "11111111-1111-4111-8111-111111111111",
  email: "ada@example.com",
  role: "user",
};

const GRACE = {
  id: "22222222-2222-4222-8222-222222222222",
  email: "grace@example.com",
  role: "user",
};

function setWide(matches: boolean) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: () => ({
      matches,
      media: "(min-width: 768px)",
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

afterEach(() => {
  cleanup();
  navigation.replace.mockReset();
  navigation.push.mockReset();
  navigation.pathname = "/";
  vi.unstubAllGlobals();
  localStorage.clear();
  document.cookie = "nexus_csrf=; Max-Age=0";
});

describe("session boundary", () => {
  it("shows a session check and hides private content until me resolves", async () => {
    setWide(true);
    let resolveMe: (response: Response) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            resolveMe = resolve;
          }),
      ),
    );
    render(
      <SessionBoundary>
        <p>Private runs</p>
      </SessionBoundary>,
    );
    expect(screen.getByRole("status").textContent).toBe("Checking session.");
    expect(screen.queryByText("Private runs")).toBeNull();
    resolveMe(Response.json(ADA));
    expect(await screen.findByText("Private runs")).toBeTruthy();
    expect(screen.getByText("ada@example.com")).toBeTruthy();
  });

  it("sends an anonymous reader to login with a safe next path", async () => {
    setWide(true);
    navigation.pathname = "/history";
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 401 })));
    render(
      <SessionBoundary>
        <p>Private runs</p>
      </SessionBoundary>,
    );
    await waitFor(() => {
      expect(navigation.replace).toHaveBeenCalledWith("/login?next=%2Fhistory");
    });
    expect(screen.queryByText("Private runs")).toBeNull();
  });

  it("drops an unknown path from the login redirect", async () => {
    setWide(true);
    navigation.pathname = "/settings";
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 401 })));
    render(
      <SessionBoundary>
        <p>Private runs</p>
      </SessionBoundary>,
    );
    await waitFor(() => {
      expect(navigation.replace).toHaveBeenCalledWith("/login");
    });
  });

  it("shows an unavailable service and retries", async () => {
    setWide(true);
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(new Response("{}", { status: 502 }))
      .mockResolvedValueOnce(Response.json(ADA));
    vi.stubGlobal("fetch", fetchImpl);
    render(
      <SessionBoundary>
        <p>Private runs</p>
      </SessionBoundary>,
    );
    expect((await screen.findByRole("alert")).textContent).toBe("The research service is unavailable.");
    expect(screen.queryByText("Private runs")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Private runs")).toBeTruthy();
  });

  it("logs out only after the server accepts revocation", async () => {
    setWide(true);
    const fetchImpl = vi.fn(async (url: string) => {
      if (url === "/api/v1/auth/logout") return new Response("{}", { status: 500 });
      return Response.json(ADA);
    });
    vi.stubGlobal("fetch", fetchImpl);
    const user = userEvent.setup();
    render(
      <SessionBoundary>
        <p>Private runs</p>
      </SessionBoundary>,
    );
    await user.click(await screen.findByRole("button", { name: "Log out" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Log out did not finish. Try again.");
    expect(screen.getByText("ada@example.com")).toBeTruthy();
    expect(navigation.replace).not.toHaveBeenCalled();

    fetchImpl.mockImplementation(async (url: string) => {
      if (url === "/api/v1/auth/logout") return new Response("{}", { status: 401 });
      return Response.json(ADA);
    });
    await user.click(screen.getByRole("button", { name: "Log out" }));
    await waitFor(() => {
      expect(navigation.replace).toHaveBeenCalledWith("/login");
    });
    expect(screen.queryByText(/logged out/i)).toBeNull();
  });

  it("confirms logout during active research and still revokes the session", async () => {
    setWide(true);
    document.cookie = "nexus_csrf=test-token";
    const stop = vi.fn();
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      void init;
      if (url === "/api/v1/auth/logout") return new Response("{}", { status: 200 });
      return Response.json(ADA);
    });
    vi.stubGlobal("fetch", fetchImpl);
    const user = userEvent.setup();
    render(
      <ResearchActivityProvider>
        <SessionBoundary>
          <ArmResearch onStop={stop} />
        </SessionBoundary>
      </ResearchActivityProvider>,
    );
    await user.click(await screen.findByRole("button", { name: "Arm research" }));
    await user.click(screen.getByRole("button", { name: "Log out" }));
    expect(stop).not.toHaveBeenCalled();
    expect(fetchImpl.mock.calls.some((call) => call[0] === "/api/v1/auth/logout")).toBe(false);
    expect(navigation.replace).not.toHaveBeenCalled();
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Log out" }));
    await waitFor(() => {
      expect(navigation.replace).toHaveBeenCalledWith("/login");
    });
    expect(stop).toHaveBeenCalledTimes(1);
    const logoutCall = fetchImpl.mock.calls.find((call) => call[0] === "/api/v1/auth/logout");
    expect(logoutCall?.[1]).toMatchObject({
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { "x-csrf-token": "test-token" },
    });
  });

  it("stops research and keeps the session when confirmed logout fails", async () => {
    setWide(true);
    document.cookie = "nexus_csrf=test-token";
    const stop = vi.fn();
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      void init;
      if (url === "/api/v1/auth/logout") return new Response("{}", { status: 500 });
      return Response.json(ADA);
    });
    vi.stubGlobal("fetch", fetchImpl);
    const user = userEvent.setup();
    render(
      <ResearchActivityProvider>
        <SessionBoundary>
          <ArmResearch onStop={stop} />
        </SessionBoundary>
      </ResearchActivityProvider>,
    );
    await user.click(await screen.findByRole("button", { name: "Arm research" }));
    await user.click(screen.getByRole("button", { name: "Log out" }));
    expect(stop).not.toHaveBeenCalled();
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Log out" }));
    expect(stop).toHaveBeenCalledTimes(1);
    expect((await screen.findByRole("alert")).textContent).toBe("Log out did not finish. Try again.");
    expect(screen.getByText("ada@example.com")).toBeTruthy();
    expect(navigation.replace).not.toHaveBeenCalled();
    const logoutCall = fetchImpl.mock.calls.find((call) => call[0] === "/api/v1/auth/logout");
    expect(logoutCall?.[1]).toMatchObject({
      method: "POST",
      headers: { "x-csrf-token": "test-token" },
    });
  });

  it("leaves for login after a successful logout", async () => {
    setWide(true);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/api/v1/auth/logout") return new Response("{}", { status: 200 });
        return Response.json(ADA);
      }),
    );
    render(
      <SessionBoundary>
        <p>Private runs</p>
      </SessionBoundary>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "Log out" }));
    await waitFor(() => {
      expect(navigation.replace).toHaveBeenCalledWith("/login");
    });
  });
});

function ArmResearch({ onStop = () => {} }: { onStop?: () => void }) {
  const activity = useContext(ResearchActivityContext);
  return (
    <button
      type="button"
      onClick={() => {
        activity?.setBusy(true);
        activity?.registerStop(onStop);
      }}
    >
      Arm research
    </button>
  );
}

function Note() {
  const [note, setNote] = useState("empty");
  return (
    <button type="button" onClick={() => setNote("from Ada")}>
      {note}
    </button>
  );
}

describe("authenticated frame", () => {
  it("remounts private content when the user id changes", async () => {
    setWide(true);
    const user = userEvent.setup();
    const frame = (identity: typeof ADA) => (
      <ResearchActivityProvider>
        <AuthenticatedFrame
          email={identity.email}
          userId={identity.id}
          logoutError={null}
          onLogout={() => {}}
        >
          <Note />
        </AuthenticatedFrame>
      </ResearchActivityProvider>
    );
    const { rerender } = render(frame(ADA));
    await user.click(screen.getByRole("button", { name: "empty" }));
    expect(screen.getByRole("button", { name: "from Ada" })).toBeTruthy();
    rerender(frame(GRACE));
    expect(screen.getByRole("button", { name: "empty" })).toBeTruthy();
    expect(screen.getByText("grace@example.com")).toBeTruthy();
  });
});
