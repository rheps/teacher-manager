from __future__ import annotations

import html
import os
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from brity_bridge.local_attachment_links import (
    LOCAL_ATTACHMENT_HOST,
    LOCAL_ATTACHMENT_PORT,
    AttachmentNotFound,
    BlockedAttachmentType,
    InvalidAttachmentName,
    resolve_local_attachment,
)

SUCCESS_MESSAGE = "파일을 열었습니다. 이 탭은 닫아도 됩니다."
MISSING_MESSAGE = "첨부파일을 찾지 못했습니다. Brity 첨부파일 다운로드 폴더를 확인해 주세요."
BLOCKED_MESSAGE = "안전을 위해 이 파일은 자동으로 열지 않습니다."
CANNOT_OPEN_MESSAGE = "이 첨부파일 링크를 열 수 없습니다."
CANCELLED_MESSAGE = "파일 열기를 취소했습니다. 이 탭은 닫아도 됩니다."
CONFIRMATION_TTL_SECONDS = 120.0


def _open_with_windows(path: Path) -> None:
    os.startfile(str(path))


class _AttachmentHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    download_dir_provider: Callable[[], Path]
    opener: Callable[[Path], None]
    expected_host: str
    expected_origin: str
    confirmations: dict[str, tuple[str, float]]
    confirmation_lock: threading.Lock

    def issue_confirmation(self, name: str) -> str:
        now = time.monotonic()
        token = secrets.token_urlsafe(32)
        with self.confirmation_lock:
            self.confirmations = {
                key: value
                for key, value in self.confirmations.items()
                if value[1] > now
            }
            self.confirmations[token] = (name, now + CONFIRMATION_TTL_SECONDS)
        return token

    def consume_confirmation(self, token: str) -> str | None:
        now = time.monotonic()
        with self.confirmation_lock:
            confirmation = self.confirmations.pop(token, None)
        if confirmation is None or confirmation[1] <= now:
            return None
        return confirmation[0]

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_EXCLUSIVEADDRUSE,
                1,
            )
        super().server_bind()


