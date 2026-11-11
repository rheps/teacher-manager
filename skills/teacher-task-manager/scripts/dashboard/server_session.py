"""The desktop's server session: sign-in, one session family per PC, refresh and data calls.

SIGN-IN and MAIN §4.4. The server holds the Google grant, so no Google token reaches this file
(SIGNIN-01). Session tokens, completion codes and answer bodies stay in memory and in the DPAPI
session file. Nothing here writes them to a log.
"""
from __future__ import annotations

import http.client
import json
import platform
import queue
import re
import secrets
import socketserver
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from attendance_session_store import AttendanceSessionStore
from brity_bridge import account_sessions, bundle_paths, component_lock, error_reports, recovery, tls

# Its own file: a 5.x attendance session in `.attendance-session.protected` never mixes with a
# family, and a return to 5.x finds its own file untouched.
SESSION_FILE_NAME = ".server-session.protected"
DEVICE_FILE_NAME = "server-device.json"

# All longer than the slow first name lookup of about 12 s (DELAY-03).
SESSION_TIMEOUT_SECONDS = 25.0
READ_TIMEOUT_SECONDS = 45.0
REPLAYABLE_TIMEOUT_SECONDS = 45.0
EFFECT_TIMEOUT_SECONDS = 120.0

ATTEMPT_SECONDS = 600.0
POLL_SECONDS = 2.0
CODE_ENTRY_AFTER_SECONDS = 20.0

_MAX_ANSWER_BYTES = 40 * 1024 * 1024
_MAX_ERROR_BYTES = 100_000

_ROUTE = re.compile(r"/v1/(?:data/[A-Za-z0-9_\-/]+|me(?:/[A-Za-z0-9_\-/]+)?)")
_GET_ROUTES = frozenset({"/v1/me", "/v1/me/devices", "/v1/data/export"})
_SAFE_READS = frozenset({"/v1/data/read", "/v1/data/check", "/v1/me", "/v1/me/devices", "/v1/data/chat/status", "/v1/data/greetings"})
# No screen offers the full disconnect in this release (SIGN-IN 'Revocation').
_REFUSED_ROUTES = frozenset({"/v1/me/disconnect"})
# Database writes (MAIN §6.2, §12 OBS-04): for the same requestId and body the server returns the
# stored result, so a lost answer may be replayed. Routes that call Google are never replayed (C7),
# and a route not named here is sent once.
_REPLAYABLE_WRITES = frozenset({
    "/v1/data/attendance", "/v1/data/students", "/v1/data/holidays", "/v1/data/settings",
    "/v1/data/years/setup", "/v1/data/years/finish-setup", "/v1/data/ai/prepare", "/v1/data/ai/apply",
    "/v1/data/messages", "/v1/data/student-reports",
})
_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{8,100}")
_TRANSIENT_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_ENDED_CODES = frozenset({"SIGNIN_REQUIRED", "RESUME_REUSED"})
_SESSION_FIELDS = ("baseUrl", "email", "subjectKey", "familyId", "deviceId", "sessionToken",
                   "expiresAt", "resumeToken", "resumeExpiresAt")
_SIGNIN_ANSWER_FIELDS = ("familyId", "sessionToken", "resumeToken", "subjectKey", "email",
                         "expiresAt", "resumeExpiresAt")
_REAPPROVE_ANSWER_FIELDS = ("familyId", "subjectKey", "email")

SESSION_ENDED_TEXT = "Teacher Manager에 다시 로그인해 주세요."
SERVER_UNREACHABLE_TEXT = "서버에 연결하지 못했어요"
_FAILURE_DEFAULT_TEXT = "Google 로그인을 끝내지 못했어요. 실패 원인은 아직 확인하지 못했습니다."
_FAILURE_TEXTS = {
    "SIGNIN_SERVER_UNREACHABLE": SERVER_UNREACHABLE_TEXT,
    "SIGNIN_REQUIRED": SESSION_ENDED_TEXT,
    "RESUME_REUSED": SESSION_ENDED_TEXT,
    "SIGNIN_CANCELLED": "Google 로그인을 취소했어요. 사용하려면 [Google로 로그인]을 눌러 주세요.",
    "SIGNIN_ACCOUNT_MISMATCH": "승인한 Google 계정이 Teacher Manager의 현재 계정과 달라요. 현재 계정으로 다시 승인해 주세요.",
    "SIGNIN_EXPIRED": "Google 로그인을 끝내지 못했어요.",
}
_RETURN_PAGE = ('<!doctype html><html lang="ko"><meta charset="utf-8"><title>Teacher Manager</title>'
                '<p>로그인을 마쳤습니다. Teacher Manager로 돌아가 주세요.</p></html>').encode("utf-8")
