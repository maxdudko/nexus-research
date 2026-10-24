"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useContext, useEffect, useRef, useState, useSyncExternalStore, type MouseEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { ThemeToggle } from "@/components/theme-toggle/theme-toggle";
import { ResearchActivityContext } from "@/components/research/research-activity";

const WIDE_QUERY = "(min-width: 768px)";

const NAV = [
  { href: "/", label: "Dashboard" },
  { href: "/research/new", label: "New research" },
  { href: "/history", label: "History" },
] as const;

function navigation(role: string) {
  if (role !== "admin") return NAV;
  return [...NAV, { href: "/admin/evaluations", label: "Admin" }];
}

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
  role,
  onLogout,
  logoutError,
}: {
  email: string;
  role: string;
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
  const [pending, setPending] = useState<{ kind: "navigate"; href: string } | { kind: "logout" } | null>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const logoutRef = useRef<HTMLButtonElement>(null);
  const firstLinkRef = useRef<HTMLAnchorElement>(null);
  const stayRef = useRef<HTMLButtonElement>(null);
  const leaveRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open || wide) return;
    firstLinkRef.current?.focus();
  }, [open, wide]);

  useEffect(() => {
    if (!open || wide || pending) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setOpenPath(null);
      menuButtonRef.current?.focus();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, pending, wide]);

  useEffect(() => {
    if (!pending) {
      returnFocus.current?.focus();
      return;
    }
    const dialog = dialogRef.current;
    const inerted: HTMLElement[] = [];
    for (const child of document.body.children) {
      if (!(child instanceof HTMLElement)) continue;
      if (dialog !== null && (child === dialog || child.contains(dialog))) continue;
      if (child.hasAttribute("inert")) continue;
      child.setAttribute("inert", "");
      inerted.push(child);
    }
    stayRef.current?.focus();
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        setPending(null);
        return;
      }
      if (event.key !== "Tab") return;
      const nodes = [stayRef.current, leaveRef.current].filter(
        (node): node is HTMLButtonElement => node !== null,
      );
      if (nodes.length === 0) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      const active = document.activeElement;
      const inside = nodes.includes(active as HTMLButtonElement);
      if (event.shiftKey && (active === first || !inside)) {
        event.preventDefault();
        last.focus();
        return;
      }
      if (!event.shiftKey && (active === last || !inside)) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      for (const node of inerted) node.removeAttribute("inert");
    };
  }, [pending]);

  function guard(event: MouseEvent<HTMLAnchorElement>, href: string) {
    if (!busy || href === pathname) {
      setOpenPath(null);
      return;
    }
    event.preventDefault();
    returnFocus.current = event.currentTarget;
    setPending({ kind: "navigate", href });
  }

  function requestLogout() {
    if (!busy) {
      onLogout();
      return;
    }
    returnFocus.current = logoutRef.current;
    setPending({ kind: "logout" });
  }

  function stay() {
    setPending(null);
  }

  function leave() {
    if (!pending) return;
    const action = pending;
    setPending(null);
    setOpenPath(null);
    activity?.stop();
    if (action.kind === "logout") {
      onLogout();
      return;
    }
    router.push(action.href);
  }

  const showNav = wide || open;

  const logoutCopy = pending?.kind === "logout";

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
            ref={logoutRef}
            type="button"
            className="cursor-pointer rounded border border-line-strong px-4 py-2"
            onClick={requestLogout}
          >
            Log out
          </button>
          <ThemeToggle />
        </div>
      </div>
      {showNav ? (
        <nav
          id="app-nav"
          aria-label="Primary"
          className={wide ? "flex flex-row gap-4" : "flex flex-col gap-2"}
        >
          {navigation(role).map((item, index) => (
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
      {pending
        ? createPortal(
            <div
              ref={dialogRef}
              role="dialog"
              aria-modal="true"
              aria-labelledby="leave-title"
              aria-describedby="leave-body"
              className="flex flex-col gap-3 rounded border border-line bg-surface p-4"
            >
              <h2 id="leave-title" className="text-lg font-semibold">
                {logoutCopy ? "Log out?" : "Leave this research?"}
              </h2>
              <p id="leave-body">
                {logoutCopy
                  ? "Logging out stops this request. It does not keep running in the background."
                  : "Leaving stops this request. It does not keep running in the background."}
              </p>
              <div className="flex flex-wrap gap-3">
                <button ref={stayRef} type="button" className="cursor-pointer rounded border border-line-strong px-4 py-2" onClick={stay}>
                  Stay
                </button>
                <button
                  ref={leaveRef}
                  type="button"
                  className="cursor-pointer rounded bg-primary px-4 py-2 text-on-primary"
                  onClick={leave}
                >
                  {logoutCopy ? "Log out" : "Leave"}
                </button>
              </div>
            </div>,
            document.body,
          )
        : null}
    </header>
  );
}

export function AuthenticatedFrame({
  email,
  role,
  userId,
  onLogout,
  logoutError,
  children,
}: {
  email: string;
  role: string;
  userId: string;
  onLogout: () => void;
  logoutError: string | null;
  children: ReactNode;
}) {
  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-8 px-4 py-8">
      <AppHeader email={email} role={role} onLogout={onLogout} logoutError={logoutError} />
      <main key={userId}>{children}</main>
    </div>
  );
}
