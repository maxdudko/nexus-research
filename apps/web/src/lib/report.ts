import type { EvidenceRef, ResearchReport } from "@/lib/types";

export interface CitationSourceView {
  kind: "source";
  evidenceId: string;
  href: string | null;
  pageLabel: string | null;
}

export interface CitationUnknownView {
  kind: "unknown";
  evidenceId: string;
}

export type CitationView = CitationSourceView | CitationUnknownView;

export function safeHttpUrl(value: string | null | undefined): string | null {
  if (typeof value !== "string" || value.trim() === "") return null;
  try {
    const url = new URL(value.trim());
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    return url.toString();
  } catch {
    return null;
  }
}

export function sourceMap(
  report: Pick<ResearchReport, "sources"> | null | undefined,
): Map<string, EvidenceRef> {
  const map = new Map<string, EvidenceRef>();
  if (!report || !Array.isArray(report.sources)) return map;
  for (const source of report.sources) {
    if (source && typeof source.evidence_id === "string" && source.evidence_id.length > 0) {
      map.set(source.evidence_id, source);
    }
  }
  return map;
}

export function citationView(
  evidenceId: string,
  sources: ReadonlyMap<string, EvidenceRef>,
): CitationView {
  const ref = sources.get(evidenceId);
  if (!ref) return { kind: "unknown", evidenceId };
  const pageLabel =
    typeof ref.page_number === "number" && Number.isInteger(ref.page_number) && ref.page_number >= 1
      ? `PDF · page ${ref.page_number}`
      : null;
  return {
    kind: "source",
    evidenceId,
    href: safeHttpUrl(ref.url),
    pageLabel,
  };
}