_COMPLETION_CODE = re.compile(r"[A-Za-z0-9_-]{20,100}")
_LOGIN_HINT = re.compile(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+")
_CODE_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,79}")


class ServerNoAnswer(Exception):
    """Nothing readable came back. The request may or may not have reached the server."""


class ServerSessionStorageError(account_sessions.AccountSessionStorageError):
    """The session file could not be read or written. It says nothing about the sign-in."""

    def __init__(self):
        super().__init__(account_sessions._STORAGE_MESSAGE)


class ServerSessionStore(AttendanceSessionStore):
    """The same DPAPI file handling under this client's own file name."""

    def __init__(self, config_dir, protect=None, unprotect=None):
        super().__init__(config_dir, protect, unprotect)
        self.path = component_lock.prepare_direct_file_path(Path(config_dir) / SESSION_FILE_NAME)


def open_store(folder) -> ServerSessionStore:
    try:
        return ServerSessionStore(folder)
    except (OSError, ValueError):
        raise ServerSessionStorageError() from None


def checked_base_url(value) -> str:
    """https only, with no credential in the address. A bad or empty address means no server."""
    text = str(value or "").strip().rstrip("/")
    try:
        parsed = urllib.parse.urlsplit(text)
        host = parsed.hostname
    except ValueError:
        return ""
    if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
        return ""
    return text


def server_base_url(environ) -> str:
    """CENTRAL_CHAT_SENDER_URL first; release.json only in an installed build, as error reports do."""
    release = bundle_paths.bundle_root() / "release.json" if bundle_paths.is_frozen() else None
    return checked_base_url(error_reports.sender_base_url(environ, release))


def load_device_id(root) -> str:
    """A random ID per PC, in the settings root: at sign-in start the account is not known yet."""
    value = secrets.token_urlsafe(24)
    try:
        path = component_lock.prepare_direct_file_path(Path(root) / DEVICE_FILE_NAME)
        if path.exists():
            saved = json.loads(path.read_text(encoding="utf-8")).get("deviceId")
            if isinstance(saved, str) and _REQUEST_ID.fullmatch(saved):
                return saved
        component_lock.atomic_write_text_unique(path, json.dumps({"deviceId": value}) + "\n")
    except (OSError, ValueError, AttributeError):
        pass  # A lost ID only makes this PC look new in the device list.
    return value


def device_label() -> str:
    name = "".join(char for char in str(platform.node() or "") if ord(char) >= 32 and ord(char) != 127)
    return name.strip()[:40].strip() or "PC"


def idle_snapshot() -> dict:
    return {"running": False, "url": "", "browser_opened": False, "ok": None, "error_code": "",
            "detail": "", "phase": "", "code_entry": False, "code_error": ""}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward a session token to a different endpoint.


def _json_object(raw):
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        return None
    return value if isinstance(value, dict) else None


def _http_request(method, url, body, headers, timeout):
    """(status, object or None). ServerNoAnswer when no readable answer came back."""
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        opener = urllib.request.build_opener(_NoRedirect, tls.FailoverHTTPSHandler())
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(_MAX_ANSWER_BYTES + 1)
            status = response.status
    except urllib.error.HTTPError as error:
        try:
            return error.code, _json_object(error.read(_MAX_ERROR_BYTES))
        except (OSError, http.client.HTTPException):
            return error.code, None
    except (OSError, http.client.HTTPException):
        raise ServerNoAnswer() from None
    value = _json_object(raw) if len(raw) <= _MAX_ANSWER_BYTES else None
    if value is None:
        raise ServerNoAnswer()
    return status, value


