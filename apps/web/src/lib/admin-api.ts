import { ApiError, errorFromResponse } from "@/lib/errors";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const METRIC = /^\d+\.\d{6}$/;
const SIGNED_METRIC = /^-?\d+\.\d{6}$/;
const TEMPERATURE = /^\d+\.\d{3}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const REVISION = /^[0-9a-f]{40}$/;
const LABELS = ["supported", "unsupported", "insufficient_context", "contradictory"] as const;

type FetchLike = typeof fetch;

export type RetrievalSummary = {
  recall_at_k: string | null;
  precision_at_k: string | null;
  mrr_at_k: string | null;
  defined_cases: number;
  undefined_cases: number;
};

export type CitationSummary = { cases: number; valid: number; invalid: number };

export type GroundingSummary = {
  cases: number;
  structurally_valid: number;
  structurally_invalid: number;
};

export type JudgeCounts = {
  attempted: number;
  succeeded: number;
  failed: number;
  skipped: number;
  disagreed: number;
};

export type Calibration = {
  labels: string[];
  matrix: Record<string, Record<string, number>>;
  compared: number;
  agreements: number;
  agreement: string | null;
};

export type EvaluationListItem = {
  id: string;
  suite: "offline" | "live";
  outcome: "passed" | "failed" | "informational";
  imported_at: string;
  executed_at: null;
  dataset_version: string;
  dataset_sha256: string;
  report_version: "1";
  scorer_version: "1";
  k: number;
  code_revision: string | null;
  code_revision_source: "operator_asserted" | null;
  provenance_level: "offline_recomputed" | "operator_imported";
  is_committed_baseline: boolean;
  model: string | null;
  prompt_version: string | null;
  temperature: string | null;
  pricing_version: string | null;
  retrieval: RetrievalSummary;
  citations: CitationSummary;
  grounding: GroundingSummary;
  counts: JudgeCounts | null;
  agreement: string | null;
  compared: number | null;
  agreements: number | null;
};

export type EvaluationConfig = {
  empty_gold: "undefined";
  metric_quant: "0.000001";
  label_taxonomy: string[] | null;
};

export type EvaluationDetail = EvaluationListItem & {
  config: EvaluationConfig;
  calibration: Calibration | null;
};

export type StoredCase = {
  category: "retrieval" | "citation" | "grounding";
  case_id: string;
  result: Record<string, string | number | boolean | null | string[]>;
};

export type EvaluationDeltas = {
  retrieval: {
    recall_at_k: string | null;
    precision_at_k: string | null;
    mrr_at_k: string | null;
    defined_cases: number;
    undefined_cases: number;
  };
  citations: CitationSummary;
  grounding: GroundingSummary;
  agreement: string | null;
  counts: JudgeCounts | null;
  matrix: Record<string, Record<string, number>> | null;
};

