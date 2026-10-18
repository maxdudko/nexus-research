import type {
  ResearchCompleted,
  ResearchFailed,
  ResearchInterrupted,
  ResearchProgress,
  ResearchStarted,
  StageName,
} from "@/lib/types";

const STAGES = new Set<StageName>([
  "planning",
  "discovery",
  "ingestion",
  "retrieval",
  "evaluation",
  "synthesis",
]);

const TERMINAL_EVENTS = new Set(["research.completed", "research.failed", "research.interrupted"]);
const MAX_SSE_BUFFER = 64 * 1024;

export type SseFrame =
  | { type: "ping" }
  | { type: "event"; name: "research.started"; data: ResearchStarted }
  | { type: "event"; name: "research.progress"; data: ResearchProgress }
  | { type: "event"; name: "research.completed"; data: ResearchCompleted }
  | { type: "event"; name: "research.failed"; data: ResearchFailed }
  | { type: "event"; name: "research.interrupted"; data: ResearchInterrupted }
  | { type: "unknown"; name: string }
  | { type: "malformed"; name: string }
  | { type: "eof"; terminal: boolean };

export function isTerminalFrame(frame: SseFrame): boolean {
  return frame.type === "event" && TERMINAL_EVENTS.has(frame.name);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isUuid(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value)
  );
}

function isVersionOne(value: unknown): boolean {
  return value === 1;
}

function narrowStarted(value: unknown): ResearchStarted | null {
  if (!isRecord(value) || !isVersionOne(value.version) || !isUuid(value.run_id)) return null;
  if (!isUuid(value.research_id) || value.status !== "running") return null;
  return {
    version: 1,
    run_id: value.run_id,
    research_id: value.research_id,
    status: "running",
  };
}

function narrowProgress(value: unknown): ResearchProgress | null {
  if (!isRecord(value) || !isVersionOne(value.version) || !isUuid(value.run_id)) return null;
  if (typeof value.stage !== "string" || !STAGES.has(value.stage as StageName)) return null;
  if (typeof value.iteration !== "number" || typeof value.message !== "string") return null;
  if (!isRecord(value.counts)) return null;
  if (typeof value.counts.pages_indexed !== "number") return null;
  if (typeof value.counts.search_calls !== "number") return null;
  return {
    version: 1,
    run_id: value.run_id,
    stage: value.stage as StageName,
    iteration: value.iteration,
    message: value.message,
    counts: {
      pages_indexed: value.counts.pages_indexed,
      search_calls: value.counts.search_calls,
    },
  };
}

function narrowCompleted(value: unknown): ResearchCompleted | null {
  if (!isRecord(value) || !isVersionOne(value.version) || !isUuid(value.run_id)) return null;
  if (!isUuid(value.research_id)) return null;
  if (
    value.status !== "completed" &&
    value.status !== "partial" &&
    value.status !== "insufficient_evidence"
  ) {
    return null;
  }
  return {
    version: 1,
    run_id: value.run_id,
    research_id: value.research_id,
    status: value.status,
  };
}

function narrowFailed(value: unknown): ResearchFailed | null {
  if (!isRecord(value) || !isVersionOne(value.version) || !isUuid(value.run_id)) return null;
  if (typeof value.code !== "string" || !/^[a-z0-9_]{1,64}$/.test(value.code)) return null;
  return { version: 1, run_id: value.run_id, code: value.code };
}

function narrowInterrupted(value: unknown): ResearchInterrupted | null {
  if (!isRecord(value) || !isVersionOne(value.version) || !isUuid(value.run_id)) return null;
  if (value.code !== "interrupted" && value.code !== "deadline_exceeded") return null;
  return { version: 1, run_id: value.run_id, code: value.code };
}