def _code(value) -> str:
    code = value.get("error") if isinstance(value, dict) else ""
    return code if isinstance(code, str) else ""


def _server_decided(value) -> bool:
    """A coded refusal. A masked INTERNAL_ERROR or a gateway's page tells nothing about the write."""
    code = _code(value)
    return bool(code) and code != "INTERNAL_ERROR"


def _still_valid(value, now) -> bool:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() > now
    except (ValueError, TypeError, AttributeError):
        return False


def _has_fields(value, names) -> bool:
    return isinstance(value, dict) and all(isinstance(value.get(name), str) and value[name] for name in names)


def _route_refusal(route, body):
    if not isinstance(route, str) or not _ROUTE.fullmatch(route) or "//" in route or route in _REFUSED_ROUTES:
        return ServerAnswer(400, {"error": "ROUTE_NOT_ALLOWED"}, sent=False)
    if body is not None and not isinstance(body, dict):
        return ServerAnswer(400, {"error": "BODY_NOT_ALLOWED"}, sent=False)
    return None


@dataclass(frozen=True)
class ServerAnswer:
    status: int
    body: dict | None = None
    unknown: bool = False
    session_ended: bool = False  # The server refused the resume token: this PC is signed out.
    sent: bool = True  # False: this answer proves nothing reached the server's handler.

    def reply(self) -> dict:
        return {"status": self.status, "body": self.body, "unknown": self.unknown}


