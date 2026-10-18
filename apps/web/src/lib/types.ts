/**
 * Hand-written contracts for the research API.
 * Matches apps/api/src/nexus/schemas/history.py, report.py, evidence.py,
 * and apps/api/src/nexus/research/events.py.
 */

export type ReportStatus = "completed" | "partial" | "insufficient_evidence";

export type RunStatus = ReportStatus | "running" | "failed" | "interrupted";

export type StageName =
  | "planning"
  | "discovery"
  | "ingestion"
  | "retrieval"
  | "evaluation"
  | "synthesis";

export interface GraphUsage {
  iterations: number;
  search_calls: number;
  llm_calls: number;
  pages_indexed: number;
  pages_failed: number;
}

export interface EvidenceRef {
  evidence_id: string;
  source_id: string;
  chunk_id: string;
  research_id: string;
  url: string | null;
  page_number: number | null;
}

export interface ReportSection {
  heading: string;
  content: string;
  citations: string[];
}

export interface ResearchReport {
  title: string;
  summary: string;
  sections: ReportSection[];
  limitations: string[];
  status: ReportStatus;
  sources: EvidenceRef[];
}

export interface RunSummary {
  run_id: string;
  research_id: string;
  question: string;
  status: RunStatus;
  error_code: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface RunDetail extends RunSummary {
  warnings: string[];
  usage: GraphUsage | null;
  started_at: string | null;
  updated_at: string;
}

export interface RunListResponse {
  items: RunSummary[];
  next_cursor: string | null;
}

export interface RunDetailResponse {
  run: RunDetail;
  report: ResearchReport | null;
}

export interface IngestionResponse {
  source_id: string;
  research_id: string;
  status: "indexed";
  chunk_count: number;
  deduplicated: boolean;
}

export interface ResearchRunRequest {
  question: string;
  research_id: string | null;
}

export interface ProgressCounts {
  pages_indexed: number;
  search_calls: number;
}

export interface ResearchStarted {
  version: 1;
  run_id: string;
  research_id: string;
  status: "running";
}

export interface ResearchProgress {
  version: 1;
  run_id: string;
  stage: StageName;
  iteration: number;
  message: string;
  counts: ProgressCounts;
}

export interface ResearchCompleted {
  version: 1;
  run_id: string;
  research_id: string;
  status: ReportStatus;
}

export interface ResearchFailed {
  version: 1;
  run_id: string;
  code: string;
}

export interface ResearchInterrupted {
  version: 1;
  run_id: string;
  code: "interrupted" | "deadline_exceeded";
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
  };
}