export type EvaluationComparison = {
  comparable: boolean;
  reasons: string[];
  warnings: string[];
  cost_comparable: boolean;
  pricing_reason: "pricing_unavailable" | "pricing_version_mismatch" | null;
  left: EvaluationDetail;
  right: EvaluationDetail;
  deltas: EvaluationDeltas | null;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function bad(status: number): ApiError {
  return new ApiError("request_failed", "The request could not be completed.", status);
}

function text(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function metric(value: unknown, signed = false): string | null | undefined {
  if (value === null) return null;
  if (typeof value !== "string") return undefined;
  const pattern = signed ? SIGNED_METRIC : METRIC;
  return pattern.test(value) ? value : undefined;
}

function whole(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) ? value : null;
}

function parseRetrieval(value: unknown, status: number): RetrievalSummary {
  if (!isRecord(value)) throw bad(status);
  const recall = metric(value.recall_at_k);
  const precision = metric(value.precision_at_k);
  const mrr = metric(value.mrr_at_k);
  const defined = whole(value.defined_cases);
  const undefinedCases = whole(value.undefined_cases);
  if (recall === undefined || precision === undefined || mrr === undefined) throw bad(status);
  if (defined === null || undefinedCases === null) throw bad(status);
  return {
    recall_at_k: recall,
    precision_at_k: precision,
    mrr_at_k: mrr,
    defined_cases: defined,
    undefined_cases: undefinedCases,
  };
}

function parseCitations(value: unknown, status: number): CitationSummary {
  if (!isRecord(value)) throw bad(status);
  const cases = whole(value.cases);
  const valid = whole(value.valid);
  const invalid = whole(value.invalid);
  if (cases === null || valid === null || invalid === null || valid + invalid !== cases) throw bad(status);
  return { cases, valid, invalid };
}

function parseGrounding(value: unknown, status: number): GroundingSummary {
  if (!isRecord(value)) throw bad(status);
  const cases = whole(value.cases);
  const structurallyValid = whole(value.structurally_valid);
  const structurallyInvalid = whole(value.structurally_invalid);
  if (
    cases === null ||
    structurallyValid === null ||
    structurallyInvalid === null ||
    structurallyValid + structurallyInvalid !== cases
  ) {
    throw bad(status);
  }
  return {
    cases,
    structurally_valid: structurallyValid,
    structurally_invalid: structurallyInvalid,
  };
}

function parseCounts(value: unknown, status: number): JudgeCounts {
  if (!isRecord(value)) throw bad(status);
  const attempted = whole(value.attempted);
  const succeeded = whole(value.succeeded);
  const failed = whole(value.failed);
  const skipped = whole(value.skipped);
  const disagreed = whole(value.disagreed);
  if (attempted === null || succeeded === null || failed === null || skipped === null || disagreed === null) {
    throw bad(status);
  }
  return { attempted, succeeded, failed, skipped, disagreed };
}

function parseMatrix(value: unknown, labels: string[], status: number): Record<string, Record<string, number>> {
  if (!isRecord(value)) throw bad(status);
  const matrix: Record<string, Record<string, number>> = {};
  for (const gold of labels) {
    const row = value[gold];
    if (!isRecord(row)) throw bad(status);
    const parsed: Record<string, number> = {};
    for (const predicted of labels) {
      const cell = whole(row[predicted]);
      if (cell === null) throw bad(status);
      parsed[predicted] = cell;
    }
    matrix[gold] = parsed;
  }
  return matrix;
}

function parseCalibration(value: unknown, status: number): Calibration {
  if (!isRecord(value) || !Array.isArray(value.labels)) throw bad(status);
  const labels = value.labels.filter((label): label is string => typeof label === "string");
  if (labels.length !== LABELS.length || labels.some((label, index) => label !== LABELS[index])) throw bad(status);
  const compared = whole(value.compared);
  const agreements = whole(value.agreements);
  const agreement = metric(value.agreement);
  if (compared === null || agreements === null || agreement === undefined) throw bad(status);
  return {
    labels,
    matrix: parseMatrix(value.matrix, labels, status),
    compared,
    agreements,
    agreement,
  };
}

function parseItem(value: unknown, status: number): EvaluationListItem {
  if (!isRecord(value)) throw bad(status);
  if (value.suite !== "offline" && value.suite !== "live") throw bad(status);
  if (value.outcome !== "passed" && value.outcome !== "failed" && value.outcome !== "informational") {
    throw bad(status);
  }
  if (typeof value.id !== "string" || !UUID.test(value.id)) throw bad(status);
  if (typeof value.imported_at !== "string" || value.executed_at !== null) throw bad(status);
  if (typeof value.dataset_version !== "string" || !SHA256.test(text(value.dataset_sha256) ?? "")) throw bad(status);
  if (value.report_version !== "1" || value.scorer_version !== "1") throw bad(status);
  const k = whole(value.k);
  if (k === null || k < 1 || k > 100) throw bad(status);
  const revision = value.code_revision;
  const source = value.code_revision_source;
  if (!((revision === null && source === null) || (typeof revision === "string" && REVISION.test(revision) && source === "operator_asserted"))) {
    throw bad(status);
  }
  if (value.provenance_level !== "offline_recomputed" && value.provenance_level !== "operator_imported") {
    throw bad(status);
  }
  if (typeof value.is_committed_baseline !== "boolean") throw bad(status);
  const live = value.suite === "live";
  const model = value.model;
  const prompt = value.prompt_version;
  const temperature = value.temperature;
  const pricing = value.pricing_version;
  if (live) {
    if (typeof model !== "string" || typeof prompt !== "string" || typeof pricing !== "string") throw bad(status);
    if (typeof temperature !== "string" || !TEMPERATURE.test(temperature)) throw bad(status);
  } else if (model !== null || prompt !== null || temperature !== null || pricing !== null) {
    throw bad(status);
  }
  const agreement = metric(value.agreement);
  const compared = value.compared === null ? null : whole(value.compared);
  const agreements = value.agreements === null ? null : whole(value.agreements);
  if (agreement === undefined) throw bad(status);
  const counts = live ? parseCounts(value.counts, status) : null;
  if (!live && value.counts !== null) throw bad(status);
  if (live && (compared === null || agreements === null)) throw bad(status);
  if (!live && (agreement !== null || compared !== null || agreements !== null)) throw bad(status);
  return {
    id: value.id,
    suite: value.suite,
    outcome: value.outcome,
    imported_at: value.imported_at,
    executed_at: null,
    dataset_version: value.dataset_version,
    dataset_sha256: text(value.dataset_sha256) ?? "",
    report_version: "1",
    scorer_version: "1",
    k,
    code_revision: typeof revision === "string" ? revision : null,
    code_revision_source: source === "operator_asserted" ? source : null,
    provenance_level: value.provenance_level,
    is_committed_baseline: value.is_committed_baseline,
    model: typeof model === "string" ? model : null,
    prompt_version: typeof prompt === "string" ? prompt : null,
    temperature: typeof temperature === "string" ? temperature : null,
    pricing_version: typeof pricing === "string" ? pricing : null,
    retrieval: parseRetrieval(value.retrieval, status),
    citations: parseCitations(value.citations, status),
    grounding: parseGrounding(value.grounding, status),
    counts,
    agreement,
    compared,
    agreements,
  };
}

function parseConfig(value: unknown, live: boolean, status: number): EvaluationConfig {
  if (!isRecord(value) || value.empty_gold !== "undefined" || value.metric_quant !== "0.000001") throw bad(status);
  if (!live) {
    return { empty_gold: "undefined", metric_quant: "0.000001", label_taxonomy: null };
  }
  if (!Array.isArray(value.label_taxonomy)) throw bad(status);
  const labels = value.label_taxonomy.filter((label): label is string => typeof label === "string");
  if (labels.length !== LABELS.length || labels.some((label, index) => label !== LABELS[index])) throw bad(status);
  return { empty_gold: "undefined", metric_quant: "0.000001", label_taxonomy: labels };
}

function parseDetail(value: unknown, status: number): EvaluationDetail {
  const item = parseItem(value, status);
  if (!isRecord(value)) throw bad(status);
  const live = item.suite === "live";
  const calibration = live ? parseCalibration(value.calibration, status) : null;
  if (!live && value.calibration !== null) throw bad(status);
  if (live && calibration && (calibration.compared !== item.compared || calibration.agreements !== item.agreements || calibration.agreement !== item.agreement)) {
    throw bad(status);
  }
  return {
    ...item,
    config: parseConfig(value.config, live, status),
    calibration,
  };
}

function parseCase(value: unknown, status: number): StoredCase {
  if (!isRecord(value) || !isRecord(value.result)) throw bad(status);
  if (value.category !== "retrieval" && value.category !== "citation" && value.category !== "grounding") {
    throw bad(status);
  }
  if (typeof value.case_id !== "string") throw bad(status);
  const source = value.result;
  const result: StoredCase["result"] = {};
  if (value.category === "retrieval") {
    const gold = whole(source.gold_count);
    const recall = metric(source.recall_at_k);
    const precision = metric(source.precision_at_k);
    const mrr = metric(source.mrr_at_k);
    if (gold === null || recall === undefined || precision === undefined || mrr === undefined) throw bad(status);
    result.gold_count = gold;
    result.recall_at_k = recall;
    result.precision_at_k = precision;
    result.mrr_at_k = mrr;
  } else if (value.category === "citation") {
    if (typeof source.citation_valid !== "boolean" || typeof source.expect_citation_valid !== "boolean") throw bad(status);
    if (typeof source.support_label !== "string" || !Array.isArray(source.error_codes)) throw bad(status);
    const codes = source.error_codes.filter((code): code is string => typeof code === "string");
    if (codes.length !== source.error_codes.length) throw bad(status);
    result.citation_valid = source.citation_valid;
    result.expect_citation_valid = source.expect_citation_valid;
    result.support_label = source.support_label;
    result.error_codes = codes;
  } else {
    if (typeof source.structural_valid !== "boolean" || typeof source.expect_structurally_valid !== "boolean") {
      throw bad(status);
    }
    if (typeof source.gold_label !== "string") throw bad(status);
    const excerpts = whole(source.excerpt_count);
    if (excerpts === null) throw bad(status);
    result.structural_valid = source.structural_valid;
    result.expect_structurally_valid = source.expect_structurally_valid;
    result.gold_label = source.gold_label;
    result.error_code = source.error_code === null ? null : text(source.error_code);
    result.excerpt_count = excerpts;
    if ("predicted_label" in source) {
      result.predicted_label = source.predicted_label === null ? null : text(source.predicted_label);
      result.outcome = text(source.outcome);
      result.model = source.model === null ? null : text(source.model);
      result.prompt_version = source.prompt_version === null ? null : text(source.prompt_version);
    }
  }
  return { category: value.category, case_id: value.case_id, result };
}

function signed(value: unknown): string | null | undefined {
  return metric(value, true);
}

function parseDeltas(value: unknown, status: number): EvaluationDeltas {
  if (!isRecord(value)) throw bad(status);
  const retrieval = value.retrieval;
  if (!isRecord(retrieval)) throw bad(status);
  const recall = signed(retrieval.recall_at_k);
  const precision = signed(retrieval.precision_at_k);
  const mrr = signed(retrieval.mrr_at_k);
  const defined = whole(retrieval.defined_cases);
  const undefinedCases = whole(retrieval.undefined_cases);
  if (recall === undefined || precision === undefined || mrr === undefined) throw bad(status);
  if (defined === null || undefinedCases === null) throw bad(status);
  const agreement = signed(value.agreement);
  if (agreement === undefined) throw bad(status);
  const counts = value.counts === null ? null : parseCounts(value.counts, status);
  let matrix: Record<string, Record<string, number>> | null = null;
  if (value.matrix !== null) matrix = parseMatrix(value.matrix, [...LABELS], status);
  return {
    retrieval: {
      recall_at_k: recall,
      precision_at_k: precision,
      mrr_at_k: mrr,
      defined_cases: defined,
      undefined_cases: undefinedCases,
    },
    citations: parseCitations(value.citations, status),
    grounding: parseGrounding(value.grounding, status),
    agreement,
    counts,
    matrix,
  };
}

async function readJson(response: Response): Promise<unknown> {
  if (!response.ok) throw await errorFromResponse(response);
  try {
    return await response.json();
  } catch {
    throw bad(response.status);
  }
}

function query(path: string, params: Record<string, string | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) search.set(key, value);
  }
  const text = search.toString();
  return text ? `${path}?${text}` : path;
}

