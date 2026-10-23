import { citationView } from "@/lib/report";
import type { EvidenceRef } from "@/lib/types";

export function CitationLink({
  evidenceId,
  sources,
}: {
  evidenceId: string;
  sources: ReadonlyMap<string, EvidenceRef>;
}) {
  const view = citationView(evidenceId, sources);
  if (view.kind === "unknown") {
    return <span>Unknown source ({view.evidenceId})</span>;
  }

  const pageText = view.pageLabel
    ? view.href
      ? ` ${view.pageLabel}`
      : ` ${view.pageLabel}. The uploaded file is not stored, so this page cannot be opened.`
    : null;

  return (
    <span className="break-words">
      {view.href ? (
        <a href={view.href} target="_blank" rel="noopener noreferrer">
          {view.evidenceId}
        </a>
      ) : (
        <span>{view.evidenceId}</span>
      )}
      {view.href ? <span className="break-all">{` ${view.href}`}</span> : null}
      {pageText ? <span>{pageText}</span> : null}
    </span>
  );
}