class ServerSessionClient:
    """One session family in one account folder. A new folder gets a new client (LOGIN-28)."""

    def __init__(self, base_url, store, *, request=None, sleeper=time.sleep, clock=time.time, app_version=""):
        self.base_url = checked_base_url(base_url)
        self._store = store
        self._transport = request or _http_request
        self._sleeper = sleeper
        self._clock = clock
        self._app_version = str(app_version or "")
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._session = None
        self._loaded = False
        self._epoch = 0

    @property
    def folder(self):
        return self._store.path.parent

    @property
    def email(self) -> str:
        with self._lock:
            return self._session["email"] if self._session else ""

    @property
    def subject_key(self) -> str:
        with self._lock:
            return self._session["subjectKey"] if self._session else ""

    # ----- the local session ----------------------------------------------------------------

    def _load_saved(self):
        try:
            value = self._store.load()
        except OSError:
            raise ServerSessionStorageError() from None
        except Exception:  # noqa: BLE001 - A damaged file or a moved profile: the next sign-in replaces it.
            return None
        if not _has_fields(value, _SESSION_FIELDS) or value["baseUrl"] != self.base_url:
            return None
        if not _still_valid(value["resumeExpiresAt"], self._clock()):
            try:
                self._store.clear()
            except OSError:
                raise ServerSessionStorageError() from None
            return None
        return value

    def _save(self, session) -> None:
        try:
            self._store.save(session)
        except Exception:  # noqa: BLE001 - Never let a store's error text reach a log or the screen.
            raise ServerSessionStorageError() from None

    def restore(self, expected_email) -> bool:
        """Load this folder's session once. True when a session of that email is in memory."""
        expected = str(expected_email or "").strip().casefold()
        if not expected:
            return False
        with self._lock:
            if self._session is None and not self._loaded:
                self._session = self._load_saved()
                self._loaded = True
            if self._session and self._session["email"].strip().casefold() != expected:
                # It never answers for this folder's account. It stays in the file for end_family.
                self._session, self._loaded = None, False
                return False
            return bool(self._session)

    def adopt_signin(self, answer, device_id) -> None:
        """ACCT-01 step 4: the redeem answer is written only after the folder is active."""
        session = {"baseUrl": self.base_url, "email": answer["email"], "subjectKey": answer["subjectKey"],
                   "familyId": answer["familyId"], "deviceId": str(device_id), "sessionToken": answer["sessionToken"],
                   "expiresAt": answer["expiresAt"], "resumeToken": answer["resumeToken"],
                   "resumeExpiresAt": answer["resumeExpiresAt"]}
        with self._lock:
            self._save(session)
            self._epoch += 1
            self._session = session
            self._loaded = True

    def end_family(self) -> bool:
        """End this PC's family and helper child at the server, and nothing at Google (SIGNIN-02).

        The local session goes first, so being offline cannot keep a PC signed in. True when the
        server confirmed the end or there was nothing to end.
        """
        readable = True
        with self._lock:
            session = self._session
            if session is None and not self._loaded:
                try:
                    session = self._load_saved()
                except ServerSessionStorageError:
                    readable = False
            self._epoch += 1
            self._session = None
            self._loaded = True
            try:
                self._store.clear()
            except OSError:
                raise ServerSessionStorageError() from None
        return self.revoke_unheld(session) if session else readable

    def revoke_unheld(self, session) -> bool:
        """Revoke a family by its own tokens. One try: a logout must not wait on a dead network."""
        if not _has_fields(session, ("familyId", "sessionToken", "resumeToken")):
            return False
        path = "/v1/me/devices/" + urllib.parse.quote(session["familyId"], safe="") + "/revoke"
        status, _value = self._send("POST", path, {}, session["sessionToken"], SESSION_TIMEOUT_SECONDS)
        if status == 401:
            # The 12-hour session ended; the resume token can still name the family.
            status, value = self._refresh_call(session["resumeToken"])
            if status == 401 and _code(value) in _ENDED_CODES:
                return True
            if status != 200 or not _has_fields(value, ("sessionToken",)):
                return False
            status, _value = self._send("POST", path, {}, value["sessionToken"], SESSION_TIMEOUT_SECONDS)
        return status == 200

    # ----- one HTTP exchange ----------------------------------------------------------------

    def _send(self, method, path, body, token, timeout):
        if not self.base_url:
            return 0, None
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = "Bearer " + token
        try:
            return self._transport(method, self.base_url + path, body, headers, timeout)
        except ServerNoAnswer:
            return 0, None

    def open_post(self, path, body, timeout=SESSION_TIMEOUT_SECONDS):
        return self._send("POST", path, body, "", timeout)

    def _refresh_call(self, resume_token):
        body = {"resumeToken": resume_token}
        if self._app_version:
            body["appVersion"] = self._app_version
        return self.open_post("/v1/signin/refresh", body)

    # ----- refresh (SIGNIN-06) --------------------------------------------------------------

    def _ended(self, value=None) -> ServerAnswer:
        body = value if _code(value) in _ENDED_CODES else {"error": "SIGNIN_REQUIRED"}
        return ServerAnswer(401, body, session_ended=True, sent=False)

    def _newest_saved(self, current):
        """Another Api of this process may have rotated the file. An older token would end the family."""
        try:
            saved = self._load_saved()
        except ServerSessionStorageError:
            return current
        if saved is None:
            return None
        return current if saved == current else saved

    def _refresh(self, observed):
        """None when a newer session is in place. Otherwise the final answer for the caller."""
        with self._refresh_lock:
            with self._lock:
                epoch, current = self._epoch, self._session
            if current is None:
                return self._ended()
            newest = self._newest_saved(current)
            with self._lock:
                if epoch != self._epoch:
                    return self._ended()
                if newest is None:
                    self._session = None
                    return self._ended()
                self._session = current = newest
            if current["sessionToken"] != observed["sessionToken"] and _still_valid(current["expiresAt"], self._clock()):
                return None
            status, value = self._refresh_call(current["resumeToken"])
            if status == 401 and _code(value) in _ENDED_CODES:
                # A refused resume token is the logout (LOGIN-32). Everything else is a failed check.
                with self._lock:
                    if epoch == self._epoch:
                        self._epoch += 1
                        self._session = None
                        self._loaded = True
                        try:
                            self._store.clear()
                        except OSError:
                            raise ServerSessionStorageError() from None
                return self._ended(value)
            if (status != 200 or not _has_fields(value, ("sessionToken", "resumeToken", "expiresAt", "resumeExpiresAt"))
                    or value.get("familyId") != current["familyId"] or value.get("subjectKey") != current["subjectKey"]):
                return ServerAnswer(0, None, sent=False)
            updated = {**current, "sessionToken": value["sessionToken"], "resumeToken": value["resumeToken"],
                       "expiresAt": value["expiresAt"], "resumeExpiresAt": value["resumeExpiresAt"]}
            with self._lock:
                if epoch != self._epoch:
                    return self._ended()
                # Saved before the new session is used: until its first use the previous token still
                # answers the same new token, so a failed save loses nothing.
                self._save(updated)
                self._session = updated
            return None

    def _authenticated(self, method, path, body, timeout) -> ServerAnswer:
        """One send, with one refresh when the session is past its time or refused."""
        with self._lock:
            session = self._session
        if session is None:
            return ServerAnswer(401, {"error": "SIGNIN_REQUIRED"}, sent=False)
        if not _still_valid(session["expiresAt"], self._clock()):
            stopped = self._refresh(session)
            if stopped is not None:
                return stopped
            with self._lock:
                session = self._session
            if session is None:
                return self._ended()
        status, value = self._send(method, path, body, session["sessionToken"], timeout)
        if status == 401 and _code(value) == "SIGNIN_REQUIRED":
            # requireFamily refused it before any work, so sending it again repeats nothing.
            stopped = self._refresh(session)
            if stopped is not None:
                return stopped
            with self._lock:
                session = self._session
            if session is None:
                return self._ended()
            status, value = self._send(method, path, body, session["sessionToken"], timeout)
        return ServerAnswer(status, value)

    def me(self) -> ServerAnswer:
        """One try. For google_status the screen owns the three tries (OBS-04, LOGIN-32)."""
        return self._authenticated("GET", "/v1/me", None, SESSION_TIMEOUT_SECONDS)

    def authenticated_post(self, path, body) -> ServerAnswer:
        return self._authenticated("POST", path, body, SESSION_TIMEOUT_SECONDS)

    def helper_client(self):
        """A separately protected child; a racing child refused on first use asks once more."""
        with component_lock.exclusive_file_lock(self.folder / ".server-helper-create.lock", timeout=10):
            with self._lock:
                parent = dict(self._session or {})
            if not parent:
                return None
            child_store = ServerSessionStore(self.folder)
            child_store.path = component_lock.prepare_direct_file_path(self.folder / ".server-helper-session.protected")
            child = ServerSessionClient(self.base_url, child_store, request=self._transport, sleeper=self._sleeper,
                                        clock=self._clock, app_version=self._app_version)
            saved = child._load_saved()
            if saved and saved.get("parentFamilyId") == parent["familyId"]:
                child._session, child._loaded = saved, True
                observed = child.me()
                if observed.status == 200:
                    return child
                if observed.status != 401:
                    return None
            for attempt in range(2):
                answer = self.authenticated_post("/v1/signin/child", {
                    "role": "helper", "parentFamilyId": parent["familyId"], "deviceId": parent["deviceId"],
                })
                if answer.status != 200 or not _has_fields(answer.body, _SIGNIN_ANSWER_FIELDS):
                    return None
                if answer.body["subjectKey"] != parent["subjectKey"] or answer.body["email"].casefold() != parent["email"].casefold():
                    return None
                child.adopt_signin(answer.body, parent["deviceId"])
                with child._lock:
                    child._session["parentFamilyId"] = parent["familyId"]
                    child._save(child._session)
                checked = child.me()
                if checked.status == 200:
                    return child
                if checked.status != 401 or attempt:
                    return None
            return None

    # ----- the contract of Api.data_call ----------------------------------------------------

    def data_call(self, route, body=None) -> ServerAnswer:
        refusal = _route_refusal(route, body)
        if refusal is not None:
            return refusal
        method = "GET" if route in _GET_ROUTES else "POST"
        payload = None if method == "GET" else (body if body is not None else {})
        safe_read = route in _SAFE_READS
        replayable = (route in _REPLAYABLE_WRITES and isinstance(payload, dict)
                      and isinstance(payload.get("requestId"), str) and bool(_REQUEST_ID.fullmatch(payload["requestId"])))
        delays = recovery.NETWORK_DELAYS if safe_read or replayable else (0.0,)
        timeout = READ_TIMEOUT_SECONDS if method == "GET" or safe_read else (
            REPLAYABLE_TIMEOUT_SECONDS if replayable else EFFECT_TIMEOUT_SECONDS)
        if route in {"/v1/data/chat/send", "/v1/data/registrations", "/v1/data/tasks", "/v1/data/reports", "/v1/data/years/report-files"}:
            timeout = 300.0
        uncertain = False
        answer = None
        for delay in delays:
            if delay:
                self._sleeper(delay)
            try:
                answer = self._authenticated(method, route, payload, timeout)
            except ServerSessionStorageError:
                if uncertain and not safe_read:
                    return ServerAnswer(0, None, unknown=True)
                raise
            undecided = answer.status == 0 or (answer.status >= 500 and not _server_decided(answer.body))
            if answer.sent and undecided:
                uncertain = True
            elif answer.sent and answer.status != 401:
                # An answer to the same requestId settles the earlier send. A 401 came before the
                # server looked at the request, so it settles nothing.
                uncertain = False
            if answer.session_ended or not self._worth_another_try(answer, safe_read):
                break
        return ServerAnswer(answer.status, answer.body, unknown=method == "POST" and not safe_read and uncertain,
                            session_ended=answer.session_ended, sent=answer.sent)

    @staticmethod
    def _worth_another_try(answer, safe_read) -> bool:
        if answer.status == 0:
            return True
        if answer.status not in _TRANSIENT_STATUSES:
            return False
        return safe_read or not _server_decided(answer.body)


