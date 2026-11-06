"use client";

import type { ReactNode } from "react";

import { useSessionIdentity } from "@/components/auth/session-boundary";

export default function AdminLayout({ children }: { children: ReactNode }) {
  const identity = useSessionIdentity();
  if (identity?.role !== "admin") {
    return (
      <p role="alert" className="text-sm text-danger">
        The request was rejected.
      </p>
    );
  }
  return children;
}
