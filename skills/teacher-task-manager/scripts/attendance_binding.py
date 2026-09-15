"""Authenticated attendance registry client. Application sessions use an optional Windows-protected store.

No Google credential is copied out of GWS. Reads may retry transient failures;
mutations are sent once, retaining their operation identity after uncertainty.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
import hashlib
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from attendance_context import AttendanceScope, AttendanceScopeError, HistoricalPublicationReceipt, PROTOCOL_VERSION


FAILURE_DOMAINS = frozenset({'central-attendance', 'desktop-gws'})
FAILURE_STAGES = frozenset({'account-grants', 'file-metadata', 'workbook-metadata',
    'workbook-settings', 'workbook-headers', 'workbook-structure', 'inventory-list', 'inventory-headers'})
RECOVERY_ACTIONS = frozenset({'authorize-attendance-account', 'check-file-access',
    'retry-verification', 'update-attendance-service'})
OPERATION_NAMES = frozenset({'capabilities', 'current', 'discover', 'adopt', 'adoption-candidate',
    'replacement-check', 'start', 'operation-read', 'claim', 'authorize', 'dispatch', 'checkpoint',
    'evidence', 'publish', 'register-sheet', 'auth-start', 'auth-status'})


class AttendanceBindingError(AttendanceScopeError):
    def __init__(self, code="ATTENDANCE_AUTHORITY_UNAVAILABLE", *, status=0,
                 failure_domain='', failure_stage='', recovery_action='', operation=''):
        messages = {
            "ATTENDANCE_AUTH_REQUIRED": "출석부 연결을 확인하려면 현재 Google 계정을 한 번 확인해 주세요.",
            "ATTENDANCE_SESSION_STORAGE_UNAVAILABLE": "저장된 출석부 연결을 읽거나 보관하지 못했어요. 기존 명단은 그대로입니다.",
            "ATTENDANCE_SESSION_RESUME_UNSUPPORTED": "출석부 연결 서버의 업데이트가 필요해요. 현재 서버는 자동 연결 유지를 지원하지 않습니다.",
            "ATTENDANCE_ACCOUNT_CHANGED": "Google 계정이 바뀌었어요. 현재 계정으로 출석부 연결을 다시 확인해 주세요.",
            "ATTENDANCE_SCOPE_CHANGED": "출석부의 학년도나 연결이 바뀌었어요. 현재 상태를 다시 읽어 주세요.",
            "ATTENDANCE_RESULT_UNKNOWN": "요청 결과를 아직 확인하지 못했어요. 같은 작업의 결과를 확인할 때까지 추가로 만들지 않습니다.",
            "ATTENDANCE_READ_ONLY": "기존 출석부의 내용을 읽을 수 있지만 수정 권한이 없어요. 해당 파일의 쓰기 권한을 확인해 주세요.",
            "ATTENDANCE_FILE_NOT_EDITABLE": "기존 출석부의 내용을 읽을 수 있지만 수정 권한이 없어요. 해당 파일의 쓰기 권한을 확인해 주세요.",
            "ATTENDANCE_EDITABILITY_UNVERIFIED": "기존 출석부의 수정 가능 여부를 확인하지 못했어요. 파일 권한을 다시 확인해 주세요.",
            "ATTENDANCE_MIGRATION_CONFLICT": "현재 학년도에 출석부로 확인되는 파일이 둘 이상이에요. 임의로 고르거나 새로 만들지 않고 기존 연결의 복구 확인이 필요합니다.",
            "ATTENDANCE_ABSENCE_CHANGED": "이전에 확인한 출석부 없음 상태가 바뀌었어요. 현재 계정의 기존 출석부 연결을 다시 확인해 주세요.",
            "ATTENDANCE_PROTOCOL_REQUIRED": "현재 프로그램과 출석부 연결 서버의 지원 버전이 맞지 않아요. 프로그램 업데이트와 서버 준비 상태를 확인해 주세요.",
            "ATTENDANCE_VERIFIER_SCOPE_MISMATCH": "출석부 확인 서버의 읽기 방식에 문제가 있어요. 서버 업데이트가 필요합니다. 기존 출석부와 준비 기록은 그대로입니다.",
            "ATTENDANCE_INITIAL_PREPARATION_UNSUPPORTED": "출석부 연결 서버가 최초 자동 준비를 지원하는지 확인하지 못했어요. 서버 업데이트 상태를 확인해 주세요.",
            "ATTENDANCE_ACCESS_OR_MISSING": "현재 계정에서 기존 출석부를 찾거나 열 수 없어요. 공유 권한과 휴지통을 확인해 주세요. 이 응답만으로 삭제 여부를 판단하지 않습니다.",
            "ATTENDANCE_ACCESS_DENIED": "Google에서 기존 출석부 접근을 허용하지 않았어요. 해당 계정의 파일 권한을 확인해 주세요.",
            "ATTENDANCE_PERMISSION_REQUIRED": "기존 출석부를 읽는 데 필요한 Google 권한이 부족해요. 현재 계정의 출석부 권한 승인을 다시 확인해 주세요.",
            "ATTENDANCE_POLICY_DENIED": "학교 또는 조직의 Google 정책이 이 요청을 막았어요. 관리자에게 해당 서비스 사용 허용 여부를 확인해 주세요.",
            "ATTENDANCE_API_DISABLED": "이 계정에서 사용하는 Google API가 활성화되지 않았어요. Google 연결 설정에서 해당 API 사용 허용 상태를 확인해 주세요.",
            "ATTENDANCE_FILE_TRASHED": "기존 출석부가 Google Drive 휴지통에 있어요. 현재 파일과 새 출석부를 만들 수 있는 조건을 확인합니다.",
            "ATTENDANCE_REPLACEMENT_UNVERIFIED": "새 출석부로 바꿀 수 있는 조건을 확인하지 못했어요. 기존 자료와 연결 기록은 그대로입니다.",
            "ATTENDANCE_REPLACEMENT_SOURCE_CHANGED": "기존 출석부의 휴지통 상태가 바뀌었어요. 현재 연결을 다시 확인해 주세요.",
            "ATTENDANCE_OLD_EFFECTS_PENDING": "기존 출석부의 처리 결과가 아직 확인되지 않았어요. 그 결과를 확인할 때까지 추가 작업을 멈췄습니다.",
            "ATTENDANCE_EFFECTS_UNVERIFIED": "기존 출석부의 처리 기록을 확인하지 못했어요. 기록을 보존하고 추가 작업을 멈췄습니다.",
            "ATTENDANCE_RETIREMENT_UNVERIFIED": "이전 출석부 연결의 보관 기록을 확인하지 못했어요. 기존 기록을 보존하고 추가 작업을 멈췄습니다.",
            "ATTENDANCE_FILE_RETIRED": "이 출석부는 이전 연결로 보관되었어요. 현재 출석부 연결을 다시 확인해 주세요.",
            "ATTENDANCE_OWNER_MISMATCH": "현재 계정이 기존 출석부의 소유자로 확인되지 않았어요. 원래 출석부를 만든 계정을 확인해 주세요.",
            "ATTENDANCE_FILE_TYPE_MISMATCH": "기록된 파일이 Google 스프레드시트로 확인되지 않았어요. 기존 연결 기록을 보존하고 복구가 필요합니다.",
            "ATTENDANCE_RATE_LIMITED": "Google의 요청 한도에 도달했어요. 잠시 뒤 같은 출석부를 다시 확인해 주세요.",
            "ATTENDANCE_ROLE_MANIFEST_REQUIRED": "기존 출석부의 월별 탭 연결 정보를 확인해야 해요. 확인된 기존 자료만 복구하며 새 출석부를 만들지 않습니다.",
            "ATTENDANCE_ROLE_MANIFEST_MISMATCH": "월별 탭 연결 기록과 실제 출석부가 달라요. 기존 자료를 보존하고 연결을 확인해야 합니다.",
            "ATTENDANCE_ROLE_COLLISION": "월별 탭과 보조 탭의 역할이 겹쳐 안전하게 복구할 수 없어요. 탭 구성을 확인해야 합니다.",
            "ATTENDANCE_STRUCTURE_UNVERIFIED": "기존 출석부의 표 구조를 확인하지 못했어요. 기존 자료를 바꾸지 않고 복구 확인이 필요합니다.",
            "ATTENDANCE_BINDING_CONFLICT": "현재 계정과 학년도에 이미 확인된 다른 연결이 있어요. 추가 생성이나 연결 교체를 멈췄습니다.",
            "ATTENDANCE_CREATION_NOT_ALLOWED": "현재 계정과 학년도에 새 출석부를 만들 수 있는 조건이 확인되지 않았어요. 기존 연결을 다시 확인해 주세요.",
            "ATTENDANCE_OPERATION_IN_PROGRESS": "같은 출석부 준비가 다른 창이나 컴퓨터에서 진행 중이에요. 그 작업이 끝난 뒤 다시 확인해 주세요.",
            "ATTENDANCE_CREATE_RESULT_UNKNOWN": "앞선 출석부 만들기 결과를 아직 확인하지 못했어요. 같은 작업의 결과를 확인하기 전에는 추가로 만들지 않습니다.",
            "ATTENDANCE_RECOVERY_REQUIRED": "기존 연결과 작업 기록의 복구 확인이 필요해요. 자료와 불확실한 작업 기록을 보존했습니다.",
            "ATTENDANCE_PROGRESS_INVALID": "출석부 준비 기록을 안전하게 읽거나 저장할 수 없어요. 기록을 보존하고 추가 작업을 멈췄습니다.",
            "ATTENDANCE_AUTH_CANCELLED": "Google 권한 승인이 취소되었어요. 필요할 때 출석부 계정 확인을 다시 시작해 주세요.",
            "ATTENDANCE_AUTH_EXPIRED": "Google 권한 승인 시간이 지났어요. 출석부 계정 확인을 다시 시작해 주세요.",
            "ATTENDANCE_AUTH_FAILED": "Google 권한 승인을 마치지 못했어요. 출석부 계정 확인을 다시 시작해 주세요.",
            "ATTENDANCE_REFRESH_REQUIRED": "출석부 연결을 유지하는 데 필요한 Google 승인이 없어요. 현재 계정의 출석부 권한을 다시 승인해 주세요.",
            "ATTENDANCE_ACCOUNT_MISMATCH": "승인한 Google 계정이 Teacher Manager의 현재 계정과 달라요. 현재 계정으로 다시 승인해 주세요.",
            "ATTENDANCE_IDENTITY_INVALID": "Google 계정의 고유 식별 정보를 확인하지 못했어요. 현재 계정의 권한 승인을 다시 확인해 주세요.",
            "ATTENDANCE_YEAR_CHANGED": "한국 날짜 기준 학년도가 바뀌었어요. 현재 상태를 다시 확인해 주세요. 앞선 작업을 새 출석부로 돌리지 않습니다.",
            "ATTENDANCE_YEAR_MISMATCH": "기존 출석부의 고정 학년도와 현재 연결 학년도가 달라요. 기존 학년도 자료를 보존했습니다.",
            "ATTENDANCE_YEAR_UNVERIFIED": "출석부의 고정 학년도를 확인하지 못했어요. 프로필 값으로 학년도를 바꾸지 않습니다.",
            "ATTENDANCE_DATE_OUTSIDE_YEAR": "입력한 출결 날짜가 이 출석부의 학년도에 속하지 않아요. 날짜와 출석부를 확인해 주세요.",
            "ATTENDANCE_SCOPE_STALE": "확인하는 동안 출석부의 계정·학년도·연결이 바뀌었어요. 현재 상태를 다시 읽어 주세요.",
            "ATTENDANCE_LEASE_EXPIRED": "앞선 준비 작업의 진행 권한이 만료되었어요. 같은 작업의 결과를 확인한 뒤 다시 시작해 주세요.",
        }
        super().__init__(code, messages.get(code, "출석부 연결 상태를 확인하지 못했어요. 기존 자료와 작업 기록은 그대로입니다."))
        self.status = status
        self.failure_domain = failure_domain if isinstance(failure_domain, str) and failure_domain in FAILURE_DOMAINS else ''
        self.failure_stage = failure_stage if isinstance(failure_stage, str) and failure_stage in FAILURE_STAGES else ''
        self.recovery_action = recovery_action if isinstance(recovery_action, str) and recovery_action in RECOVERY_ACTIONS else ''
        self.operation = operation if isinstance(operation, str) and operation in OPERATION_NAMES else ''
        # Remote ATTENDANCE_* strings may still contain arbitrary data. Only
        # codes defined by this client may enter diagnostic logs or reports.
        generic_codes = {
            "ATTENDANCE_AUTHORITY_UNAVAILABLE", "ATTENDANCE_VERIFY_UNAVAILABLE",
            "ATTENDANCE_INVALID_RESPONSE", "ATTENDANCE_ENDPOINT_INVALID",
            "ATTENDANCE_AUTH_URL_INVALID", "ATTENDANCE_ADOPTION_NOT_ALLOWED",
            "ATTENDANCE_OPERATION_INVALID", "ATTENDANCE_OPERATION_STALE",
        }
        self.diagnostic_code = (
            code if code in messages or code in generic_codes else "ATTENDANCE_UNKNOWN_ERROR"
        )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # never forward a bearer credential to a different endpoint


def _http_request(method, url, body, headers):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=25) as response:
            raw = response.read(2_000_001)
            status = response.status
    except urllib.error.HTTPError as error:
        code = "ATTENDANCE_AUTH_REQUIRED" if error.code == 401 else "ATTENDANCE_AUTHORITY_UNAVAILABLE"
        controlled = {}
        try:
            payload = json.loads(error.read(100_000))
            remote_error = payload.get("error")
            remote_code = payload.get("code") or (remote_error if isinstance(remote_error, str) else (remote_error or {}).get("code"))
            if isinstance(remote_code, str) and remote_code.startswith("ATTENDANCE_") and remote_code.replace("_", "").isalnum():
                code = remote_code
            controlled = {key: payload.get(key, '') for key in ('failure_domain', 'failure_stage', 'recovery_action')}
        except (AttributeError, ValueError, OSError):
            pass
        raise AttendanceBindingError(code, status=error.code, **controlled) from None
    except (OSError, urllib.error.URLError):
        raise AttendanceBindingError() from None
    if len(raw) > 2_000_000:
        raise AttendanceBindingError("ATTENDANCE_INVALID_RESPONSE")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise AttendanceBindingError("ATTENDANCE_INVALID_RESPONSE") from None
    if status != 200 or not isinstance(value, dict) or value.get("ok") is False:
        raise AttendanceBindingError("ATTENDANCE_INVALID_RESPONSE")
    return value


class AttendanceBindingClient:
    def __init__(self, base_url: str, *, request: Callable = _http_request, sleeper=time.sleep, session_store=None, clock=time.time):
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise AttendanceBindingError("ATTENDANCE_ENDPOINT_INVALID")
        self.base_url = base_url.rstrip("/")
        self._request_transport = request
        self._sleeper = sleeper
        self._lock = threading.RLock()
        self._epoch = 0
        self._token = ""
        self._subject = ""
        self._email = ""
        self._attempt = None
        self._store, self._clock = session_store, clock
        self._resume = ""
        self._expires = self._resume_expires = ""
        self._refresh_lock = threading.Lock()
        self._restored = False
        self._resume_supported = False
        self._reference_cache = {}

    @property
    def email(self):
        return self._email

    @staticmethod
    def _valid_until(value, now):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() > now
        except (ValueError, TypeError, AttributeError):
            return False

    def _persist(self):
        if self._store and self._resume:
            try:
                self._store.save(dict(baseUrl=self.base_url, email=self._email, subjectKey=self._subject,
                    sessionToken=self._token, expiresAt=self._expires, resumeToken=self._resume,
                    resumeExpiresAt=self._resume_expires))
            except Exception:
                raise AttendanceBindingError('ATTENDANCE_SESSION_STORAGE_UNAVAILABLE') from None

    def restore(self, expected_email):
        expected = str(expected_email or '').strip().casefold()
        if not expected:
            return
        with self._lock:
            if self._email and self._email != expected:
                raise AttendanceBindingError('ATTENDANCE_ACCOUNT_CHANGED')
            if self._restored or self._token or self._attempt or not self._store:
                return
            try:
                value = self._store.load()
            except Exception:
                raise AttendanceBindingError('ATTENDANCE_SESSION_STORAGE_UNAVAILABLE') from None
            if value is None:
                self._restored = True
                return
            keys = ('baseUrl', 'email', 'subjectKey', 'sessionToken', 'expiresAt', 'resumeToken', 'resumeExpiresAt')
            if not isinstance(value, dict) or any(not isinstance(value.get(k), str) or not value[k] for k in keys):
                raise AttendanceBindingError('ATTENDANCE_SESSION_STORAGE_UNAVAILABLE')
            if value['baseUrl'] != self.base_url or value['email'].casefold() != expected:
                raise AttendanceBindingError('ATTENDANCE_ACCOUNT_CHANGED')
            try:
                for key in ('expiresAt', 'resumeExpiresAt'):
                    if datetime.fromisoformat(value[key].replace('Z', '+00:00')).tzinfo is None:
                        raise ValueError()
            except (ValueError, TypeError):
                raise AttendanceBindingError('ATTENDANCE_SESSION_STORAGE_UNAVAILABLE') from None
            if not self._valid_until(value['resumeExpiresAt'], self._clock()):
                self._store.clear()
                self._restored = True
                return
            self._email, self._subject = expected, value['subjectKey']
            self._token, self._expires = value['sessionToken'], value['expiresAt']
            self._resume, self._resume_expires = value['resumeToken'], value['resumeExpiresAt']
            self._restored = self._resume_supported = True

    def clear(self, *, revoke=False):
        with self._lock:
            resume = self._resume
            revoke_readable = True
            if revoke and not resume and self._store:
                try:
                    saved = self._store.load()
                    if saved and saved.get('baseUrl') == self.base_url:
                        resume = saved.get('resumeToken', '')
                except Exception:
                    revoke_readable = False
            self._epoch += 1
            self._token = self._subject = self._email = self._resume = ''
            self._expires = self._resume_expires = ''
            self._attempt = None
            self._restored = True
            if self._store:
                try:
                    self._store.clear()
                except Exception:
                    raise AttendanceBindingError('ATTENDANCE_SESSION_STORAGE_UNAVAILABLE') from None
        if revoke and resume:
            try:
                result = self._request_transport('POST', self.base_url + '/v1/attendance/auth/revoke',
                    {'resumeToken': resume}, {'Content-Type': 'application/json', 'Accept': 'application/json'})
                return isinstance(result, dict) and result.get('revoked') is True
            except Exception:
                return False
        return revoke_readable

    def _refresh(self, observed_token):
        with self._refresh_lock:
            with self._lock:
                if self._token != observed_token:
                    if self._token:
                        return
                    raise AttendanceBindingError('ATTENDANCE_AUTH_REQUIRED')
                epoch, resume, email, subject = self._epoch, self._resume, self._email, self._subject
            if not resume:
                raise AttendanceBindingError('ATTENDANCE_AUTH_REQUIRED')
            try:
                value = self._request('POST', '/v1/attendance/auth/refresh', {'resumeToken': resume},
                                      authenticated=False, safe_read=True)
            except AttendanceBindingError as error:
                error.attendance_auth_origin = 'refresh-rejected'
                if error.status == 404:
                    raise AttendanceBindingError('ATTENDANCE_SESSION_RESUME_UNSUPPORTED') from None
                raise
            with self._lock:
                if epoch != self._epoch:
                    raise AttendanceBindingError('ATTENDANCE_ACCOUNT_CHANGED')
                if value.get('email', '').casefold() != email or value.get('subjectKey') != subject:
                    raise AttendanceBindingError('ATTENDANCE_ACCOUNT_CHANGED')
                if not isinstance(value.get('sessionToken'), str) or not value['sessionToken'] or not self._valid_until(value.get('expiresAt'), self._clock()) or not self._valid_until(value.get('resumeExpiresAt'), self._clock()):
                    raise AttendanceBindingError('ATTENDANCE_INVALID_RESPONSE')
                self._token, self._expires = value['sessionToken'], value['expiresAt']
                self._resume_expires = value['resumeExpiresAt']
                self._persist()

    def _request(self, method, path, body=None, *, authenticated=True, safe_read=False, _renewed=False):
        with self._lock:
            epoch, token = self._epoch, self._token
        if authenticated and self._resume and not self._valid_until(self._expires, self._clock()):
            self._refresh(token)
            with self._lock:
                if epoch != self._epoch:
                    raise AttendanceBindingError('ATTENDANCE_ACCOUNT_CHANGED')
                token = self._token
        if authenticated and not token:
            error = AttendanceBindingError("ATTENDANCE_AUTH_REQUIRED")
            error.attendance_auth_origin = 'local-session-missing'
            raise error
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if authenticated:
            headers["Authorization"] = "Bearer " + token
        attempts = 3 if safe_read else 1
        for attempt in range(attempts):
            try:
                value = self._request_transport(method, self.base_url + path, body, headers)
                if not isinstance(value, dict):
                    raise AttendanceBindingError("ATTENDANCE_INVALID_RESPONSE")
                if safe_read and value.get("verificationState") in ("ATTENDANCE_VERIFY_UNAVAILABLE", "ATTENDANCE_RATE_LIMITED"):
                    raise AttendanceBindingError(value["verificationState"], status=503,
                        failure_domain=value.get('failure_domain'), failure_stage=value.get('failure_stage'),
                        recovery_action=value.get('recovery_action'))
                with self._lock:
                    if epoch != self._epoch:
                        raise AttendanceBindingError("ATTENDANCE_ACCOUNT_CHANGED")
                return value
            except AttendanceBindingError as error:
                if authenticated and not _renewed and error.code == 'ATTENDANCE_AUTH_REQUIRED' and self._resume:
                    with self._lock:
                        if epoch != self._epoch:
                            raise AttendanceBindingError('ATTENDANCE_ACCOUNT_CHANGED')
                    self._refresh(token)
                    with self._lock:
                        if epoch != self._epoch:
                            raise AttendanceBindingError('ATTENDANCE_ACCOUNT_CHANGED')
                    if method == 'GET' or path == '/v1/attendance/workbook/authorize':
                        return self._request(method, path, body, authenticated=authenticated, safe_read=safe_read, _renewed=True)
                    raise AttendanceBindingError('ATTENDANCE_AUTH_REQUIRED') from None
                if not error.operation:
                    name = path.rsplit('/', 1)[-1]
                    if '/operations/' in path and method == 'GET': name = 'operation-read'
                    if path == '/v1/attendance/auth/start': name = 'auth-start'
                    if path == '/v1/attendance/auth/status': name = 'auth-status'
                    error.operation = name if name in OPERATION_NAMES else ''
                if not error.failure_domain:
                    error.failure_domain = 'central-attendance'
                if (
                    ((method == "GET" and path == "/v1/attendance/capabilities")
                     or path in ("/v1/attendance/current/record", "/v1/attendance/references")
                     or path.startswith("/v1/attendance/references/"))
                    and error.status == 404 and error.code == "ATTENDANCE_AUTHORITY_UNAVAILABLE"
                ):
                    # This unauthenticated handshake route is required by the
                    # protocol. Its absence says nothing about a workbook.
                    raise AttendanceBindingError("ATTENDANCE_PROTOCOL_REQUIRED", status=404) from None
                if not safe_read or error.code not in ("ATTENDANCE_AUTHORITY_UNAVAILABLE", "ATTENDANCE_VERIFY_UNAVAILABLE", "ATTENDANCE_RATE_LIMITED") or error.status not in (0, 429, 500, 502, 503, 504) or attempt == attempts - 1:
                    raise
                self._sleeper(0.25 * (attempt + 1))

    def begin_authorization(self, expected_email: str, expected_subject_key: str = ""):
        self.clear(revoke=True)
        with self._lock:
            epoch = self._epoch
        expected_email = str(expected_email).strip().casefold()
        if not expected_email or "@" not in expected_email:
            raise AttendanceBindingError("ATTENDANCE_ACCOUNT_CHANGED")
        capabilities = self._request("GET", "/v1/attendance/capabilities", authenticated=False, safe_read=True)
        if capabilities.get("protocolVersion") != PROTOCOL_VERSION or capabilities.get("accountBootstrap") is not True:
            raise AttendanceBindingError("ATTENDANCE_PROTOCOL_REQUIRED")
        self._resume_supported = capabilities.get('attendanceSessionResume') is True
        if self._store and not self._resume_supported:
            raise AttendanceBindingError('ATTENDANCE_SESSION_RESUME_UNSUPPORTED')
        body = {"expectedEmail": expected_email}
        if isinstance(expected_subject_key, str) and expected_subject_key:
            body["expectedSubjectKey"] = expected_subject_key  # nonsecret hint; verified independently by OAuth
        value = self._request("POST", "/v1/attendance/auth/start", body, authenticated=False)
        if not all(isinstance(value.get(key), str) and value[key] for key in ("attemptId", "pollSecret", "authUrl")):
            raise AttendanceBindingError("ATTENDANCE_INVALID_RESPONSE")
        auth_url = urllib.parse.urlparse(value["authUrl"])
        if auth_url.scheme != "https" or auth_url.hostname != "accounts.google.com":
            raise AttendanceBindingError("ATTENDANCE_AUTH_URL_INVALID")
        with self._lock:
            if epoch != self._epoch:
                raise AttendanceBindingError("ATTENDANCE_ACCOUNT_CHANGED")
            self._attempt = {"attemptId": value["attemptId"], "pollSecret": value["pollSecret"]}
            self._email = expected_email
        return {"state": "pending", "auth_url": value["authUrl"], "expires_at": value.get("expiresAt")}

    def authorization_status(self, expected_email: str):
        with self._lock:
            epoch = self._epoch
            attempt = dict(self._attempt or {})
            if self._email != str(expected_email).strip().casefold():
                self.clear()
                raise AttendanceBindingError("ATTENDANCE_ACCOUNT_CHANGED")
        if not attempt:
            return {"state": "complete" if self._token else "required"}
        value = self._request("POST", "/v1/attendance/auth/status", attempt, authenticated=False, safe_read=True)
        if value.get("state") == "complete":
            session = value.get("session", value)
            if not all(isinstance(session.get(key), str) and session[key] for key in ("sessionToken", "subjectKey", "email")):
                raise AttendanceBindingError("ATTENDANCE_INVALID_RESPONSE")
            if session["email"].strip().casefold() != self._email:
                self.clear()
                raise AttendanceBindingError("ATTENDANCE_ACCOUNT_CHANGED")
            with self._lock:
                if epoch != self._epoch:
                    raise AttendanceBindingError("ATTENDANCE_ACCOUNT_CHANGED")
                if self._resume_supported and (not isinstance(session.get('resumeToken'), str) or not session['resumeToken'] or not self._valid_until(session.get('expiresAt'), self._clock()) or not self._valid_until(session.get('resumeExpiresAt'), self._clock())):
                    raise AttendanceBindingError('ATTENDANCE_INVALID_RESPONSE')
                self._token, self._subject = session["sessionToken"], session["subjectKey"]
                self._attempt = None
                if self._resume_supported:
                    self._resume, self._expires = session['resumeToken'], session['expiresAt']
                    self._resume_expires = session['resumeExpiresAt']
                    self._persist()
            return {"state": "complete"}
        if value.get("state") not in ("pending", "failed"):
            raise AttendanceBindingError("ATTENDANCE_INVALID_RESPONSE")
        if value["state"] == "failed":
            code = value.get("error") if isinstance(value.get("error"), str) else "ATTENDANCE_AUTH_FAILED"
            if not code.startswith("ATTENDANCE_") or not code.replace("_", "").isalnum():
                code = "ATTENDANCE_AUTH_FAILED"
            error = AttendanceBindingError(code, failure_domain=value.get('failure_domain'),
                failure_stage=value.get('failure_stage'), recovery_action=value.get('recovery_action'))
            return {"state": "failed", "code": error.diagnostic_code, "detail": str(error),
                    "failure_code": error.diagnostic_code, "failure_domain": error.failure_domain,
                    "failure_stage": error.failure_stage, "recovery_action": error.recovery_action}
        return {"state": value["state"]}

    def current(self) -> AttendanceScope:
        return AttendanceScope.parse(self._request("GET", "/v1/attendance/current", safe_read=True), expected_subject=self._subject)

    def workbook_choices(self):
        value = self._request('GET', '/v1/attendance/current/choices', safe_read=True)
        context = value.get('context') if isinstance(value, dict) else None
        rows = value.get('candidates') if isinstance(value, dict) else None
        if (not isinstance(context, dict) or value.get('state') != 'choices' or context.get('subjectKey') != self._subject
                or str(value.get('email') or '').casefold() != self.email.casefold()
                or not isinstance(rows, list)
                or type(context.get('expectedGeneration')) is not int
                or type(context.get('expectedSchoolYear')) is not int
                or context['expectedGeneration'] < 0 or not 2000 <= context['expectedSchoolYear'] <= 2099
                or any(context.get(key) is not None and not isinstance(context[key], str)
                       for key in ('previousSpreadsheetId', 'previousOperationId'))):
            raise AttendanceBindingError('ATTENDANCE_INVALID_RESPONSE')
        seen = set()
        for row in rows:
            sid = row.get('spreadsheet_id') if isinstance(row, dict) else None
            if (not isinstance(sid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{3,200}', sid)
                    or sid in seen or not isinstance(row.get('name'), str)
                    or row.get('spreadsheet_url') != f'https://docs.google.com/spreadsheets/d/{sid}/edit'
                    or type(row.get('can_edit')) is not bool or not isinstance(row.get('modified_time'), str)):
                raise AttendanceBindingError('ATTENDANCE_INVALID_RESPONSE')
            seen.add(sid)
        return value

    def select_workbook(self, spreadsheet_id, context, idempotency_key):
        if not isinstance(context, dict) or context.get('subjectKey') != self._subject:
            raise AttendanceBindingError('ATTENDANCE_SCOPE_CHANGED')
        payload = {key: context.get(key) for key in ('subjectKey', 'expectedSchoolYear',
            'expectedGeneration', 'previousSpreadsheetId', 'previousOperationId')}
        payload.update(spreadsheetId=spreadsheet_id, idempotencyKey=idempotency_key, explicitConfirmation=True)
        scope = AttendanceScope.parse(self._request('POST', '/v1/attendance/current/select', payload), expected_subject=self._subject)
        if (scope.payload['spreadsheetId'] != spreadsheet_id or scope.payload['bindingState'] != 'ACTIVE'
                or scope.payload['verificationState'] != 'VERIFIED'
                or scope.payload['workbookSchoolYear'] != context.get('expectedSchoolYear')
                or scope.payload['generation'] != context.get('expectedGeneration', -1) + (0 if context.get('previousSpreadsheetId') == spreadsheet_id else 1)
                or str(scope.payload.get('email') or '').casefold() != self.email.casefold()):
            raise AttendanceBindingError('ATTENDANCE_SCOPE_CHANGED')
        return scope

    def save_workbook_record(self, record, expected=None):
        from attendance_install_record import CONNECTION_FIELDS, validate_verified_canonical_record
        from attendance_server_record import record_from_scope
        checked = validate_verified_canonical_record(record)
        if checked.get('subject_key') != self._subject or str(checked.get('setup_account') or '').casefold() != self.email.casefold():
            raise AttendanceBindingError('ATTENDANCE_ACCOUNT_CHANGED')
        body = dict(spreadsheetId=checked['spreadsheet_id'], generation=checked['binding_generation'],
            workbookSchoolYear=int(checked['school_year']), protocolVersion=PROTOCOL_VERSION,
            resourceManifest={key: checked[key] for key in CONNECTION_FIELDS},
            monthlySheetIds=checked.get('monthly_sheet_ids'),
            verification={key: checked[key] for key in ('script_attestation', 'script_update_required') if key in checked})
        if expected is not None:
            body['expectedVerification'] = {key: expected[key] for key in ('script_attestation', 'script_update_required') if key in expected}
            body['expectedManifest'] = {key: expected[key] for key in CONNECTION_FIELDS}
        value = self._request('POST', '/v1/attendance/current/record', body)
        scope = AttendanceScope.parse(value, expected_subject=self._subject)
        if scope.payload.get('spreadsheetId') != checked['spreadsheet_id'] or scope.payload.get('generation') != checked['binding_generation']:
            raise AttendanceBindingError('ATTENDANCE_SCOPE_CHANGED')
        return record_from_scope(scope, self.email)

    def store_reference(self, field, value, key):
        cache_key = (self._subject, key)
        if self._reference_cache.get(cache_key) == value:
            return
        result = self._request('POST', '/v1/attendance/references', {'field': field, 'value': value, 'key': key})
        if result.get('key') != key:
            raise AttendanceBindingError('ATTENDANCE_INVALID_RESPONSE')
        self._reference_cache[cache_key] = value

    def read_reference(self, key):
        if not isinstance(key, str) or not re.fullmatch('[a-f0-9]{64}', key):
            raise AttendanceBindingError('ATTENDANCE_INVALID_RESPONSE')
        if (self._subject, key) in self._reference_cache:
            return self._reference_cache[(self._subject, key)]
        result = self._request('GET', '/v1/attendance/references/' + key, safe_read=True)
        if result.get('key') != key or not isinstance(result.get('value'), str):
            raise AttendanceBindingError('ATTENDANCE_INVALID_RESPONSE')
        self._reference_cache[(self._subject, key)] = result['value']
        return result['value']

    def adopt(self, spreadsheet_id: str) -> AttendanceScope:
        value = self._request("POST", "/v1/attendance/current/adopt", {"spreadsheetId": spreadsheet_id})
        return AttendanceScope.parse(value, expected_subject=self._subject)

    def discover(self) -> AttendanceScope:
        value = self._request("POST", "/v1/attendance/current/discover", {})
        return AttendanceScope.parse(value, expected_subject=self._subject)

    def adoption_candidate(self, spreadsheet_id):
        value = self._request("POST", "/v1/attendance/current/adoption-candidate", {"spreadsheetId": spreadsheet_id})
        scope = AttendanceScope.parse(value, expected_subject=self._subject)
        if value.get("authorized") is not True or value.get("adoptionAllowed") is not True:
            raise AttendanceBindingError("ATTENDANCE_ADOPTION_NOT_ALLOWED")
        return scope

    def authorize_workbook(self, record, *, purpose="repair"):
        from attendance_sheet_layout import validate_month_sheet_ids
        if record.get("subject_key") != self._subject or record.get("binding_protocol_version") != 1:
            raise AttendanceBindingError("ATTENDANCE_SCOPE_CHANGED")
        body = {"spreadsheetId": record["spreadsheet_id"], "workbookSchoolYear": int(record["school_year"]),
                "generation": record.get("binding_generation"), "monthlySheetIds": validate_month_sheet_ids(record.get("monthly_sheet_ids")),
                "purpose": purpose, "protocolVersion": PROTOCOL_VERSION}
        value = self._request("POST", "/v1/attendance/workbook/authorize", body)
        if (value.get("authorized") is not True or value.get("subjectKey") != self._subject
                or value.get("spreadsheetId") != body["spreadsheetId"] or value.get("generation") != body["generation"]
                or value.get("workbookSchoolYear") != body["workbookSchoolYear"]):
            raise AttendanceBindingError("ATTENDANCE_SCOPE_CHANGED")
        return value

    def replacement_check(self, scope: AttendanceScope, previous_spreadsheet_id: str, previous_operation_id: str = '', *, reason="replace-trashed") -> AttendanceScope:
        # An older service must fail at the public feature handshake, before
        # receiving an unsupported mutation or interpreting an ambiguous 404.
        capabilities = self._request("GET", "/v1/attendance/capabilities", authenticated=False, safe_read=True)
        if reason not in ('replace-trashed', 'replace-unavailable'):
            raise AttendanceBindingError('ATTENDANCE_SCOPE_CHANGED')
        capability = 'attendanceUnavailableReplacement' if reason == 'replace-unavailable' else 'attendanceTrashReplacement'
        if capabilities.get("protocolVersion") != PROTOCOL_VERSION or capabilities.get(capability) is not True:
            raise AttendanceBindingError("ATTENDANCE_PROTOCOL_REQUIRED", failure_domain="central-attendance", recovery_action="update-attendance-service")
        previous_operation_id = previous_operation_id or (scope.payload.get('operationId') if scope.payload['bindingState'] in ('PREPARING', 'CREATE_RESULT_UNKNOWN') else '')
        if reason == 'replace-trashed' and previous_operation_id and capabilities.get('attendancePendingTrashReplacement') is not True:
            raise AttendanceBindingError("ATTENDANCE_PROTOCOL_REQUIRED", failure_domain='central-attendance', recovery_action='update-attendance-service')
        value = self._request("POST", "/v1/attendance/current/replacement-check", {
            "previousSpreadsheetId": previous_spreadsheet_id, "expectedSchoolYear": scope.year,
            "expectedGeneration": scope.payload["generation"],
            **({'reason': reason} if reason == 'replace-unavailable' else {}),
            **({"previousOperationId": previous_operation_id} if previous_operation_id else {}),
        }, safe_read=True)
        checked = AttendanceScope.parse(value, expected_subject=self._subject)
        if checked.replacement_allowed and checked.payload['replacement']['reason'] != reason:
            raise AttendanceBindingError('ATTENDANCE_SCOPE_CHANGED')
        checked.require_same(scope)
        if checked.replacement_allowed and checked.payload["replacement"]["previousSpreadsheetId"] != previous_spreadsheet_id:
            raise AttendanceBindingError("ATTENDANCE_SCOPE_CHANGED")
        if checked.replacement_allowed and checked.payload['replacement'].get('previousOperationId') != (previous_operation_id or None):
            raise AttendanceBindingError("ATTENDANCE_SCOPE_CHANGED")
        return checked

    def start(self, scope: AttendanceScope, idempotency_key: str, reason: str, *, previous_spreadsheet_id: str = "", previous_operation_id: str = "", trigger: str = "", explicit_confirmation=False, failure_code="", failure_stage=""):
        fresh = self.current()
        fresh.require_same(scope)
        body = {"idempotencyKey": idempotency_key, "expectedSchoolYear": scope.year,
                "expectedGeneration": scope.payload["generation"], "reason": reason}
        if trigger:
            if trigger != 'initial-auto' or not fresh.initial_preparation_allowed:
                raise AttendanceBindingError('ATTENDANCE_CREATION_NOT_ALLOWED')
            body['trigger'] = trigger
        if reason in ("replace-trashed", "replace-unavailable"):
            if reason == 'replace-unavailable':
                if explicit_confirmation is not True or not previous_operation_id:
                    raise AttendanceBindingError('ATTENDANCE_CREATION_NOT_ALLOWED')
                body.update(explicitConfirmation=True, failureCode=failure_code, failureStage=failure_stage)
            checked = self.replacement_check(fresh, previous_spreadsheet_id, previous_operation_id,
                **({'reason': reason} if reason == 'replace-unavailable' else {}))
            if reason == "replace-unavailable" and any(checked.payload.get("replacement", {}).get(key) != body[key] for key in ("failureCode", "failureStage")):
                raise AttendanceBindingError("ATTENDANCE_SCOPE_CHANGED")
            if not checked.replacement_allowed:
                raise AttendanceBindingError("ATTENDANCE_REPLACEMENT_SOURCE_CHANGED")
            if checked.payload['replacement'].get('previousOperationId') != (previous_operation_id or None):
                raise AttendanceBindingError("ATTENDANCE_SCOPE_CHANGED")
            body["previousSpreadsheetId"] = previous_spreadsheet_id
            if previous_operation_id:
                body['previousOperationId'] = previous_operation_id
        elif not fresh.create_allowed:
            raise AttendanceBindingError("ATTENDANCE_CREATION_NOT_ALLOWED")
        return self._request("POST", "/v1/attendance/current/start", body)

    def operation(self, operation_id: str):
        return self._request("GET", self._operation_path(operation_id), safe_read=True)

    @staticmethod
    def _operation_path(operation_id):
        if not isinstance(operation_id, str) or not operation_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in operation_id):
            raise AttendanceBindingError("ATTENDANCE_OPERATION_INVALID")
        return "/v1/attendance/operations/" + operation_id

    def operation_action(self, operation_id: str, action: str, body: dict):
        if action not in ("claim", "dispatch", "evidence", "publish", "checkpoint", "authorize"):
            raise AttendanceBindingError("ATTENDANCE_OPERATION_INVALID")
        return self._request("POST", self._operation_path(operation_id) + "/" + action, body)


class RegistryCreationOperation:
    """One durable server operation; every external create is journaled first."""
    def __init__(self, client: AttendanceBindingClient, scope: AttendanceScope, operation: dict):
        self.client, self.scope = client, scope
        self._lease_owner = str(uuid.uuid4())
        self.operation = operation
        self._validate(operation)

    def _validate(self, operation):
        if (not isinstance(operation, dict) or not operation.get("operationId")
                or operation.get("subjectKey") != self.scope.payload["subjectKey"]
                or operation.get("schoolYear") != self.scope.year):
            raise AttendanceBindingError("ATTENDANCE_SCOPE_CHANGED")
        if type(operation.get("generation")) is not int or operation["generation"] != self.scope.payload["generation"]:
            raise AttendanceBindingError("ATTENDANCE_SCOPE_CHANGED")
        if not isinstance(operation.get("resources", {}), dict) or not isinstance(operation.get("progress", {}), dict):
            raise AttendanceBindingError("ATTENDANCE_OPERATION_INVALID")
        if self.operation.get("reason") in ("replace-trashed", "replace-unavailable"):
            if any(operation.get(key) != self.operation.get(key) for key in ("operationId", "reason", "previousSpreadsheetId", "previousOperationId")):
                raise AttendanceBindingError("ATTENDANCE_SCOPE_CHANGED")

    @property
    def year(self):
        return self.operation["schoolYear"]

    @property
    def operation_id(self):
        return self.operation["operationId"]

    def _action(self, action, **values):
        body = {"fence": self.operation.get("fence"), "leaseOwner": self.operation.get("leaseOwner"), **values}
        response = self.client.operation_action(self.operation_id, action, body)
        if action != "publish":
            self._validate(response)
            self.operation = response
        return response

    def claim(self):
        value = self._action("claim", leaseOwner=self._lease_owner)
        if value.get("leaseOwner") != self._lease_owner:
            raise AttendanceBindingError("ATTENDANCE_OPERATION_STALE")
        return value

    def authorize_write(self):
        # The service independently checks current year, generation and lease.
        self.claim()
        return self._action("authorize")

    def checkpoint(self, progress):
        return self._action("checkpoint", progress=progress)

    def reconcile_progress(self, progress):
        """Recovered exact IDs may resolve an uncertain intent, never repeat it."""
        fields = {"template": "template_doc_id", "spreadsheet": "spreadsheet_id", "folder": "folder_id",
                  "taskList": "task_list_id", "script": "script_id", "scriptVersion": "pending_deployment_version_number", "deployment": "deployment_id"}
        for kind, key in fields.items():
            resource = self.operation.get("resources", {}).get(kind, {})
            resource_id = str(progress.get(key) or "")
            if resource_id and resource.get("phase") == "DISPATCHED_OR_UNKNOWN":
                self._action("evidence", resourceKind=kind, resourceId=resource_id, intent=resource["intent"])

    def publish(self):
        value = self._action("publish", expectedGeneration=self.scope.payload["generation"])
        if value.get("historical") is True:
            return HistoricalPublicationReceipt.parse(value, operation=self.operation)
        return AttendanceScope.parse(value, expected_subject=self.scope.payload["subjectKey"])

    def runner(self, underlying):
        if getattr(underlying, "_attendance_registry_operation", None) is self:
            return underlying
        def execute(args, cwd):
            words = list(args)
            command = tuple(words[1:4])
            # Reads cannot dispatch, even when an old operation is uncertain.
            verbs = {"create", "insert", "update", "delete", "clear", "batchUpdate", "updateContent", "+push"}
            if not any(word in verbs for word in words[1:6]):
                return underlying(args, cwd)
            self.authorize_write()
            kinds = {
                ("tasks", "tasklists", "insert"): "taskList",
                ("script", "projects", "create"): "script",
                ("script", "projects", "versions"): "scriptVersion",
                ("script", "projects", "deployments"): "deployment",
            }
            kind = kinds.get(command) if "create" in words[1:5] or "insert" in words[1:5] else None
            body = {}
            if "--json" in words:
                try:
                    body = json.loads(words[words.index("--json") + 1])
                except (IndexError, ValueError):
                    raise AttendanceBindingError("ATTENDANCE_OPERATION_INVALID") from None
            if command == ("drive", "files", "create"):
                kind = {"application/vnd.google-apps.document": "template", "application/vnd.google-apps.spreadsheet": "spreadsheet", "application/vnd.google-apps.folder": "folder"}.get(body.get("mimeType"))
            if kind is None:
                return underlying(args, cwd)
            properties = body.get("appProperties") or {}
            intent = str(properties.get("teacherManagerInstallIntent") or
                         hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest())
            prior = self.operation.get("resources", {}).get(kind, {})
            if prior.get("phase") in ("DISPATCHED_OR_UNKNOWN", "CONFIRMED_RESULT"):
                raise AttendanceBindingError("ATTENDANCE_RESULT_UNKNOWN")
            self._action("dispatch", resourceKind=kind, intent=intent)
            try:
                result = underlying(args, cwd)  # exactly one attempt, no mutation retry
            except Exception as error:
                # Even a transport-reported rejection cannot erase the durable
                # server intent. Reconcile that original intent before reuse.
                raise AttendanceBindingError("ATTENDANCE_RESULT_UNKNOWN") from error
            try:
                from brity_bridge.process_win import parse_first_json
                payload = result if isinstance(result, dict) else parse_first_json(result)
                key = {"script": "scriptId", "scriptVersion": "versionNumber", "deployment": "deploymentId"}.get(kind, "id")
                resource_id = str(payload.get(key) or "")
            except (AttributeError, TypeError, ValueError):
                raise AttendanceBindingError("ATTENDANCE_RESULT_UNKNOWN") from None
            if not resource_id:
                raise AttendanceBindingError("ATTENDANCE_RESULT_UNKNOWN")
            self._action("evidence", resourceKind=kind, resourceId=resource_id, intent=intent)
            return result
        execute._attendance_registry_operation = self
        return execute