export async function listEvaluations(
  limit = 20,
  cursor?: string,
  fetchImpl: FetchLike = fetch,
): Promise<{ items: EvaluationListItem[]; next_cursor: string | null }> {
  const response = await fetchImpl(query("/api/v1/admin/evaluations", { limit: String(limit), cursor }), {
    cache: "no-store",
  });
  const body = await readJson(response);
  if (!isRecord(body) || !Array.isArray(body.items)) throw bad(response.status);
  if (body.next_cursor !== null && typeof body.next_cursor !== "string") throw bad(response.status);
  return {
    items: body.items.map((item) => parseItem(item, response.status)),
    next_cursor: body.next_cursor,
  };
}

export async function getEvaluation(id: string, fetchImpl: FetchLike = fetch): Promise<EvaluationDetail> {
  if (!UUID.test(id)) throw new ApiError("not_found", "The evaluation was not found.", 404);
  const response = await fetchImpl(`/api/v1/admin/evaluations/${id}`, { cache: "no-store" });
  return parseDetail(await readJson(response), response.status);
}

export async function listEvaluationCases(
  id: string,
  limit = 20,
  cursor?: string,
  fetchImpl: FetchLike = fetch,
): Promise<{ items: StoredCase[]; next_cursor: string | null }> {
  if (!UUID.test(id)) throw new ApiError("not_found", "The evaluation was not found.", 404);
  const response = await fetchImpl(
    query(`/api/v1/admin/evaluations/${id}/cases`, { limit: String(limit), cursor }),
    { cache: "no-store" },
  );
  const body = await readJson(response);
  if (!isRecord(body) || !Array.isArray(body.items)) throw bad(response.status);
  if (body.next_cursor !== null && typeof body.next_cursor !== "string") throw bad(response.status);
  return {
    items: body.items.map((item) => parseCase(item, response.status)),
    next_cursor: body.next_cursor,
  };
}