function frameFromJson(name: string, value: unknown): SseFrame {
  if (name === "research.started") {
    const data = narrowStarted(value);
    return data ? { type: "event", name, data } : { type: "malformed", name };
  }
  if (name === "research.progress") {
    const data = narrowProgress(value);
    return data ? { type: "event", name, data } : { type: "malformed", name };
  }
  if (name === "research.completed") {
    const data = narrowCompleted(value);
    return data ? { type: "event", name, data } : { type: "malformed", name };
  }
  if (name === "research.failed") {
    const data = narrowFailed(value);
    return data ? { type: "event", name, data } : { type: "malformed", name };
  }
  if (name === "research.interrupted") {
    const data = narrowInterrupted(value);
    return data ? { type: "event", name, data } : { type: "malformed", name };
  }
  return { type: "unknown", name };
}

function fieldValue(line: string, prefix: string): string {
  const rest = line.slice(prefix.length);
  return rest.startsWith(" ") ? rest.slice(1) : rest;
}

export function parseBlock(block: string): SseFrame | null {
  let eventName = "message";
  const dataLines: string[] = [];
  let sawComment = false;
  let sawField = false;
  for (const line of block.split("\n")) {
    if (line === "" || line.startsWith(":")) {
      if (line.startsWith(":")) sawComment = true;
      continue;
    }
    sawField = true;
    if (line.startsWith("event:")) eventName = fieldValue(line, "event:");
    else if (line.startsWith("data:")) dataLines.push(fieldValue(line, "data:"));
  }
  if (!sawField) return sawComment ? { type: "ping" } : null;
  const known =
    eventName === "research.started" ||
    eventName === "research.progress" ||
    TERMINAL_EVENTS.has(eventName);
  if (!known) return { type: "unknown", name: eventName };
  try {
    return frameFromJson(eventName, JSON.parse(dataLines.join("\n")) as unknown);
  } catch {
    return { type: "malformed", name: eventName };
  }
}

function takeBlocks(buffer: string, flush: boolean): { blocks: string[]; rest: string } {
  let pending = buffer;
  let held = "";
  if (!flush && pending.endsWith("\r")) {
    pending = pending.slice(0, -1);
    held = "\r";
  }
  const normalized = pending.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const parts = normalized.split("\n\n");
  if (!flush) {
    const rest = `${parts.pop() ?? ""}${held}`;
    return { blocks: parts.filter((part) => part.length > 0), rest };
  }
  return { blocks: parts.filter((part) => part.length > 0), rest: "" };
}

function abortError(): DOMException {
  return new DOMException("The operation was aborted.", "AbortError");
}

function readChunk(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  signal?: AbortSignal,
): Promise<ReadableStreamReadResult<Uint8Array>> {
  if (signal?.aborted) return Promise.reject(abortError());
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      void reader.cancel();
      reject(abortError());
    };
    signal?.addEventListener("abort", onAbort, { once: true });
    reader.read().then(
      (result) => {
        signal?.removeEventListener("abort", onAbort);
        resolve(result);
      },
      (error: unknown) => {
        signal?.removeEventListener("abort", onAbort);
        reject(signal?.aborted ? abortError() : error);
      },
    );
  });
}

export async function* parseSse(
  body: ReadableStream<Uint8Array> | null,
  signal?: AbortSignal,
): AsyncGenerator<SseFrame> {
  if (signal?.aborted) throw abortError();
  if (!body) {
    yield { type: "eof", terminal: false };
    return;
  }
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let sawTerminal = false;
  try {
    while (true) {
      const { done, value } = await readChunk(reader, signal);
      if (done) buffer += decoder.decode();
      else buffer += decoder.decode(value, { stream: true });
      if (buffer.length > MAX_SSE_BUFFER) {
        yield { type: "malformed", name: "" };
        yield { type: "eof", terminal: false };
        return;
      }
      const { blocks, rest } = takeBlocks(buffer, done);
      buffer = rest;
      for (const block of blocks) {
        const frame = parseBlock(block);
        if (!frame) continue;
        if (isTerminalFrame(frame)) sawTerminal = true;
        yield frame;
      }
      if (done) {
        yield { type: "eof", terminal: sawTerminal };
        return;
      }
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // The reader was already cancelled.
    }
  }
}
