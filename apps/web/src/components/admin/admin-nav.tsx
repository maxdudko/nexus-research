"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/admin", label: "Overview" },
  { href: "/admin/evaluations", label: "Evaluations" },
  { href: "/admin/diagnostics", label: "Diagnostics" },
  { href: "/admin/usage", label: "Usage" },
] as const;

export function AdminNav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Admin">
      <ul className="flex flex-wrap gap-3 text-sm">
        {LINKS.map((link) => {
          const current =
            link.href === "/admin"
              ? pathname === "/admin"
              : pathname === link.href || pathname.startsWith(`${link.href}/`);
          return (
            <li key={link.href}>
              <Link href={link.href} aria-current={current ? "page" : undefined} className="underline">
                {link.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
