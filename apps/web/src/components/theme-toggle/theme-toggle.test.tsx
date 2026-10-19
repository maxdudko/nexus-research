// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { ThemeToggle } from "@/components/theme-toggle/theme-toggle";
import { THEME_STORAGE_KEY } from "@/lib/theme";

afterEach(() => {
  cleanup();
  document.documentElement.classList.remove("dark");
  document.documentElement.style.colorScheme = "";
  localStorage.clear();
});

describe("theme toggle", () => {
  it("switches the document theme and stores the choice", async () => {
    render(<ThemeToggle />);

    await userEvent.click(screen.getByRole("button", { name: "Switch to dark theme" }));

    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.style.colorScheme).toBe("dark");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(screen.getByRole("button", { name: "Switch to light theme" })).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Switch to light theme" }));

    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(document.documentElement.style.colorScheme).toBe("light");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
  });
});
