import { csrfHeaders } from "@/lib/csrf";
import { ApiError, errorFromResponse } from "@/lib/errors";
import type {
  IngestionResponse,
  ResearchRunRequest,
  RunDetailResponse,
  RunListResponse,
} from "@/lib/types";

const RUN_STATUSES = new Set([
  "running",
  "completed",
  "partial",
  "insufficient_evidence",
  "failed",
  "interrupted",
]);

type FetchLike = typeof fetch;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isRunSummary(value: unknown): boolean {
  if (!isRecord(value)) return false;
  return (
    isString(value.run_id) &&
    isString(value.research_id) &&
    isString(value.question) &&
    isString(value.status) &&
    RUN_STATUSES.has(value.status) &&
    (value.error_code === null || isString(value.error_code)) &&
    isString(value.created_at) &&
    (value.finished_at === null || isString(value.finished_at))
  );
}

async function readJson(response: Response): Promise<unknown> {
  if (!response.ok) {
    throw await errorFromResponse(response);
  }
  try {
    return await response.json();
  } catch {
    throw new ApiError("request_failed", "The request could not be completed.", response.status);
  }
}

export async function createResearchScope(fetchImpl: FetchLike = fetch): Promise<string> {
  const response = await fetchImpl("/api/v1/research/scopes", {
    method: "POST",
    headers: csrfHeaders(),
  });
  const body = await readJson(response);
  if (!isRecord(body) || !isString(body.research_id)) {
    throw new ApiError("request_failed", "The request could not be completed.", response.status);
  }
  return body.research_id;
}

export async function listRuns(
  limit = 20,
  cursor?: string,
  fetchImpl: FetchLike = fetch,
): Promise<RunListResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  const response = await fetchImpl(`/api/v1/research/runs?${params.toString()}`);
  const body = await readJson(response);
  if (!isRecord(body) || !Array.isArray(body.items) || !body.items.every(isRunSummary)) {
    throw new ApiError("request_failed", "The request could not be completed.", response.status);
  }
  if (body.next_cursor !== null && !isString(body.next_cursor)) {
    throw new ApiError("request_failed", "The request could not be completed.", response.status);
  }
  return body as unknown as RunListResponse;
}

export async function getRun(
  runId: string,
  fetchImpl: FetchLike = fetch,
): Promise<RunDetailResponse> {
  const response = await fetchImpl(`/api/v1/research/runs/${encodeURIComponent(runId)}`);
  const body = await readJson(response);
  if (!isRecord(body) || !isRunSummary(body.run)) {
    throw new ApiError("request_failed", "The request could not be completed.", response.status);
  }
  return body as unknown as RunDetailResponse;
}

export async function uploadPdf(
  researchId: string,
  file: File,
  signal?: AbortSignal,
  fetchImpl: FetchLike = fetch,
): Promise<IngestionResponse> {
  const form = new FormData();
  form.set("research_id", researchId);
  form.set("file", file);
  const response = await fetchImpl("/api/v1/evidence/pdf", {
    method: "POST",
    headers: csrfHeaders(),
    body: form,
    signal,
  });
  const body = await readJson(response);
  if (
    !isRecord(body) ||
    !isString(body.source_id) ||
    body.research_id !== researchId ||
    body.status !== "indexed" ||
    typeof body.chunk_count !== "number" ||
    typeof body.deduplicated !== "boolean"
  ) {
    throw new ApiError("request_failed", "The request could not be completed.", response.status);
  }
  return body as unknown as IngestionResponse;
}

export function openResearchStream(
  body: ResearchRunRequest,
  signal: AbortSignal,
  fetchImpl: FetchLike = fetch,
): Promise<Response> {
  return fetchImpl("/api/v1/research/stream", {
    method: "POST",
    headers: {
      accept: "text/event-stream",
      "content-type": "application/json",
      ...csrfHeaders(),
    },
    body: JSON.stringify(body),
    signal,
  });
}
