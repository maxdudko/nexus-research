import { CitationLink } from "@/components/research/citation-link";
import { safeHttpUrl, sourceMap } from "@/lib/report";
import type { EvidenceRef, ReportBlock, ResearchReport } from "@/lib/types";

function Block({ block, sources }: { block: ReportBlock; sources: ReadonlyMap<string, EvidenceRef> }) {
  return (
    <div className="flex flex-col gap-2">
      {block.kind === "inference" ? <p className="text-sm font-medium text-muted">Analytical inference</p> : null}
      {block.text ? <p className="whitespace-pre-wrap break-words">{block.text}</p> : null}
      {block.kind === "table" ? (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left text-sm">
            <thead><tr>{block.columns.map((column, i) => <th key={i} scope="col" className="border border-line-strong p-2">{column}</th>)}</tr></thead>
            <tbody>{block.rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j} className="border border-line-strong p-2">{cell}</td>)}</tr>)}</tbody>
          </table>
        </div>
      ) : null}
      {block.citations.length ? (
        <ul className="flex flex-col gap-1 text-sm" aria-label="Evidence for this block">
          {block.citations.map((id) => {
            const source = sources.get(id);
            return <li key={id}>
              <CitationLink evidenceId={id} sources={sources} />
              {source?.excerpt ? <details className="mt-1"><summary className="cursor-pointer text-link">Read cited passage ({id})</summary><blockquote className="mt-2 whitespace-pre-wrap border-l-2 border-line-strong pl-3">{source.excerpt}</blockquote></details> : null}
            </li>;
          })}
        </ul>
      ) : null}
    </div>
  );
}

export function ReportV2({ report }: { report: ResearchReport }) {
  const sources = sourceMap(report);
  const documents = new Map<string, EvidenceRef>();
  for (const source of report.sources) if (!documents.has(source.source_id)) documents.set(source.source_id, source);
  return (
    <article aria-labelledby="report-title" className="flex flex-col gap-6 leading-relaxed">
      <p role="status">{report.status === "completed" ? "Completed" : report.status === "partial" ? "Partial" : "Insufficient evidence"}</p>
      <h2 id="report-title" className="break-words text-xl font-semibold">{report.title}</h2>
      <p className="text-sm text-muted">{report.verification === "passed" ? "Model support checks passed." : "Support checking is incomplete."} Model verification does not guarantee factual accuracy.</p>
      <section aria-label="Summary" className="flex flex-col gap-4">
        {report.summary_blocks?.length
          ? report.summary_blocks.map((block, i) => <Block key={i} block={block} sources={sources} />)
          : <p>{report.summary}</p>}
      </section>
      <nav aria-label="Report contents"><ol className="list-decimal pl-5">{report.sections.map((section, i) => <li key={i}><a href={`#report-section-${i}`} className="text-link">{section.heading}</a></li>)}</ol></nav>
      {report.sections.map((section, i) => <section id={`report-section-${i}`} key={i} className="flex scroll-mt-4 flex-col gap-4">
        <h3 className="text-lg font-semibold">{section.heading}</h3>
        {section.blocks?.map((block, j) => <Block key={j} block={block} sources={sources} />)}
      </section>)}
      {report.limitations.length ? <section className="surface p-4"><h3 className="font-semibold">Limitations</h3><ul className="list-disc pl-5">{report.limitations.map((item, i) => <li key={i}>{item}</li>)}</ul></section> : null}
      <section className="flex flex-col gap-3"><h3 className="font-semibold">Sources</h3>
        <ul className="flex flex-col gap-3">{Array.from(documents.values()).map((source) => {
          const url = safeHttpUrl(source.url);
          return <li key={source.source_id} className="break-words">
            {url ? <a href={url} target="_blank" rel="noopener noreferrer" className="text-link">{source.title || source.domain || url}</a> : <span>{source.title || "Uploaded PDF"}</span>}
            {source.domain ? <p className="text-sm text-muted">{source.domain}</p> : null}
            <p className="text-sm text-muted">Published: {source.published_at || "unknown"} · Retrieved: {source.retrieved_at || "unknown"}</p>
            {source.query ? <p className="text-sm text-muted">Found with: {source.query}</p> : null}
          </li>;
        })}</ul>
      </section>
    </article>
  );
}
