// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useContext } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResearchActivityContext, ResearchActivityProvider } from "@/components/research/research-activity";
import { AppHeader } from "@/components/shell/app-header";
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

function Arm() {
  const activity = useContext(ResearchActivityContext);
  return (
    <button type="button" onClick={() => activity?.setBusy(true)}>
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
    await user.click(screen.getByRole("button", { name: "Stay" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("link", { name: "History" }));
    await user.click(screen.getByRole("link", { name: "History" }));
    await user.click(screen.getByRole("button", { name: "Leave" }));
    expect(navigation.push).toHaveBeenCalledWith("/history");
  });

  it("stores the theme chosen from the header", async () => {
    setWide(true);
    renderHeader();
    await userEvent.click(screen.getByRole("button", { name: "Switch to dark theme" }));
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
  });
});
