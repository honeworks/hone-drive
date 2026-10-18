"""The local dashboard (design §10, decision D-005): a standard-library HTTP server on 127.0.0.1, a JSON
API, server-sent events for progress, and the static page in `dashboard_page/`.

Requests with another `Host` are refused (DNS rebinding); changes need the page's token in a header, JSON
bodies and no foreign `Origin` (CSRF)."""

from __future__ import annotations

import json
import queue
import secrets
import threading
import webbrowser
from email.message import Message
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from hone_drive.errors import HoneDriveError, NotFound
from hone_drive.jobs import Jobs
from hone_drive.runs import list_runs, load_run
from hone_drive.views import compare_view, run_view

PAGE = resources.files("hone_drive") / "dashboard_page"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
TOKEN_HEADER = "X-Hone-Drive-Token"  # noqa: S105 - a header name, not a secret
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}
CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def refusal(method: str, headers: Message[str, str], token: str) -> tuple[int, str] | None:
    """Why a request is refused, or None."""
    host = headers.get("Host") or ""
    if host.rsplit(":", 1)[0].strip("[]") not in LOCAL_HOSTS:
        return HTTPStatus.FORBIDDEN, "the dashboard answers on 127.0.0.1 only"
    if method == "GET":
        return None
    origin = headers.get("Origin")
    if origin is not None and origin != f"http://{host}":
        return HTTPStatus.FORBIDDEN, "the dashboard takes changes from its own page only"
    if not secrets.compare_digest(headers.get(TOKEN_HEADER) or "", token):
        return HTTPStatus.FORBIDDEN, "missing or wrong page token: reload the page"
    if not (headers.get("Content-Type") or "").startswith("application/json"):
        return HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "send the body as application/json"
    return None


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], jobs: Jobs, token: str) -> None:
        self.jobs, self.token, self.closing = jobs, token, threading.Event()
        super().__init__(address, partial(Handler))


class Handler(BaseHTTPRequestHandler):
    server_version = "hone-drive"
    protocol_version = "HTTP/1.1"

    @property
    def app(self) -> Server:
        server: Server = self.server  # pyright: ignore[reportAssignmentType]
        return server

    def log_message(self, format: str, *args: Any) -> None:
        """Quiet."""

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        parts = urlsplit(self.path)
        path = unquote(parts.path)
        query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
        try:
            if (refused := refusal(method, self.headers, self.app.token)) is not None:
                self._json(refused[0], {"error": refused[1]})
            elif method == "GET" and path == "/api/events":
                self._events()
            elif path.startswith("/api/"):
                self._api(method, path.removeprefix("/api"), query)
            elif method == "GET":
                self._static(path)
            else:
                self._json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "method not allowed"})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _api(self, method: str, path: str, query: dict[str, str]) -> None:
        try:
            status, result = self._route(method, path, query)
        except NotFound as exc:
            self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
        except (HoneDriveError, ValueError, KeyError, TypeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        else:
            if isinstance(result, str):
                self._send(
                    status, result.encode(), "text/markdown; charset=utf-8", {"Cache-Control": "no-store"}
                )
            else:
                self._json(status, result)

    def _route(self, method: str, path: str, query: dict[str, str]) -> tuple[int, Any]:
        jobs, home = self.app.jobs, self.app.jobs.home
        if method == "GET" and path == "/runs":
            return HTTPStatus.OK, {"runs": list_runs(), "job": jobs.state}
        if method == "GET" and path.startswith("/runs/"):
            return HTTPStatus.OK, run_view(load_run(path.removeprefix("/runs/")), home)
        if method == "GET" and path == "/compare":
            return HTTPStatus.OK, compare_view(load_run(query["a"]), load_run(query.get("b", "latest")))
        if method == "GET" and path.startswith("/report/"):
            from hone_drive.report import report  # noqa: PLC0415

            run = load_run(path.removeprefix("/report/"))
            return HTTPStatus.OK, report(run, query.get("format", "md"), home=home)
        if method == "POST":
            return self._post(path, self._body())
        raise NotFound(f"no API {method} {path}")

    def _post(self, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        jobs = self.app.jobs
        if path == "/scan":
            jobs.start_scan(bool(body.get("accounting")), bool(body.get("duplicates")))
        elif path == "/bench":
            from hone_drive.cli import parse_size  # noqa: PLC0415

            folders = [str(Path(str(f)).expanduser()) for f in body["folders"]]
            tests = [str(t) for t in body.get("tests", ["seq"])]
            jobs.start_bench(folders, tests, parse_size(str(body.get("size", "4G"))))
        elif path == "/stop":
            jobs.request_stop()
        else:
            raise NotFound(f"no API POST {path}")
        return HTTPStatus.ACCEPTED, jobs.state

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        body: Any = json.loads(raw) if raw else {}
        if not isinstance(body, dict):
            raise ValueError("the body must be a JSON object")
        return body  # pyright: ignore[reportUnknownVariableType]

    def _events(self) -> None:
        listener = self.app.jobs.hub.listen()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while not self.app.closing.is_set():
                try:
                    kind, data = listener.get(timeout=1)
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                else:
                    self.wfile.write(f"event: {kind}\ndata: {json.dumps(data)}\n\n".encode())
                self.wfile.flush()
        finally:
            self.app.jobs.hub.leave(listener)

    def _static(self, path: str) -> None:
        parts = [p for p in path.split("/") if p]
        node = PAGE.joinpath(*parts) if parts and ".." not in parts else PAGE / "index.html"
        if not node.is_file():
            node = PAGE / "index.html"
        body = node.read_bytes()
        if node.name == "index.html":
            body = body.replace(b"{{TOKEN}}", self.app.token.encode())
        kind = TYPES.get(Path(node.name).suffix, "application/octet-stream")
        self._send(HTTPStatus.OK, body, kind, {"Content-Security-Policy": CSP, "Cache-Control": "no-store"})

    def _json(self, status: int, data: Any) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
        self._send(status, body, "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    def _send(self, status: int, body: bytes, kind: str, headers: dict[str, str]) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)


def start(port: int = 0, jobs: Jobs | None = None) -> Server:
    """A running server on 127.0.0.1 (a free port when `port` is 0), serving in a thread."""
    server = Server(("127.0.0.1", port), jobs or Jobs(), secrets.token_urlsafe(24))
    threading.Thread(target=server.serve_forever, name="hone-drive dashboard", daemon=True).start()
    return server


def stop(server: Server) -> None:
    server.closing.set()
    server.shutdown()
    server.server_close()


def serve(port: int = 0, open_browser: bool = True) -> None:
    """Run the dashboard until Ctrl+C."""
    import sys  # noqa: PLC0415

    server = start(port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    sys.stdout.write(f"hone-drive dashboard: {url}  (Ctrl+C to stop)\n")
    sys.stdout.flush()
    if open_browser:
        webbrowser.open(url)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        stop(server)
