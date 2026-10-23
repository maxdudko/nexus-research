"use client";

import { createContext, useCallback, useMemo, useRef, useState, type ReactNode } from "react";

interface ResearchActivityValue {
  busy: boolean;
  setBusy: (busy: boolean) => void;
  registerStop: (stop: (() => void) | null) => void;
  stop: () => void;
}

export const ResearchActivityContext = createContext<ResearchActivityValue | null>(null);

export function ResearchActivityProvider({ children }: { children: ReactNode }) {
  const [busy, setBusy] = useState(false);
  const stopRef = useRef<(() => void) | null>(null);
  const registerStop = useCallback((stop: (() => void) | null) => {
    stopRef.current = stop;
  }, []);
  const stop = useCallback(() => {
    stopRef.current?.();
  }, []);
  const value = useMemo(
    () => ({ busy, setBusy, registerStop, stop }),
    [busy, registerStop, stop],
  );
  return <ResearchActivityContext.Provider value={value}>{children}</ResearchActivityContext.Provider>;
}
