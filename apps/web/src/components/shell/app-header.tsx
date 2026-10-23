"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useContext, useEffect, useRef, useState, useSyncExternalStore, type MouseEvent, type ReactNode } from "react";

import { ThemeToggle } from "@/components/theme-toggle/theme-toggle";
import { ResearchActivityContext } from "@/components/research/research-activity";

const WIDE_QUERY = "(min-width: 768px)";

const NAV = [
  { href: "/", label: "Dashboard" },
  { href: "/research/new", label: "New research" },
  { href: "/history", label: "History" },
] as const;

function subscribeWide(onChange: () => void): () => void {
  const media = window.matchMedia(WIDE_QUERY);
  media.addEventListener("change", onChange);
  return () => media.removeEventListener("change", onChange);
}

function wideSnapshot(): boolean {
  return window.matchMedia(WIDE_QUERY).matches;
}

function useWideLayout(): boolean {
  return useSyncExternalStore(subscribeWide, wideSnapshot, () => true);
}

export function AppHeader({
  email,
  onLogout,
  logoutError,
}: {
  email: string;
  onLogout: () => void;
  logoutError: string | null;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const wide = useWideLayout();
  const activity = useContext(ResearchActivityContext);
  const busy = activity?.busy ?? false;
  const [openPath, setOpenPath] = useState<string | null>(null);
  const open = openPath === pathname;
  const [pendingHref, setPendingHref] = useState<string | null>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const firstLinkRef = useRef<HTMLAnchorElement>(null);
  const stayRef = useRef<HTMLButtonElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open || wide) return;
    firstLinkRef.current?.focus();
  }, [open, wide]);

  useEffect(() => {
    if (!open || wide) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setOpenPath(null);
      menuButtonRef.current?.focus();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, wide]);

  useEffect(() => {
    if (!pendingHref) return;
    stayRef.current?.focus();
  }, [pendingHref]);

  function guard(event: MouseEvent<HTMLAnchorElement>, href: string) {
    if (!busy || href === pathname) {
      setOpenPath(null);
      return;
    }
    event.preventDefault();
    returnFocus.current = event.currentTarget;
    setPendingHref(href);
  }

  function stay() {
    setPendingHref(null);
    returnFocus.current?.focus();
  }

  function leave() {
    if (!pendingHref) return;
    const href = pendingHref;
    setPendingHref(null);
    setOpenPath(null);
    router.push(href);
  }

  const showNav = wide || open;

  return (
    <header className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Link href="/" className="text-lg font-semibold" onClick={(event) => guard(event, "/")}>
          Nexus Research
        </Link>
        {wide ? null : (
          <button
            ref={menuButtonRef}
            type="button"
            className="cursor-pointer rounded border border-line-strong px-4 py-2"
            aria-expanded={open}
            aria-controls="app-nav"
            onClick={() => setOpenPath(open ? null : pathname)}
          >
            Menu
          </button>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-sm text-muted">
            Signed in as <span className="text-ink">{email}</span>
          </p>
          <button
            type="button"
            className="cursor-pointer rounded border border-line-strong px-4 py-2"
            onClick={onLogout}
          >
            Log out
          </button>
          <ThemeToggle />
        </div>
      </div>
      {showNav ? (
        <nav id="app-nav" aria-label="Primary" className={wide ? "flex flex-row gap-4" : "flex flex-col gap-2"}>
          {NAV.map((item, index) => (
            <Link
              key={item.href}
              ref={index === 0 && !wide ? firstLinkRef : undefined}
              href={item.href}
              aria-current={pathname === item.href ? "page" : undefined}
              className="underline"
              onClick={(event) => guard(event, item.href)}
            >
              {item.label}
            </Link>
          ))}
        </nav>
      ) : null}
      {logoutError ? (
        <p role="alert" className="text-sm text-danger">
          {logoutError}
        </p>
      ) : null}
      {pendingHref ? (
        <div role="dialog" aria-labelledby="leave-title" className="flex flex-col gap-3 rounded border border-line bg-surface p-4">
          <h2 id="leave-title" className="text-lg font-semibold">
            Leave this research?
          </h2>
          <p>Leaving stops this request. It does not keep running in the background.</p>
          <div className="flex flex-wrap gap-3">
            <button ref={stayRef} type="button" className="cursor-pointer rounded border border-line-strong px-4 py-2" onClick={stay}>
              Stay
            </button>
            <button type="button" className="cursor-pointer rounded bg-primary px-4 py-2 text-on-primary" onClick={leave}>
              Leave
            </button>
          </div>
        </div>
      ) : null}
    </header>
  );
}

export function AuthenticatedFrame({
  email,
  userId,
  onLogout,
  logoutError,
  children,
}: {
  email: string;
  userId: string;
  onLogout: () => void;
  logoutError: string | null;
  children: ReactNode;
}) {
  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-8 px-4 py-8">
      <AppHeader email={email} onLogout={onLogout} logoutError={logoutError} />
      <main key={userId}>{children}</main>
    </div>
  );
}