export async function compareEvaluations(
  leftId: string,
  rightId: string,
  fetchImpl: FetchLike = fetch,
): Promise<EvaluationComparison> {
  const response = await fetchImpl(
    query("/api/v1/admin/evaluations/compare", { left_id: leftId, right_id: rightId }),
    { cache: "no-store" },
  );
  const body = await readJson(response);
  if (!isRecord(body) || typeof body.comparable !== "boolean" || typeof body.cost_comparable !== "boolean") {
    throw bad(response.status);
  }
  if (!Array.isArray(body.reasons) || !Array.isArray(body.warnings)) throw bad(response.status);
  const reasons = body.reasons.filter((reason): reason is string => typeof reason === "string");
  const warnings = body.warnings.filter((warning): warning is string => typeof warning === "string");
  if (reasons.length !== body.reasons.length || warnings.length !== body.warnings.length) throw bad(response.status);
  const pricing = body.pricing_reason;
  if (pricing !== null && pricing !== "pricing_unavailable" && pricing !== "pricing_version_mismatch") {
    throw bad(response.status);
  }
  const deltas = body.comparable ? parseDeltas(body.deltas, response.status) : null;
  if (!body.comparable && body.deltas !== null) throw bad(response.status);
  return {
    comparable: body.comparable,
    reasons,
    warnings,
    cost_comparable: body.cost_comparable,
    pricing_reason: pricing,
    left: parseDetail(body.left, response.status),
    right: parseDetail(body.right, response.status),
    deltas,
  };
}

