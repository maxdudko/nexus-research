"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useContext, useEffect, useRef, useState, useSyncExternalStore, type MouseEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { AdminNav } from "@/components/admin/admin-nav";
import { ThemeToggle } from "@/components/theme-toggle/theme-toggle";
import { ResearchActivityContext } from "@/components/research/research-activity";
import { Button } from "@/components/ui/button";

const WIDE_QUERY = "(min-width: 768px)";

const NAV = [
  { href: "/", label: "Dashboard" },
  { href: "/research/new", label: "New research" },
  { href: "/history", label: "History" },
  { href: "/account", label: "Account" },
] as const;

function navigation(role: string) {
  if (role !== "admin") return NAV;
  return [...NAV, { href: "/admin", label: "Admin" }];
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
  const items = navigation(role);

  return (
    <header
      className={
        wide
          ? "fixed inset-y-0 left-0 z-30 flex w-60 flex-col overflow-y-auto border-r border-line bg-surface"
          : "sticky top-0 z-20 border-b border-line bg-surface"
      }
    >
      <div className="flex items-center justify-between gap-3 px-4 py-4">
        <Link href="/" className="text-lg font-semibold tracking-tight" onClick={(event) => guard(event, "/")}>
          Nexus Research
        </Link>
        {wide ? null : (
          <Button
            ref={menuButtonRef}
            variant="secondary"
            aria-expanded={open}
            aria-controls="app-nav"
            onClick={() => setOpenPath(open ? null : pathname)}
          >
            Menu
          </Button>
        )}
      </div>
      {showNav ? (
        <nav
          id="app-nav"
          aria-label="Primary"
          className={wide ? "flex flex-1 flex-col gap-1 px-3" : "flex flex-col gap-1 border-t border-line px-3 py-3"}
        >
          {items.map((item, index) => {
            const current = pathname === item.href;
            return (
              <Link
                key={item.href}
                ref={index === 0 && !wide ? firstLinkRef : undefined}
                href={item.href}
                aria-current={current ? "page" : undefined}
                className="nav-link"
                onClick={(event) => guard(event, item.href)}
              >
                {item.label}
              </Link>
            );
          })}
          {role === "admin" ? <AdminNav onNavigate={guard} /> : null}
        </nav>
      ) : null}
      <div
        className={
          wide
            ? "mt-auto flex flex-col gap-3 border-t border-line px-4 py-4"
            : "flex flex-wrap items-center gap-3 border-t border-line px-4 py-3"
        }
      >
        <p className="text-sm break-all text-muted">
          Signed in as <span className="text-ink">{email}</span>
        </p>
        <div className="flex items-center gap-2">
          <Button ref={logoutRef} variant="secondary" onClick={requestLogout}>
            Log out
          </Button>
          <ThemeToggle />
        </div>
        {logoutError ? (
          <p role="alert" className="text-sm text-danger">
            {logoutError}
          </p>
        ) : null}
      </div>
      {pending
        ? createPortal(
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-overlay p-4">
              <div
                ref={dialogRef}
                role="dialog"
                aria-modal="true"
                aria-labelledby="leave-title"
                aria-describedby="leave-body"
                className="surface flex w-full max-w-md flex-col gap-4 p-5 shadow-[0_18px_50px_rgb(20_20_19/0.18)]"
              >
                <h2 id="leave-title" className="text-lg font-semibold tracking-tight">
                  {logoutCopy ? "Log out?" : "Leave this research?"}
                </h2>
                <p id="leave-body">
                  {logoutCopy
                    ? "Logging out stops this request. It does not keep running in the background."
                    : "Leaving stops this request. It does not keep running in the background."}
                </p>
                <div className="flex flex-wrap gap-3">
                  <Button ref={stayRef} variant="secondary" onClick={stay}>
                    Stay
                  </Button>
                  <Button ref={leaveRef} variant="primary" onClick={leave}>
                    {logoutCopy ? "Log out" : "Leave"}
                  </Button>
                </div>
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
    <div className="min-h-dvh bg-canvas md:pl-60">
      <AppHeader email={email} role={role} onLogout={onLogout} logoutError={logoutError} />
      <main key={userId} className="mx-auto w-full max-w-5xl px-4 py-8 md:px-8">
        {children}
      </main>
    </div>
  );
}
