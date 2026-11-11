"""AI attendance input, the PC's half (database design §7.3; SHEET-AI-01 to -04, AI-KEY-02, C7).

The server builds the Gemini request, then checks and writes its answer. This module holds the
teacher's key for one call (D11) and sends the request. The key goes only into the Gemini header.
Nothing here logs, and no sentence, Gemini reply or key is written to disk.

Entry point::

    run_attendance_ai_input(data_call, get_gemini_key, sentence, month=None, *, year=None,
                            resume=None, transport=None, sleep=time.sleep) -> dict

``month`` is accepted and ignored. The month comes from the sentence, and a sentence with no month
means this month (D27). Run inputs one at a time, from a queue: each call is independent, and an
``unknown`` result holds only its own sentence.

``data_call(route, body)`` returns ``{"status": int, "body": dict or None, "unknown": bool}``, as
``ServerSessionClient.data_call(route, body).reply()`` does. ``route`` is the full path
(``/v1/data/ai/prepare``). Coded errors stay as ``{error, message?, ...}`` in ``body``, and
``unknown: true`` means a write got no answer. ``data_call`` owns the replays of a lost answer, same
request ID and same body (database design §6.2). There is one retry owner (OBS-04), so this module
never calls it twice for one request within a run.

``get_gemini_key()`` returns this PC's key or an empty value.

``transport(url, headers, body_bytes, timeout)`` returns ``(status, text)``, as in
``brity_bridge.gemini_analyze``. Tests inject it and ``sleep``.

One run can take minutes: up to three Gemini sends of 60 seconds each and 20 seconds of waits.

The returned dict::

    state             what the result line shows (below)
    message           Korean text for that line, possibly empty
    rows              rows entered or changed, as the server gave them; [] otherwise:
                      recordId, version, date, studentLabel, category, kind, reason, period,
                      reportStatus, attachmentStatus; on a first answer to a correction also
                      changes: [{field, from, to}]. A replayed answer has no changes.
    updated_existing  True when rows are a correction of existing rows
    unknown           True when the write may have happened and could not be confirmed
    code              the server's code, or ""
    fix               the one fix for "blocked" and "signin_required", or "":
                      gemini-key   연결 → Brity 메신저 열기
                      class-settings   학급 설정 열기
                      roster       학생명단 고치기
                      holidays     휴일 추가
                      sign-in      다시 로그인
    record_id         the existing row a duplicate was refused against, or ""
    calendar_year     the year with no holidays, with code HOLIDAYS_MISSING; None otherwise
    resume            only with unknown: keep it in memory, never on disk, and pass it back as
                      resume= on ↻, never by itself
    year              the school year of this input, when known

The states, and what each means for the sentence box:

    applied           green 입력함. Clear the box.
    check_required    amber 확인 필요 with message. Nothing was written. The box stays.
    blocked           amber line with message and fix. Nothing was written. The box stays.
    stale             amber G5 text, then read. Nothing was written. The box stays.
    expired           grey 입력되지 않았어요. Nothing was written. Unlock the box.
    unknown           amber 결과 확인 못 함 with the G4 text. Lock the box until ↻ or an edit.
    unavailable       server not reached before anything was sent to Gemini. The box stays.
    signin_required   the session ended before the server read the request. The box stays.

There is no 다른 입력 처리 중 state: the server has no shared lock that an input waits for.
"""
from __future__ import annotations

import json
import time
import uuid

PREPARE_ROUTE = "/v1/data/ai/prepare"
APPLY_ROUTE = "/v1/data/ai/apply"

# The attendance sender accepts this endpoint and this model only (AI-KEY-02). The messenger
# analysis list in brity_bridge.settings also allows gemini-3.6-flash, so it is not used here.
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_TIMEOUT_SECONDS = 60.0
GEMINI_REQUEST_KEYS = frozenset({"model", "input", "store", "response_format"})

# SHEET-AI-01: these replies are tried again at 5 and 15 seconds, always before any write.
GEMINI_TRANSIENT = frozenset({429, 500, 502, 503, 504})
GEMINI_RETRY_DELAYS = (5.0, 15.0)

NO_KEY_MESSAGE = "AI 출결 입력을 쓸 수 없어요."
SERVER_UNREACHABLE_MESSAGE = "서버에 연결하지 못했어요"
SIGNIN_MESSAGE = "Teacher Manager에 다시 로그인해 주세요."
FAILED_MESSAGE = "AI 출결 입력을 처리하지 못했습니다. 입력 문장을 남겨 두었습니다. 잠시 뒤 [AI로 입력]을 다시 눌러 주세요."
RATE_LIMIT_MESSAGE = "Gemini 무료 사용량(1분에 15번)을 넘었어요. 입력 문장을 남겨 두었습니다. 1분 뒤 [AI로 입력]을 다시 눌러 주세요."
NOT_ENTERED_MESSAGE = "입력되지 않았어요"
# SCREENS G4 and G5.
UNKNOWN_MESSAGE = "저장됐는지 확인하지 못했어요. 서버에 연결되면 창 위쪽 [새로 읽기] 버튼(↻)을 눌러 확인해 주세요."
STALE_MESSAGE = "다른 컴퓨터에서 먼저 바뀌어 저장하지 않고 다시 읽었어요. 바뀐 내용을 확인한 뒤 다시 해 주세요."

