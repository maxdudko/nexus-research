"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { AuthenticatedFrame } from "@/components/shell/app-header";
import { ResearchActivityProvider } from "@/components/research/research-activity";
import { fetchIdentity, type Identity } from "@/lib/identity";
import { csrfHeaders } from "@/lib/csrf";
import { loginHref } from "@/lib/return-path";

type Phase =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "unavailable" }
  | { status: "ready"; identity: Identity };

export function SessionBoundary({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [attempt, setAttempt] = useState(0);
  const [phase, setPhase] = useState<Phase>({ status: "loading" });
  const [logoutError, setLogoutError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void fetchIdentity().then((result) => {
      if (!cancelled) setPhase(result);
    });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  useEffect(() => {
    if (phase.status !== "anonymous") return;
    router.replace(loginHref(pathname));
  }, [pathname, phase, router]);

  async function logout() {
    setLogoutError(null);
    try {
      const response = await fetch("/api/v1/auth/logout", {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: csrfHeaders(),
      });
      if (response.ok || response.status === 401) {
        router.replace("/login");
        return;
      }
    } catch {
      setLogoutError("Log out did not finish. Try again.");
      return;
    }
    setLogoutError("Log out did not finish. Try again.");
  }

  if (phase.status === "loading") {
    return (
      <main className="mx-auto w-full max-w-2xl px-4 py-8">
        <p role="status">Checking session.</p>
      </main>
    );
  }

  if (phase.status === "anonymous") {
    return (
      <main className="mx-auto w-full max-w-2xl px-4 py-8">
        <p role="status">Sign-in is required.</p>
      </main>
    );
  }

  if (phase.status === "unavailable") {
    return (
      <main className="mx-auto flex w-full max-w-2xl flex-col gap-4 px-4 py-8">
        <p role="alert">The research service is unavailable.</p>
        <button
          type="button"
          className="w-fit cursor-pointer rounded border border-line-strong px-4 py-2"
          onClick={() => {
            setPhase({ status: "loading" });
            setAttempt((current) => current + 1);
          }}
        >
          Try again
        </button>
      </main>
    );
  }

  return (
    <AuthenticatedFrame
      email={phase.identity.email}
      userId={phase.identity.id}
      logoutError={logoutError}
      onLogout={() => {
        void logout();
      }}
    >
      {children}
    </AuthenticatedFrame>
  );
}

export function AuthenticatedLayout({ children }: { children: ReactNode }) {
  return (
    <ResearchActivityProvider>
      <SessionBoundary>{children}</SessionBoundary>
    </ResearchActivityProvider>
  );
}
