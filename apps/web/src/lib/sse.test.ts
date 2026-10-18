import { describe, expect, it } from "vitest";

import { parseSse, type SseFrame } from "@/lib/sse";

const RUN = "11111111-1111-4111-8111-111111111111";
const RESEARCH = "22222222-2222-4222-8222-222222222222";

function startedFrame(): string {
  return `event: research.started\ndata: ${JSON.stringify({
    version: 1,
    run_id: RUN,
    research_id: RESEARCH,
    status: "running",
  })}\n\n`;
}

function progressFrame(message = "Planning the research"): string {
  return `event: research.progress\ndata: ${JSON.stringify({
    version: 1,
    run_id: RUN,
    stage: "planning",
    iteration: 0,
    message,
    counts: { pages_indexed: 1, search_calls: 0 },
  })}\n\n`;
}

function streamFrom(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
}

async function collect(
  body: ReadableStream<Uint8Array> | null,
  signal?: AbortSignal,
): Promise<SseFrame[]> {
  const frames: SseFrame[] = [];
  for await (const frame of parseSse(body, signal)) frames.push(frame);
  return frames;
}

describe("parseSse", () => {
  it("parses a frame split across UTF-8 bytes", async () => {
    const bytes = new TextEncoder().encode(progressFrame("Planning café"));
    const splitAt = bytes.findIndex((byte, index) => byte === 0xc3 && bytes[index + 1] === 0xa9);
    expect(splitAt).toBeGreaterThan(0);
    const frames = await collect(streamFrom([bytes.slice(0, splitAt + 1), bytes.slice(splitAt + 1)]));
    const progress = frames.find((frame) => frame.type === "event" && frame.name === "research.progress");
    expect(progress).toMatchObject({
      type: "event",
      data: { message: "Planning café" },
    });
  });

  it("accepts CRLF frames and comment heartbeats", async () => {
    const payload = startedFrame().replace(/\n/g, "\r\n") + ": ping\r\n\r\n";
    const frames = await collect(streamFrom([new TextEncoder().encode(payload)]));
    expect(frames.map((frame) => frame.type === "event" ? frame.name : frame.type)).toEqual([
      "research.started",
      "ping",
      "eof",
    ]);
    expect(frames.at(-1)).toEqual({ type: "eof", terminal: false });
  });

  it("parses several frames in one chunk and multi-line data", async () => {
    const failed = [
      "event: research.failed",
      `data: {"version":1,"run_id":"${RUN}",`,
      'data: "code":"invalid_request"}',
      "",
      "",
    ].join("\n");
    const frames = await collect(
      streamFrom([new TextEncoder().encode(startedFrame() + failed)]),
    );
    expect(frames[0]).toMatchObject({ type: "event", name: "research.started" });
    expect(frames[1]).toMatchObject({
      type: "event",
      name: "research.failed",
      data: { code: "invalid_request" },
    });
    expect(frames.at(-1)).toEqual({ type: "eof", terminal: true });
  });

  it("records unknown events and malformed JSON without stopping", async () => {
    const payload = [
      "event: research.other",
      "data: {}",
      "",
      "event: research.progress",
      "data: {not-json}",
      "",
      startedFrame(),
    ].join("\n");
    const frames = await collect(streamFrom([new TextEncoder().encode(payload)]));
    expect(frames[0]).toEqual({ type: "unknown", name: "research.other" });
    expect(frames[1]).toEqual({ type: "malformed", name: "research.progress" });
    expect(frames[2]).toMatchObject({ type: "event", name: "research.started" });
  });

  it("reports EOF when the stream ends without a terminal frame", async () => {
    const frames = await collect(streamFrom([new TextEncoder().encode(progressFrame())]));
    expect(frames.some((frame) => frame.type === "event" && frame.name === "research.completed")).toBe(
      false,
    );
    expect(frames.at(-1)).toEqual({ type: "eof", terminal: false });
  });

  it("stops when the signal aborts", async () => {
    const body = new ReadableStream<Uint8Array>({
      start() {},
    });
    const controller = new AbortController();
    const pending = parseSse(body, controller.signal).next();
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });
});