_FIX_BY_CODE = {"AI_INPUT_OFF": "class-settings", "ROSTER_EMPTY": "roster", "HOLIDAYS_MISSING": "holidays"}
_NOT_ENTERED_CODES = frozenset({"AI_INPUT_EXPIRED", "AI_INPUT_REJECTED"})
_ANSWER_STATES = frozenset({"applied", "rejected", "needs-new-entry"})


def run_attendance_ai_input(data_call, get_gemini_key, sentence="", month=None, *, year=None, resume=None,
                            transport=None, sleep=time.sleep) -> dict:
    """Runs one press of [AI로 입력], or with ``resume`` replays a lost apply once (§7.3)."""
    run = _Run(data_call, get_gemini_key, transport or _default_transport, sleep)
    if resume is not None:
        return run.resume(resume)
    return run.start(sentence, year)


def _default_transport(url, headers, body, timeout):
    # The same HTTP transport as messenger analysis (§7.3). Not checked on a school PC yet (UPD-01).
    from brity_bridge.gemini_analyze import _default_transport as transport
    return transport(url, headers, body, timeout)


def _outcome(state, message, *, rows=None, updated_existing=False, unknown=False, code="", fix="",
             record_id="", calendar_year=None, resume=None, year=None) -> dict:
    return {"state": state, "message": message, "rows": rows or [], "updated_existing": updated_existing,
            "unknown": unknown, "code": code, "fix": fix, "record_id": record_id, "calendar_year": calendar_year,
            "resume": resume, "year": year}


def _answered(reply) -> bool:
    """An answer the server stands behind. Anything else may hide a committed write (C7)."""
    status, body = reply.get("status"), reply.get("body")
    if reply.get("unknown") or not isinstance(status, int) or not isinstance(body, dict):
        return False
    if 200 <= status < 300:
        return True
    code = body.get("error")
    return status >= 400 and isinstance(code, str) and bool(code) and code != "INTERNAL_ERROR"


def _call(data_call, route, body):
    """The server's answer, or None when there is none to rely on."""
    try:
        reply = data_call(route, body)
    except Exception:  # noqa: BLE001 - a call that ended without an answer is not "nothing happened".
        return None
    return reply if isinstance(reply, dict) and _answered(reply) else None


def _request_allowed(request) -> bool:
    return (isinstance(request, dict) and set(request) == GEMINI_REQUEST_KEYS
            and request["model"] == GEMINI_MODEL and request["store"] is False)


def _reply_for_server(text):
    """Only what the server's parser reads; a reply that is not JSON reaches it as null."""
    try:
        reply = json.loads(text)
    except (TypeError, ValueError):
        return None
    return {"status": reply.get("status"), "steps": reply.get("steps")} if isinstance(reply, dict) else None


def _send_to_gemini(request, key, sends_left, transport, sleep):
    """Returns (http status, reply for the server, sends used). No answer at all is status 0."""
    payload = json.dumps(request, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "x-goog-api-key": key}
    status, used = 0, 0
    for attempt in range(len(GEMINI_RETRY_DELAYS) + 1):
        if used >= sends_left:
            break
        if attempt:
            sleep(GEMINI_RETRY_DELAYS[attempt - 1])
        used += 1
        try:
            status, text = transport(GEMINI_URL, headers, payload, GEMINI_TIMEOUT_SECONDS)
        except Exception:  # noqa: BLE001 - only the listed replies are tried again (SHEET-AI-01).
            return 0, None, used
        if 200 <= status < 300:
            return status, _reply_for_server(text), used
        if status not in GEMINI_TRANSIENT:
            break
    return status, None, used


