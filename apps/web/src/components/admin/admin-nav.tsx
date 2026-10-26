"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { MouseEvent } from "react";

const LINKS = [
  { href: "/admin", label: "Overview" },
  { href: "/admin/evaluations", label: "Evaluations" },
  { href: "/admin/diagnostics", label: "Diagnostics" },
  { href: "/admin/usage", label: "Usage" },
] as const;

export function AdminNav({
  onNavigate,
}: {
  onNavigate: (event: MouseEvent<HTMLAnchorElement>, href: string) => void;
}) {
  const pathname = usePathname();
  return (
    <div className="mt-3 border-t border-line pt-3">
      <ul className="flex flex-col gap-1">
        {LINKS.map((link) => {
          const current =
            link.href === "/admin"
              ? pathname === "/admin"
              : pathname === link.href || pathname.startsWith(`${link.href}/`);
          return (
            <li key={link.href}>
              <Link
                href={link.href}
                aria-current={current ? "page" : undefined}
                className="nav-link"
                onClick={(event) => onNavigate(event, link.href)}
              >
                {link.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