class _ReturnServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True
    attempt_id = ""
    deliver = None

    def server_bind(self):
        # Not HTTPServer.server_bind: its reverse name lookup can take ten seconds on a school laptop.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def handle_error(self, request, client_address):
        pass  # A browser that closed its connection is nothing to report.


class _ReturnHandler(BaseHTTPRequestHandler):
    timeout = 5
    server_version = "TeacherManager"
    sys_version = ""

    def log_message(self, format, *args):  # noqa: A002 - The query holds the completion code.
        pass

    def _answer(self, status, page=b""):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(page)

    def do_GET(self):  # noqa: N802 - http.server's method name.
        parts = urllib.parse.urlsplit(self.path)
        values = urllib.parse.parse_qs(parts.query)
        attempt, code = values.get("a", [""])[0], values.get("c", [""])[0]
        server, deliver = self.server, self.server.deliver
        expected_host = f"127.0.0.1:{server.server_port}"
        if (parts.path == "/tm-signin" and self.headers.get("Host", "") == expected_host
                and server.attempt_id and secrets.compare_digest(attempt.encode(), server.attempt_id.encode())
                and _COMPLETION_CODE.fullmatch(code) and deliver is not None):
            deliver(code)
            self._answer(200, _RETURN_PAGE)
        else:
            self._answer(404)