class _Run:
    def __init__(self, data_call, get_gemini_key, transport, sleep):
        self.data_call = data_call
        self.get_gemini_key = get_gemini_key
        self.transport = transport
        self.sleep = sleep
        self.key = ""
        self.fallback_taken = False

    def _read_key(self) -> str:
        if not self.key:
            try:
                self.key = str(self.get_gemini_key() or "").strip()
            except Exception:  # noqa: BLE001 - shown as no key; the key's reader reports its own failure.
                self.key = ""
        return self.key

    def start(self, sentence, year) -> dict:
        if not self._read_key():
            return _outcome("blocked", NO_KEY_MESSAGE, fix="gemini-key", year=year)
        body = {"requestId": uuid.uuid4().hex, "sentence": sentence}
        if year is not None:
            body["year"] = year
        reply = _call(self.data_call, PREPARE_ROUTE, body)
        if reply is None:
            return _outcome("unavailable", SERVER_UNREACHABLE_MESSAGE, year=year)
        if reply["status"] >= 400:
            return self._refused(reply, year)
        return self._round(reply["body"], sentence)

    def resume(self, resume) -> dict:
        body = resume["body"]
        return self._after_apply(_call(self.data_call, APPLY_ROUTE, body), body, resumed=True)

    def _round(self, answer, sentence) -> dict:
        year = answer.get("year")
        request, input_id = answer.get("request"), answer.get("inputId")
        if not _request_allowed(request) or not isinstance(input_id, str):
            return _outcome("check_required", FAILED_MESSAGE, year=year)
        sends_left = answer.get("sendsLeft")
        status, reply, used = _send_to_gemini(request, self.key, sends_left if isinstance(sends_left, int) else 0,
                                              self.transport, self.sleep)
        body = {"requestId": uuid.uuid4().hex, "inputId": input_id, "sentence": sentence, "httpStatus": status,
                "body": reply, "sendsUsed": used}
        if year is not None:
            body["year"] = year
        return self._after_apply(_call(self.data_call, APPLY_ROUTE, body), body)

    def _after_apply(self, reply, body, resumed=False) -> dict:
        year = body.get("year")
        if reply is None:
            return self._no_result(body)
        answer = reply["body"]
        if reply["status"] >= 400:
            # A session refusal or a server failure comes before the request record is read, so it
            # says nothing about the try that went unanswered. Every other refusal comes after it.
            if resumed and (reply["status"] in (401, 403) or reply["status"] >= 500):
                return self._unknown(body)
            return self._refused(reply, year)
        state = answer.get("status")
        if state not in _ANSWER_STATES:
            return self._no_result(body)
        if state == "applied":
            return self._applied(answer, year)
        if state == "rejected":
            return self._turned_down(answer, year)
        # needs-new-entry: send the server-built request at once, inside the same budget (§7.3).
        if self.fallback_taken:
            return _outcome("check_required", FAILED_MESSAGE, year=year)
        self.fallback_taken = True
        if not self._read_key():
            return _outcome("blocked", NO_KEY_MESSAGE, fix="gemini-key", year=year)
        return self._round({"year": year, **answer}, body["sentence"])

    @staticmethod
    def _applied(answer, year) -> dict:
        rows = answer.get("rows") if isinstance(answer.get("rows"), list) else []
        updated = answer.get("updatedExisting") is True
        message = answer.get("message")
        if not message:
            message = f"출결 기록의 {len(rows)}줄을 고쳤어요." if updated else f"출결 기록에 {len(rows)}줄을 입력했어요."
        return _outcome("applied", str(message), rows=rows, updated_existing=updated, year=year)

    @staticmethod
    def _turned_down(answer, year) -> dict:
        code, record_id = str(answer.get("code") or ""), answer.get("recordId")
        fix = _FIX_BY_CODE.get(code, "")
        return _outcome("blocked" if fix else "check_required", str(answer.get("message") or FAILED_MESSAGE), code=code, fix=fix,
                        record_id=record_id if isinstance(record_id, str) else "", calendar_year=answer.get("calendarYear"), year=year)

    def _no_result(self, body) -> dict:
        """No answer to rely on. A failed Gemini reply writes nothing, so it hides no row (C7)."""
        status = body["httpStatus"]
        if 200 <= status < 300:
            return self._unknown(body)
        message = RATE_LIMIT_MESSAGE if status == 429 else FAILED_MESSAGE
        return _outcome("check_required", message, year=body.get("year"))

    @staticmethod
    def _unknown(body) -> dict:
        return _outcome("unknown", UNKNOWN_MESSAGE, unknown=True, resume={"body": body}, year=body.get("year"))

    @staticmethod
    def _refused(reply, year) -> dict:
        answer, code = reply["body"], str(reply["body"].get("error") or "")
        if code == "STALE":
            return _outcome("stale", STALE_MESSAGE, code=code, year=year)
        if code in _NOT_ENTERED_CODES:
            return _outcome("expired", NOT_ENTERED_MESSAGE, code=code, year=year)
        if reply["status"] == 401:
            return _outcome("signin_required", SIGNIN_MESSAGE, code=code, fix="sign-in", year=year)
        message, fix = str(answer.get("message") or FAILED_MESSAGE), _FIX_BY_CODE.get(code, "")
        return _outcome("blocked" if fix else "check_required", message, code=code, fix=fix,
                        calendar_year=answer.get("calendarYear"), year=year)
