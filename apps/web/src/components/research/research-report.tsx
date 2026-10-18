import { CitationLink } from "@/components/research/citation-link";
import { sourceMap } from "@/lib/report";
import type { ReportStatus, ResearchReport } from "@/lib/types";

const STATUS_LABEL: Record<ReportStatus, string> = {
  completed: "Completed",
  partial: "Partial",
  insufficient_evidence: "Insufficient evidence",
};

export function ResearchReportView({ report }: { report: ResearchReport }) {
  const sources = sourceMap(report);
  const sections = Array.isArray(report.sections) ? report.sections : [];
  const limitations = Array.isArray(report.limitations) ? report.limitations : [];
  const prominent = report.status === "partial" || report.status === "insufficient_evidence";

  return (
    <section aria-labelledby="report-title" className="flex flex-col gap-4">
      <p className={prominent ? "font-semibold" : undefined}>Status: {STATUS_LABEL[report.status]}</p>
      <h2 id="report-title" className="break-words text-lg font-semibold">
        {report.title}
      </h2>
      <p className="break-words">{report.summary}</p>
      {sections.map((section, index) => {
        const citations = Array.isArray(section.citations) ? section.citations : [];
        return (
          <section key={`${section.heading}-${index}`} className="flex flex-col gap-2">
            <h3 className="break-words font-semibold">{section.heading}</h3>
            <p className="break-words">{section.content}</p>
            {citations.length > 0 ? (
              <ul aria-label={`Sources for ${section.heading}`} className="flex list-disc flex-col gap-1 pl-5">
                {citations.map((evidenceId, citationIndex) => (
                  <li key={`${evidenceId}-${citationIndex}`}>
                    <CitationLink evidenceId={evidenceId} sources={sources} />
                  </li>
                ))}
              </ul>
            ) : null}
          </section>
        );
      })}
      {limitations.length > 0 ? (
        <section className="flex flex-col gap-2">
          <h3 className="font-semibold">Limitations</h3>
          <ul className="flex list-disc flex-col gap-1 pl-5">
            {limitations.map((item, index) => (
              <li key={`${item}-${index}`} className="break-words">
                {item}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </section>
  );
}
