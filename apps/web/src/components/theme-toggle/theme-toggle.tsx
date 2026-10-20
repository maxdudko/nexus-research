"use client";

import { useSyncExternalStore } from "react";

import { applyTheme, subscribeTheme, themeFromDocument, type Theme } from "@/lib/theme";

function themeLabel(theme: Theme): string {
  return theme === "dark" ? "Switch to light theme" : "Switch to dark theme";
}

function ThemeIcon({ theme }: { theme: Theme }) {
  if (theme === "dark") {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true" className="size-5">
        <circle cx="12" cy="12" r="4" fill="none" stroke="currentColor" strokeWidth="1.75" />
        <path
          fill="none"
          stroke="currentColor"
          strokeLinecap="round"
          strokeWidth="1.75"
          d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M5.1 5.1l1.8 1.8M17.1 17.1l1.8 1.8M18.9 5.1l-1.8 1.8M6.9 17.1l-1.8 1.8"
        />
      </svg>
    );
  }

  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="size-5">
      <path
        fill="currentColor"
        d="M15.5 14.5a6.5 6.5 0 0 1-8.4-8.4 7 7 0 1 0 8.4 8.4Z"
      />
    </svg>
  );
}

export function ThemeToggle() {
  const theme = useSyncExternalStore(subscribeTheme, themeFromDocument, () => "light" as const);

  return (
    <button
      type="button"
      aria-label={themeLabel(theme)}
      title="Switch between light and dark theme"
      className="cursor-pointer rounded p-2 text-ink"
      onClick={() => applyTheme(theme === "dark" ? "light" : "dark")}
    >
      <ThemeIcon theme={theme} />
    </button>
  );
}
