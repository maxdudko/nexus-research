"use client";

import { AuthenticatedLayout } from "@/components/auth/session-boundary";
import type { ReactNode } from "react";

export default function AppGroupLayout({ children }: { children: ReactNode }) {
  return <AuthenticatedLayout>{children}</AuthenticatedLayout>;
}
