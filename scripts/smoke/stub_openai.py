"""A stand-in OpenAI endpoint for the packaged smoke test. Stdlib only.

Every POST is counted and then held open without a response until the client
disconnects, so a research run stays in its first model call and the stream
emits heartbeats. ``GET /count`` returns the number of POSTs received.
"""

import json
import select
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8080
MAX_HOLD_SECONDS = 300

_lock = threading.Lock()
_posts = 0


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._json({"status": "ok"})
        elif self.path == "/count":
            with _lock:
                self._json({"posts": _posts})
        else:
            self._json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        global _posts
        length = int(self.headers.get("content-length") or 0)
        if length:
            self.rfile.read(length)
        with _lock:
            _posts += 1
        self._hold()

    def _hold(self) -> None:
        deadline = time.monotonic() + MAX_HOLD_SECONDS
        connection = self.connection
        while time.monotonic() < deadline:
            readable, _, _ = select.select([connection], [], [], 0.5)
            if not readable:
                continue
            try:
                if connection.recv(1, socket.MSG_PEEK) == b"":
                    return
            except OSError:
                return
            time.sleep(0.5)
        self._json({"error": "stub hold expired"}, status=504)

    def _json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
