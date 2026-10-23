"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { RunSummaryItem } from "@/components/research/run-summary";
import { listRuns } from "@/lib/api";
import { isApiError, REQUEST_FAILED_MESSAGE } from "@/lib/errors";
import { loginHref } from "@/lib/return-path";
import type { RunSummary } from "@/lib/types";

function dedupe(items: RunSummary[]): RunSummary[] {
  const seen = new Set<string>();
  const unique: RunSummary[] = [];
  for (const item of items) {
    if (seen.has(item.run_id)) continue;
    seen.add(item.run_id);
    unique.push(item);
  }
  return unique;
}

function failureText(cause: unknown): string {
  return isApiError(cause) ? cause.message : REQUEST_FAILED_MESSAGE;
}

export function ResearchHistory({
  limit = 20,
  variant = "history",
}: {
  limit?: number;
  variant?: "history" | "recent";
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [redirecting, setRedirecting] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const generation = useRef(0);

  useEffect(() => {
    const current = generation.current + 1;
    generation.current = current;
    let cancelled = false;
    listRuns(limit)
      .then((page) => {
        if (cancelled || current !== generation.current) return;
        setRuns(dedupe(page.items));
        setCursor(page.next_cursor);
      })
      .catch((cause: unknown) => {
        if (cancelled || current !== generation.current) return;
        if (isApiError(cause) && cause.status === 401) {
          setRedirecting(true);
          router.replace(loginHref(pathname));
          return;
        }
        setError(failureText(cause));
      })
      .finally(() => {
        if (!cancelled && current === generation.current) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [attempt, limit, pathname, router]);

  async function loadOlder(): Promise<void> {
    if (!cursor || loadingOlder || variant !== "history") return;
    const current = generation.current;
    setLoadingOlder(true);
    setError(null);
    try {
      const page = await listRuns(limit, cursor);
      if (current !== generation.current) return;
      setRuns((items) => dedupe([...items, ...page.items]));
      setCursor(page.next_cursor);
    } catch (cause: unknown) {
      if (current !== generation.current) return;
      if (isApiError(cause) && cause.status === 401) {
        setRedirecting(true);
        router.replace(loginHref(pathname));
        return;
      }
      setError(failureText(cause));
    } finally {
      if (current === generation.current) setLoadingOlder(false);
    }
  }

  function retry() {
    if (runs.length === 0) {
      setLoading(true);
      setError(null);
      setAttempt((current) => current + 1);
      return;
    }
    void loadOlder();
  }

  if (redirecting) {
    return <p role="status">Sign-in is required.</p>;
  }

  const heading = variant === "recent" ? "Recent research" : "Research runs";
  const headingId = variant === "recent" ? "recent-heading" : "history-heading";

  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      <h2 id={headingId} className="text-lg font-semibold">
        {heading}
      </h2>
      {loading ? <p>{variant === "recent" ? "Loading recent runs." : "Loading history."}</p> : null}
      {error ? (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      ) : null}
      {error ? (
        <button
          type="button"
          className="w-fit cursor-pointer rounded border border-line-strong px-4 py-2"
          onClick={retry}
        >
          Try again
        </button>
      ) : null}
      {!loading && !error && runs.length === 0 ? <p>No research runs yet.</p> : null}
      <ul className="flex flex-col gap-3">
        {runs.map((run) => (
          <RunSummaryItem key={run.run_id} run={run} />
        ))}
      </ul>
      {variant === "recent" && cursor ? (
        <Link href="/history" className="underline">
          View history
        </Link>
      ) : null}
      {variant === "history" && cursor ? (
        <button
          type="button"
          className={`w-fit rounded border border-line-strong px-4 py-2 ${loadingOlder ? "cursor-not-allowed" : "cursor-pointer"}`}
          disabled={loadingOlder}
          onClick={() => {
            void loadOlder();
          }}
        >
          {loadingOlder ? "Loading older runs." : "Older runs"}
        </button>
      ) : null}
    </section>
  );
}