const SECONDS = /^\d+\.\d{3}$/;
const ERROR_CODE = /^[a-z0-9_]{1,64}$/;
const STATUSES = [
  "running",
  "completed",
  "partial",
  "insufficient_evidence",
  "failed",
  "interrupted",
] as const;

export type StatusCounts = {
  running: number;
  completed: number;
  partial: number;
  insufficient_evidence: number;
  failed: number;
  interrupted: number;
  recorded_success: number;
  recorded_failure: number;
  recorded_running: number;
};

export type DiagnosticsSummary = {
  window_start: string;
  window_end: string;
  statuses: StatusCounts;
  stale_running: number;
  running_without_start: number;
  errors: { error_code: string; count: number }[];
  duration: {
    known_count: number;
    unknown_count: number;
    total_seconds: string | null;
    max_seconds: string | null;
  };
};

export type DiagnosticRun = {
  run_id: string;
  status: (typeof STATUSES)[number];
  error_code: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration: string | null;
};

export type UsageSummary = {
  window_start: string;
  window_end: string;
  runs_with_usage: number;
  runs_without_usage: number;
  counters: {
    iterations: number;
    search_calls: number;
    llm_calls: number;
    pages_indexed: number;
    pages_failed: number;
  };
  cost_estimate: null;
  coverage: "unavailable";
  pricing_version: null;
};

function counted(value: unknown, status: number): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) throw bad(status);
  return value;
}

function instant(value: unknown, status: number): string {
  if (typeof value !== "string" || Number.isNaN(Date.parse(value))) throw bad(status);
  return value;
}

function seconds(value: unknown, status: number): string | null {
  if (value === null) return null;
  if (typeof value !== "string" || !SECONDS.test(value)) throw bad(status);
  return value;
}

function parseDiagnostics(value: unknown, status: number): DiagnosticsSummary {
  if (!isRecord(value) || !isRecord(value.statuses) || !isRecord(value.duration)) throw bad(status);
  if (!Array.isArray(value.errors)) throw bad(status);
  const statuses = {
    running: counted(value.statuses.running, status),
    completed: counted(value.statuses.completed, status),
    partial: counted(value.statuses.partial, status),
    insufficient_evidence: counted(value.statuses.insufficient_evidence, status),
    failed: counted(value.statuses.failed, status),
    interrupted: counted(value.statuses.interrupted, status),
    recorded_success: counted(value.statuses.recorded_success, status),
    recorded_failure: counted(value.statuses.recorded_failure, status),
    recorded_running: counted(value.statuses.recorded_running, status),
  };
  const errors = value.errors.map((item) => {
    if (!isRecord(item) || typeof item.error_code !== "string" || !ERROR_CODE.test(item.error_code)) {
      throw bad(status);
    }
    return { error_code: item.error_code, count: counted(item.count, status) };
  });
  return {
    window_start: instant(value.window_start, status),
    window_end: instant(value.window_end, status),
    statuses,
    stale_running: counted(value.stale_running, status),
    running_without_start: counted(value.running_without_start, status),
    errors,
    duration: {
      known_count: counted(value.duration.known_count, status),
      unknown_count: counted(value.duration.unknown_count, status),
      total_seconds: seconds(value.duration.total_seconds, status),
      max_seconds: seconds(value.duration.max_seconds, status),
    },
  };
}

