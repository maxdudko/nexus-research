// @vitest-environment jsdom

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useContext, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResearchActivityContext, ResearchActivityProvider } from "@/components/research/research-activity";
import { AppHeader, AuthenticatedFrame } from "@/components/shell/app-header";
import { THEME_STORAGE_KEY } from "@/lib/theme";

const navigation = vi.hoisted(() => ({
  replace: vi.fn(),
  push: vi.fn(),
  pathname: "/research/new",
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: navigation.replace, push: navigation.push }),
  usePathname: () => navigation.pathname,
}));

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

const stopResearch = vi.fn();

function Arm() {
  const activity = useContext(ResearchActivityContext);
  return (
    <button
      type="button"
      onClick={() => {
        activity?.setBusy(true);
        activity?.registerStop(stopResearch);
      }}
    >
      Arm
    </button>
  );
}

function renderHeader() {
  return render(
    <ResearchActivityProvider>
      <AppHeader email="ada@example.com" logoutError={null} onLogout={() => {}} />
      <Arm />
    </ResearchActivityProvider>,
  );
}

afterEach(() => {
  cleanup();
  navigation.push.mockReset();
  stopResearch.mockReset();
  navigation.pathname = "/research/new";
  document.documentElement.classList.remove("dark");
  localStorage.clear();
});

describe("app header", () => {
  it("opens the narrow menu, focuses the first link, and closes on Escape", async () => {
    setWide(false);
    const user = userEvent.setup();
    renderHeader();
    expect(screen.queryByRole("link", { name: "History" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Menu" }));
    expect(document.activeElement).toBe(screen.getByRole("link", { name: "Dashboard" }));
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("link", { name: "New research" }));
    await user.keyboard("{Escape}");
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Menu" }));
    expect(screen.queryByRole("link", { name: "History" })).toBeNull();
  });

  it("closes the narrow menu after a link is chosen", async () => {
    setWide(false);
    const user = userEvent.setup();
    renderHeader();
    await user.click(screen.getByRole("button", { name: "Menu" }));
    await user.click(screen.getByRole("link", { name: "History" }));
    expect(screen.queryByRole("link", { name: "Dashboard" })).toBeNull();
  });

  it("shows links and no menu on a wide layout", () => {
    setWide(true);
    renderHeader();
    expect(screen.queryByRole("button", { name: "Menu" })).toBeNull();
    expect(screen.getByRole("link", { name: "History" })).toBeTruthy();
  });

  it("asks before a shell link leaves an active research request", async () => {
    setWide(true);
    const user = userEvent.setup();
    renderHeader();
    await user.click(screen.getByRole("button", { name: "Arm" }));
    await user.click(screen.getByRole("link", { name: "History" }));
    expect(screen.getByRole("dialog").textContent).toContain(
      "Leaving stops this request. It does not keep running in the background.",
    );
    expect(navigation.push).not.toHaveBeenCalled();
    expect(stopResearch).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Stay" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("link", { name: "History" }));
    expect(stopResearch).not.toHaveBeenCalled();
    await user.click(screen.getByRole("link", { name: "History" }));
    await user.click(screen.getByRole("button", { name: "Leave" }));
    expect(stopResearch).toHaveBeenCalledTimes(1);
    expect(navigation.push).toHaveBeenCalledWith("/history");
  });

  it("keeps keyboard focus inside the leave dialog until Escape", async () => {
    setWide(true);
    const user = userEvent.setup();
    renderHeader();
    const outside = screen.getByRole("button", { name: "Arm" });
    await user.click(outside);
    await user.click(screen.getByRole("link", { name: "History" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.getAttribute("aria-labelledby")).toBe("leave-title");
    expect(dialog.getAttribute("aria-describedby")).toBe("leave-body");
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "Stay" }));
    expect(outside.closest("[inert]")).not.toBeNull();
    await user.tab();
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "Leave" }));
    await user.tab();
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "Stay" }));
    await user.keyboard("{Shift>}{Tab}{/Shift}");
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "Leave" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("link", { name: "History" }));
    expect(navigation.push).not.toHaveBeenCalled();
  });

  it("asks before logout while research is active", async () => {
    setWide(true);
    const onLogout = vi.fn();
    const user = userEvent.setup();
    render(
      <ResearchActivityProvider>
        <AppHeader email="ada@example.com" logoutError={null} onLogout={onLogout} />
        <Arm />
      </ResearchActivityProvider>,
    );
    await user.click(screen.getByRole("button", { name: "Arm" }));
    await user.click(screen.getByRole("button", { name: "Log out" }));
    expect(onLogout).not.toHaveBeenCalled();
    expect(stopResearch).not.toHaveBeenCalled();
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain(
      "Logging out stops this request. It does not keep running in the background.",
    );
    await user.click(within(dialog).getByRole("button", { name: "Stay" }));
    expect(onLogout).not.toHaveBeenCalled();
    expect(stopResearch).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Log out" }));
    await user.click(screen.getByRole("button", { name: "Log out" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Log out" }));
    expect(stopResearch).toHaveBeenCalledTimes(1);
    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  it("remounts page content when the signed-in user changes", () => {
    setWide(true);
    let mounts = 0;
    function Marker() {
      const [mark] = useState(() => {
        mounts += 1;
        return mounts;
      });
      return <p>Mount {mark}</p>;
    }
    const frame = (userId: string) => (
      <ResearchActivityProvider>
        <AuthenticatedFrame email="ada@example.com" userId={userId} onLogout={() => {}} logoutError={null}>
          <Marker />
        </AuthenticatedFrame>
      </ResearchActivityProvider>
    );
    const { rerender } = render(frame("user-a"));
    expect(screen.getByText("Mount 1")).toBeTruthy();
    rerender(frame("user-b"));
    expect(screen.getByText("Mount 2")).toBeTruthy();
    expect(screen.queryByText("Mount 1")).toBeNull();
  });

  it("stores the theme chosen from the header", async () => {
    setWide(true);
    renderHeader();
    await userEvent.click(screen.getByRole("button", { name: "Switch to dark theme" }));
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
  });
});