class _ReturnListener:
    """The temporary listener of SIGN-IN 'Browser binding': loopback, one attempt, only while waiting."""

    def __init__(self, deliver):
        self._server = _ReturnServer(("127.0.0.1", 0), _ReturnHandler)
        self._server.deliver = deliver
        self.port = self._server.server_port
        thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.2},
                                  name="tm-signin-return", daemon=True)
        thread.start()

    def accept(self, attempt_id):
        self._server.attempt_id = str(attempt_id)

    def close(self):
        self._server.attempt_id = ""
        self._server.deliver = None
        self._server.shutdown()
        self._server.server_close()


class SigninFlow:
    """One browser sign-in (SIGNIN-05). Its background thread polls and redeems; it never touches
    the account state, which the bridge changes on the thread of a screen call (ACCT-01 steps 3-4).
    """

    def __init__(self, client, *, device_id, device_label_text, app_version, opener, on_complete=None,
                 poll_seconds=POLL_SECONDS, code_entry_after=CODE_ENTRY_AFTER_SECONDS,
                 attempt_seconds=ATTEMPT_SECONDS, sleeper=time.sleep, clock=time.monotonic):
        self._client = client
        self.device_id = device_id
        self._device_label = device_label_text
        self._app_version = app_version
        self._opener = opener
        self._on_complete = on_complete
        self._poll_seconds = poll_seconds
        self._code_entry_after = code_entry_after
        self._attempt_seconds = attempt_seconds
        self._sleeper = sleeper
        self._clock = clock
        self._lock = threading.Lock()
        self._codes = queue.Queue()
        self._listener = None
        self.purpose = "signin"
        self.account_token = None
        self._attempt_id = ""
        self._poll_secret = ""
        self._pending_url = ""
        self._auth_url = ""
        self._browser_opened = False
        self._phase = "preparing"
        self._code_entry = False
        self._code_error = ""
        self._started = False
        self._finished = False
        self._ok = None
        self._error_code = ""
        self._detail = ""
        self._answer = None
        self._deadline = 0.0

    # ----- called by the bridge -------------------------------------------------------------

    def start(self, *, purpose="signin", login_hint="") -> bool:
        """POST /v1/signin/start. False leaves everything as it was ('Server unreachable at sign-in')."""
        self.purpose = purpose
        try:
            self._listener = _ReturnListener(self._deliver_code)
        except OSError:
            self._finish(False, "SIGNIN_LISTENER_FAILED")
            return False
        body = {"deviceId": self.device_id, "deviceLabel": self._device_label, "appVersion": self._app_version,
                "purpose": purpose, "returnPort": self._listener.port}
        hint = str(login_hint or "").strip()
        if hint and _LOGIN_HINT.fullmatch(hint):
            body["loginHint"] = hint
        if purpose == "reapprove":
            answer = self._client.authenticated_post("/v1/signin/start", body)
            status, value = answer.status, answer.body
        else:
            status, value = self._client.open_post("/v1/signin/start", body)
        if status == 0 or status >= 500:
            self._finish(False, "SIGNIN_SERVER_UNREACHABLE")
            return False
        if status != 200 or not _has_fields(value, ("attemptId", "pollSecret", "authUrl")):
            self._finish(False, _code(value) if _CODE_NAME.fullmatch(_code(value)) else "SIGNIN_START_REFUSED")
            return False
        try:
            auth_url = urllib.parse.urlsplit(value["authUrl"])
            trusted = auth_url.scheme == "https" and auth_url.hostname == "accounts.google.com"
        except ValueError:
            trusted = False
        if not trusted:
            self._finish(False, "SIGNIN_START_REFUSED")
            return False
        self._attempt_id, self._poll_secret = value["attemptId"], value["pollSecret"]
        self._pending_url = value["authUrl"]
        self._listener.accept(self._attempt_id)
        with self._lock:
            self._started = True
        return True

    def run(self) -> None:
        self._deadline = self._clock() + self._attempt_seconds
        threading.Thread(target=self._work_to_the_end, name="tm-signin", daemon=True).start()

    def abort(self) -> None:
        self._finish(False, "SIGNIN_FAILED")

    def cancel(self) -> bool:
        with self._lock:
            if not self._started:
                return False
        return self._finish(False, "SIGNIN_CANCELLED")

    def submit_code(self, code) -> bool:
        text = str(code or "").strip()
        with self._lock:
            if self._finished or not self._started or not text:
                return False
            self._code_error = ""
        self._codes.put(text)
        return True

    def snapshot(self) -> dict:
        with self._lock:
            running = self._started and self._ok is None
            return {"running": running, "url": self._auth_url if running else "",
                    "browser_opened": self._browser_opened, "ok": self._ok,
                    "error_code": self._error_code if self._ok is False else "",
                    "detail": self._detail if self._ok is False else "",
                    "phase": self._phase if running else "", "code_entry": self._code_entry and running,
                    "code_error": self._code_error if running else ""}

    def answer(self):
        with self._lock:
            return self._answer

    def forget_answer(self) -> None:
        with self._lock:
            self._answer = None

    # ----- the background thread ------------------------------------------------------------

    def _deliver_code(self, code) -> None:
        self._codes.put(code)

    def _finish(self, ok, code="", answer=None) -> bool:
        with self._lock:
            if self._finished:
                return False
            self._finished = True
            listener, callback = self._listener, self._on_complete
            self._listener = self._on_complete = None
            self._poll_secret = ""
        if listener is not None:
            listener.close()
        if callback is not None:
            try:
                callback()
            except Exception:  # noqa: BLE001 - The lock's release must not hide the sign-in's own result.
                pass
        with self._lock:
            # Published last: the screen hears of the end after the shared lock is free.
            self._answer = answer
            self._auth_url = ""
            self._error_code = "" if ok else code
            self._detail = "" if ok else _FAILURE_TEXTS.get(code, _FAILURE_DEFAULT_TEXT)
            self._ok = ok
        return True

    def _open_browser(self) -> bool:
        try:
            opened = self._opener(self._pending_url)
        except Exception:  # noqa: BLE001 - The address stays for direct opening (LOGIN-01).
            return False
        return bool(opened.get("opened")) if isinstance(opened, dict) else opened is True

    def _work_to_the_end(self) -> None:
        try:
            self._work()
        except Exception:  # noqa: BLE001 - The waiting screen must end whatever happened here (OBS-10).
            self._finish(False, "SIGNIN_FAILED")

    def _work(self) -> None:
        opened = self._open_browser()
        with self._lock:
            if self._finished:
                return
            self._auth_url, self._pending_url = self._pending_url, ""
            self._browser_opened = opened
            self._phase = "waiting"
        next_poll = self._clock()
        awaiting_since = None
        while True:
            with self._lock:
                if self._finished:
                    return
            if self._clock() >= self._deadline:
                self._finish(False, "SIGNIN_EXPIRED")
                return
            try:
                code = self._codes.get(timeout=min(0.25, self._poll_seconds))
            except queue.Empty:
                code = None
            if code is not None:
                if self._redeem(code):
                    return
                continue
            if self._clock() >= next_poll:
                next_poll = self._clock() + self._poll_seconds
                state = self._poll_once()
                if state is None:
                    return
                if state == "awaiting-redeem":
                    if awaiting_since is None:
                        awaiting_since = self._clock()
                    with self._lock:
                        self._phase = "awaiting-redeem"
                        if self._clock() - awaiting_since >= self._code_entry_after:
                            self._code_entry = True

    def _poll_once(self):
        """The attempt's state; None when the flow ended. A failed read keeps waiting (CONSENT-17)."""
        status, value = self._client.open_post("/v1/signin/status", {"attemptId": self._attempt_id, "pollSecret": self._poll_secret})
        if status == 401:
            self._finish(False, "SIGNIN_EXPIRED")
            return None
        if status != 200 or not isinstance(value, dict):
            return ""
        state = value.get("state")
        if state == "failed":
            code = _code(value)
            self._finish(False, code if _CODE_NAME.fullmatch(code) else "SIGNIN_FAILED")
            return None
        if state == "expired":
            self._finish(False, "SIGNIN_EXPIRED")
            return None
        return state if isinstance(state, str) else ""

    def _redeem(self, code) -> bool:
        """True when the flow ended. The same code answers the same family, so a lost reply can retry."""
        with self._lock:
            if self._finished:
                return True
            self._phase = "redeeming"
            self._code_error = ""
        fields = _REAPPROVE_ANSWER_FIELDS if self.purpose == "reapprove" else _SIGNIN_ANSWER_FIELDS
        body = {"attemptId": self._attempt_id, "pollSecret": self._poll_secret, "code": code}
        status, value = 0, None
        for delay in recovery.NETWORK_DELAYS:
            if delay:
                self._sleeper(delay)
            status, value = self._client.open_post("/v1/signin/redeem", body)
            if status != 0 and status not in _TRANSIENT_STATUSES:
                break
        if status == 200 and _has_fields(value, fields):
            if not self._finish(True, answer=value):
                # Cancel won the race: nothing here will ever hold this family.
                self._client.revoke_unheld(value)
            return True
        if status == 0 or status in _TRANSIENT_STATUSES:
            self._finish(False, "SIGNIN_SERVER_UNREACHABLE")
            return True
        if status == 400 and _code(value) == "SIGNIN_CODE_INVALID":
            with self._lock:
                self._code_error = "SIGNIN_CODE_INVALID"
                self._code_entry = True
                self._phase = "awaiting-redeem"
            return False
        code_name = _code(value)
        self._finish(False, code_name if _CODE_NAME.fullmatch(code_name) else "SIGNIN_FAILED")
        return True