function parseRun(value: unknown, status: number): DiagnosticRun {
  if (!isRecord(value) || typeof value.run_id !== "string" || !UUID.test(value.run_id)) throw bad(status);
  if (typeof value.status !== "string" || !STATUSES.includes(value.status as (typeof STATUSES)[number])) {
    throw bad(status);
  }
  if (value.error_code !== null && (typeof value.error_code !== "string" || !ERROR_CODE.test(value.error_code))) {
    throw bad(status);
  }
  return {
    run_id: value.run_id,
    status: value.status as (typeof STATUSES)[number],
    error_code: value.error_code,
    created_at: instant(value.created_at, status),
    started_at: value.started_at === null ? null : instant(value.started_at, status),
    finished_at: value.finished_at === null ? null : instant(value.finished_at, status),
    duration: seconds(value.duration, status),
  };
}

function parseUsage(value: unknown, status: number): UsageSummary {
  if (!isRecord(value) || !isRecord(value.counters)) throw bad(status);
  if (value.cost_estimate !== null || value.pricing_version !== null || value.coverage !== "unavailable") {
    throw bad(status);
  }
  return {
    window_start: instant(value.window_start, status),
    window_end: instant(value.window_end, status),
    runs_with_usage: counted(value.runs_with_usage, status),
    runs_without_usage: counted(value.runs_without_usage, status),
    counters: {
      iterations: counted(value.counters.iterations, status),
      search_calls: counted(value.counters.search_calls, status),
      llm_calls: counted(value.counters.llm_calls, status),
      pages_indexed: counted(value.counters.pages_indexed, status),
      pages_failed: counted(value.counters.pages_failed, status),
    },
    cost_estimate: null,
    coverage: "unavailable",
    pricing_version: null,
  };
}

export async function getOverview(
  fetchImpl: FetchLike = fetch,
): Promise<{ diagnostics: DiagnosticsSummary; latest_evaluation: EvaluationListItem | null }> {
  const response = await fetchImpl("/api/v1/admin/overview", { cache: "no-store" });
  const body = await readJson(response);
  if (!isRecord(body)) throw bad(response.status);
  if (body.latest_evaluation !== null && !isRecord(body.latest_evaluation)) throw bad(response.status);
  return {
    diagnostics: parseDiagnostics(body.diagnostics, response.status),
    latest_evaluation: body.latest_evaluation === null ? null : parseItem(body.latest_evaluation, response.status),
  };
}

export async function getDiagnostics(
  from?: string,
  to?: string,
  fetchImpl: FetchLike = fetch,
): Promise<DiagnosticsSummary> {
  const response = await fetchImpl(query("/api/v1/admin/diagnostics", { from, to }), { cache: "no-store" });
  return parseDiagnostics(await readJson(response), response.status);
}

export async function listDiagnosticRuns(
  from?: string,
  to?: string,
  limit = 20,
  cursor?: string,
  fetchImpl: FetchLike = fetch,
): Promise<{ items: DiagnosticRun[]; next_cursor: string | null }> {
  const response = await fetchImpl(
    query("/api/v1/admin/diagnostics/runs", { from, to, limit: String(limit), cursor }),
    { cache: "no-store" },
  );
  const body = await readJson(response);
  if (!isRecord(body) || !Array.isArray(body.items)) throw bad(response.status);
  if (body.next_cursor !== null && typeof body.next_cursor !== "string") throw bad(response.status);
  return {
    items: body.items.map((item) => parseRun(item, response.status)),
    next_cursor: body.next_cursor,
  };
}

export async function getUsage(
  from?: string,
  to?: string,
  fetchImpl: FetchLike = fetch,
): Promise<UsageSummary> {
  const response = await fetchImpl(query("/api/v1/admin/usage", { from, to }), { cache: "no-store" });
  return parseUsage(await readJson(response), response.status);
}