class _AttachmentRequestHandler(BaseHTTPRequestHandler):
    server_version = "TeacherManager"
    sys_version = ""

    def log_message(self, format_string, *args) -> None:
        return

    def send_error(self, code, message=None, explain=None) -> None:
        self._reply(405, CANNOT_OPEN_MESSAGE)

    def _reply(self, status: int, message: str) -> None:
        self._reply_html(status, "<p>" + html.escape(message) + "</p>")

    def _reply_html(self, status: int, content: str) -> None:
        body = (
            "<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
            "<title>Teacher Manager</title></head><body>"
            + content
            + "</body></html>"
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; form-action 'self'; frame-ancestors 'none'; "
            "base-uri 'none'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _reply_with_filename(self, status: int, name: str, message: str) -> None:
        content = (
            "<h1>Teacher Manager</h1><p>"
            + html.escape(name)
            + "</p><p>"
            + html.escape(message)
            + "</p>"
        )
        self._reply_html(status, content)

    def _reply_confirmation(self, name: str, token: str) -> None:
        content = (
            "<h1>첨부파일을 컴퓨터에서 열까요?</h1><p>"
            + html.escape(name)
            + "</p><form method=\"post\" action=\"/confirm\">"
            "<input type=\"hidden\" name=\"token\" value=\""
            + html.escape(token, quote=True)
            + "\"><button type=\"submit\">컴퓨터에서 열기</button>"
            "<button type=\"submit\" formaction=\"/cancel\">취소</button>"
            "</form>"
        )
        self._reply_html(200, content)

    def do_HEAD(self) -> None:
        self._reply(405, CANNOT_OPEN_MESSAGE)

    def do_POST(self) -> None:
        server = self.server
        if not isinstance(server, _AttachmentHTTPServer):
            self._reply(500, CANNOT_OPEN_MESSAGE)
            return
        parsed = urlsplit(self.path)
        if parsed.path not in {"/confirm", "/cancel"} or parsed.query:
            self._reply(405, CANNOT_OPEN_MESSAGE)
            return
        if (
            self.client_address[0] != LOCAL_ATTACHMENT_HOST
            or self.headers.get("Host") != server.expected_host
            or self.headers.get("Origin") not in {server.expected_origin, "null"}
            or self.headers.get("Referer") is not None
            or self.headers.get("Sec-Fetch-Mode") != "navigate"
            or self.headers.get("Sec-Fetch-Dest") != "document"
            or self.headers.get("Sec-Fetch-Site") != "same-origin"
            or self.headers.get("Sec-Fetch-User") != "?1"
        ):
            self._reply(403, CANNOT_OPEN_MESSAGE)
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
        content_length = self.headers.get("Content-Length", "")
        if (
            content_type.casefold() != "application/x-www-form-urlencoded"
            or not content_length.isascii()
            or not content_length.isdigit()
            or not 0 < int(content_length) <= 4096
        ):
            self._reply(400, CANNOT_OPEN_MESSAGE)
            return
        try:
            raw_body = self.rfile.read(int(content_length)).decode("ascii")
            form = parse_qs(raw_body, keep_blank_values=True, strict_parsing=True)
        except (UnicodeDecodeError, ValueError):
            self._reply(400, CANNOT_OPEN_MESSAGE)
            return
        if set(form) != {"token"} or len(form["token"]) != 1 or not form["token"][0]:
            self._reply(400, CANNOT_OPEN_MESSAGE)
            return
        name = server.consume_confirmation(form["token"][0])
        if name is None:
            self._reply(403, CANNOT_OPEN_MESSAGE)
            return
        if parsed.path == "/cancel":
            self._reply(200, CANCELLED_MESSAGE)
            return
        try:
            target = resolve_local_attachment(server.download_dir_provider(), name)
        except InvalidAttachmentName:
            self._reply(400, CANNOT_OPEN_MESSAGE)
            return
        except AttachmentNotFound:
            self._reply_with_filename(404, name, MISSING_MESSAGE)
            return
        except BlockedAttachmentType:
            self._reply_with_filename(403, name, BLOCKED_MESSAGE)
            return
        try:
            server.opener(target)
        except OSError:
            self._reply(500, CANNOT_OPEN_MESSAGE)
            return
        self._reply(200, SUCCESS_MESSAGE)

    def do_GET(self) -> None:
        server = self.server
        if not isinstance(server, _AttachmentHTTPServer):
            self._reply(500, CANNOT_OPEN_MESSAGE)
            return
        parsed = urlsplit(self.path)
        if parsed.path != "/open":
            self._reply(405, CANNOT_OPEN_MESSAGE)
            return
        if (
            self.client_address[0] != LOCAL_ATTACHMENT_HOST
            or self.headers.get("Host") != server.expected_host
        ):
            self._reply(403, CANNOT_OPEN_MESSAGE)
            return
        if (
            self.headers.get("Sec-Fetch-Mode") != "navigate"
            or self.headers.get("Sec-Fetch-Dest") != "document"
            or self.headers.get("Sec-Fetch-User") not in (None, "?1")
        ):
            self._reply(403, CANNOT_OPEN_MESSAGE)
            return
        try:
            query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        except ValueError:
            self._reply(400, CANNOT_OPEN_MESSAGE)
            return
        if set(query) != {"name"} or len(query["name"]) != 1 or not query["name"][0]:
            self._reply(400, CANNOT_OPEN_MESSAGE)
            return
        try:
            target = resolve_local_attachment(server.download_dir_provider(), query["name"][0])
        except InvalidAttachmentName:
            self._reply(400, CANNOT_OPEN_MESSAGE)
            return
        except AttachmentNotFound:
            self._reply_with_filename(404, query["name"][0], MISSING_MESSAGE)
            return
        except BlockedAttachmentType:
            self._reply_with_filename(403, query["name"][0], BLOCKED_MESSAGE)
            return
        token = server.issue_confirmation(target.name)
        self._reply_confirmation(target.name, token)


class LocalAttachmentServer:
    def __init__(
        self,
        download_dir_provider: Callable[[], Path],
        opener: Callable[[Path], None] | None = None,
        host: str = LOCAL_ATTACHMENT_HOST,
        port: int = LOCAL_ATTACHMENT_PORT,
    ):
        if host != LOCAL_ATTACHMENT_HOST:
            raise ValueError("local attachment server must use IPv4 loopback")
        self._download_dir_provider = download_dir_provider
        self._opener = opener or _open_with_windows
        self._host = host
        self._port = port
        self._server: _AttachmentHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def address(self) -> tuple[str, int]:
        with self._lock:
            if self._server is None:
                raise RuntimeError("local attachment server is not running")
            host, port = self._server.server_address[:2]
            return str(host), int(port)

    def start(self) -> None:
        with self._lock:
            if self._server is not None:
                return
            server: _AttachmentHTTPServer | None = None
            thread: threading.Thread | None = None
            try:
                server = _AttachmentHTTPServer(
                    (self._host, self._port), _AttachmentRequestHandler
                )
                host, port = server.server_address[:2]
                server.download_dir_provider = self._download_dir_provider
                server.opener = self._opener
                server.expected_host = f"{host}:{port}"
                server.expected_origin = f"http://{host}:{port}"
                server.confirmations = {}
                server.confirmation_lock = threading.Lock()
                thread = threading.Thread(
                    target=server.serve_forever,
                    kwargs={"poll_interval": 0.05},
                    name="TeacherManagerAttachmentLinks",
                    daemon=True,
                )
                thread.start()
            except Exception:
                if server is not None:
                    if thread is not None and thread.is_alive():
                        server.shutdown()
                        thread.join(timeout=2)
                    server.server_close()
                raise
            self._server = server
            self._thread = thread

    def stop(self) -> None:
        with self._lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=2)
