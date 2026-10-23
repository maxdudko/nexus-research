import Link from "next/link";

import { ResearchHistory } from "@/components/research/research-history";

export default function DashboardPage() {
  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-3">
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <p className="text-sm text-muted">
          Nexus answers a question from public web pages and an optional text PDF. The request runs
          only while it is open. A citation is a retrieved source, not proof the report is correct.
        </p>
        <Link
          href="/research/new"
          className="w-fit rounded bg-primary px-4 py-2 text-on-primary"
        >
          New research
        </Link>
      </div>
      <ResearchHistory limit={5} variant="recent" />
    </div>
  );
}
