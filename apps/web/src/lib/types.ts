/**
 * Hand-written contracts for the research API.
 * Matches apps/api/src/nexus/schemas/history.py, report.py, evidence.py,
 * and apps/api/src/nexus/research/events.py.
 */

export type ResearchDepth = "standard" | "deep";

export type ReportStatus = "completed" | "partial" | "insufficient_evidence";

export type RunStatus = ReportStatus | "running" | "failed" | "interrupted";

export type StageName =
  | "planning"
  | "discovery"
  | "ingestion"
  | "retrieval"
  | "analysis"
  | "verification"
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
  title?: string | null;
  domain?: string | null;
  published_at?: string | null;
  retrieved_at?: string | null;
  query?: string | null;
  excerpt?: string | null;
}

export interface ReportBlock {
  kind: "fact" | "inference" | "context" | "table";
  text: string;
  citations: string[];
  columns: string[];
  rows: string[][];
}

export interface ReportSection {
  task_ids?: string[];
  blocks?: ReportBlock[];
  heading: string;
  content: string;
  citations: string[];
}

export interface ResearchReport {
  format_version?: 1 | 2;
  body_gaps?: string[];
  summary_blocks?: ReportBlock[];
  verification?: "not_checked" | "passed" | "incomplete";
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
  depth?: ResearchDepth;
  applied_limits?: Record<string, unknown>;
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
  depth?: ResearchDepth;
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
