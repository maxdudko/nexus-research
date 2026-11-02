// @vitest-environment jsdom

import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import LandingPage from "@/app/page";

afterEach(() => vi.unstubAllGlobals());

describe("public landing page", () => {
  it("renders without identity or research requests and provides working navigation targets", () => {
    const fetchImpl = vi.fn(() => { throw new Error("API is unavailable"); });
    vi.stubGlobal("fetch", fetchImpl);
    const document = new DOMParser().parseFromString(renderToStaticMarkup(<LandingPage />), "text/html");

    expect(document.querySelector("h1")?.textContent).toBe("From a question to a report you can inspect.");
    expect(document.body.textContent).toContain("Illustrative example");
    expect(document.body.textContent).toContain("It does not guarantee that a conclusion is correct.");
    expect(document.querySelectorAll('a[href="/register"]')).toHaveLength(3);
    expect(document.querySelectorAll('a[href="/login"]')).toHaveLength(2);
    for (const link of document.querySelectorAll<HTMLAnchorElement>('a[href^="#"]')) {
      expect(document.getElementById(link.getAttribute("href")!.slice(1))).not.toBeNull();
    }
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
