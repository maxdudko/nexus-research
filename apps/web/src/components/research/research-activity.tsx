"use client";

import { createContext, useMemo, useState, type ReactNode } from "react";

interface ResearchActivityValue {
  busy: boolean;
  setBusy: (busy: boolean) => void;
}

export const ResearchActivityContext = createContext<ResearchActivityValue | null>(null);

export function ResearchActivityProvider({ children }: { children: ReactNode }) {
  const [busy, setBusy] = useState(false);
  const value = useMemo(() => ({ busy, setBusy }), [busy]);
  return <ResearchActivityContext.Provider value={value}>{children}</ResearchActivityContext.Provider>;
}
