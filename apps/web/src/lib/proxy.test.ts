import { createServer, type Server } from "node:http";
import { once } from "node:events";

import { afterEach, describe, expect, it } from "vitest";

import { proxyApi } from "@/lib/proxy";

const RUN = "11111111-1111-4111-8111-111111111111";
const RESEARCH = "22222222-2222-4222-8222-222222222222";

let server: Server | null = null;

afterEach(async () => {
  if (!server) return;
  const current = server;
  server = null;
  current.closeAllConnections();
  current.close();
  await once(current, "close");
});

describe("proxyApi", () => {
  it("returns the first SSE chunk before the upstream finishes", async () => {
    let release: () => void = () => {};
    const finished = new Promise<void>((resolve) => {
      release = resolve;
    });
    let completed = false;
    let seenUrl = "";
    let seenType = "";
    server = createServer((request, response) => {
      seenUrl = request.url ?? "";
      seenType = request.headers["content-type"] ?? "";
      response.writeHead(200, {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache, no-transform",
        "x-accel-buffering": "no",
      });
      response.write(
        `event: research.started\ndata: ${JSON.stringify({
          version: 1,
          run_id: RUN,
          research_id: RESEARCH,
          status: "running",
        })}\n\n`,
      );
      void finished.then(() => {
        completed = true;
        response.write(
          `event: research.completed\ndata: ${JSON.stringify({
            version: 1,
            run_id: RUN,
            research_id: RESEARCH,
            status: "completed",
          })}\n\n`,
        );
        response.end();
      });
    });
    await new Promise<void>((resolve) => {
      server?.listen(0, "127.0.0.1", resolve);
    });
    const address = server.address();
    if (address === null || typeof address === "string") throw new Error("missing port");

    const request = new Request("http://ui.local/api/v1/research/stream?limit=1", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        question: "How does the proxy stream results?",
        research_id: RESEARCH,
      }),
    });
    const response = await proxyApi(request, ["research", "stream"], `http://127.0.0.1:${address.port}`);
    const reader = response.body?.getReader();
    if (!reader) throw new Error("missing body");
    const first = await reader.read();
    const text = new TextDecoder().decode(first.value);
    expect(text).toContain("research.started");
    expect(completed).toBe(false);
    expect(seenUrl).toBe("/api/v1/research/stream?limit=1");
    expect(seenType).toContain("application/json");
    expect(response.headers.get("content-type")).toContain("text/event-stream");
    expect(response.headers.get("cache-control")).toBe("no-cache, no-transform");
    expect(response.headers.get("x-accel-buffering")).toBe("no");
    release();
    await reader.cancel();
  });

  it("delivers each heartbeat comment before the stream ends", async () => {
    let ended = false;
    server = createServer((_request, response) => {
      response.writeHead(200, { "content-type": "text/event-stream; charset=utf-8" });
      let sent = 0;
      const timer = setInterval(() => {
        response.write(": ping\n\n");
        sent += 1;
        if (sent === 3) {
          clearInterval(timer);
          setTimeout(() => {
            ended = true;
            response.end();
          }, 200);
        }
      }, 50);
    });
    const port = await listen();

    const response = await proxyApi(streamRequest(), ["research", "stream"], `http://127.0.0.1:${port}`);
    const reader = response.body?.getReader();
    if (!reader) throw new Error("missing body");
    const decoder = new TextDecoder();
    let text = "";
    while ((text.match(/: ping/g) ?? []).length < 3) {
      const chunk = await reader.read();
      if (chunk.done) break;
      text += decoder.decode(chunk.value, { stream: true });
    }
    expect((text.match(/: ping/g) ?? []).length).toBe(3);
    expect(ended).toBe(false);
    await reader.cancel();
  });

  it("closes the upstream request when the browser disconnects", async () => {
    let upstreamClosed: () => void = () => {};
    const closed = new Promise<void>((resolve) => {
      upstreamClosed = resolve;
    });
    server = createServer((request, response) => {
      response.writeHead(200, { "content-type": "text/event-stream; charset=utf-8" });
      response.write(": ping\n\n");
      request.on("close", () => upstreamClosed());
    });
    const port = await listen();

    const browser = new AbortController();
    const response = await proxyApi(
      streamRequest(browser.signal),
      ["research", "stream"],
      `http://127.0.0.1:${port}`,
    );
    const reader = response.body?.getReader();
    if (!reader) throw new Error("missing body");
    await reader.read();
    browser.abort();
    await reader.cancel().catch(() => {});

    await expect(
      Promise.race([
        closed.then(() => "closed"),
        new Promise((resolve) => setTimeout(() => resolve("timeout"), 2000)),
      ]),
    ).resolves.toBe("closed");
  });

  it("forwards only allowlisted response headers", async () => {
    server = createServer((_request, response) => {
      response.writeHead(200, {
        "content-type": "application/json",
        "set-cookie": "session=leak",
        authorization: "Bearer leak",
        "x-internal-host": "api",
      });
      response.end("{}");
    });
    const port = await listen();

    const response = await proxyApi(
      new Request("http://ui.local/api/v1/research/runs"),
      ["research", "runs"],
      `http://127.0.0.1:${port}`,
    );
    expect(response.headers.get("content-type")).toBe("application/json");
    expect(response.headers.get("set-cookie")).toBeNull();
    expect(response.headers.get("authorization")).toBeNull();
    expect(response.headers.get("x-internal-host")).toBeNull();
  });
});

async function listen(): Promise<number> {
  await new Promise<void>((resolve) => {
    server?.listen(0, "127.0.0.1", resolve);
  });
  const address = server?.address();
  if (!address || typeof address === "string") throw new Error("missing port");
  return address.port;
}

function streamRequest(signal?: AbortSignal): Request {
  return new Request("http://ui.local/api/v1/research/stream", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question: "How does the proxy stream results?", research_id: RESEARCH }),
    signal,
  });
}
