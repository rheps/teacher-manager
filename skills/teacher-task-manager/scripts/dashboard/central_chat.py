# skills/teacher-task-manager/scripts/dashboard/central_chat.py
"""중앙 Google Chat 발송소의 화면 문장과 학생 인사 요약 도우미."""
from __future__ import annotations

import ctypes
import hashlib
import os
import re

from brity_bridge import google_account


NOT_PREPARED_MESSAGE = "출결 준비가 아직 안 됐어요. 먼저 출결 준비 시작하기를 눌러 주세요."
CONFIG_BROKEN_MESSAGE = "출결 시트의 설정 값을 읽지 못했어요. 시트가 열리는지 확인해 주세요."
# 설정 탭은 제대로 읽었는데 필수 값(발송소 주소·시트 번호·확인값)이 비어 있는 결정적 상태.
# 다시 읽어도 같으므로 세 번 묶음에 넣지 않고 한 번에 멈춘다(2026-09-04). 화면에 보일 수
# 있는 문장이라 내부 값 이름을 넣지 않는다.
CONFIG_VALUE_MISSING_MESSAGE = (
    "출석부의 Google Chat 연결 정보가 비어 있어요. 출석부를 열고 위쪽 [🔵 처음 한 번 설정하기] → "
    "현재 연결 상태를 다시 확인해 주세요."
)
SERVER_ERROR_MESSAGE = "발송 서버와 연결하지 못했어요. 인터넷 연결을 확인해 주세요."
# 서버가 답을 준 경우는 인터넷 탓이 아니다. 답에 적힌 뜻을 그대로 옮긴다 —
# "인터넷을 확인하세요"라고만 하면 몇 번을 다시 눌러도 달라질 게 없다(2026-07-30 확인).
SHEET_MOVED_MESSAGE = (
    "Google Chat 발송은 다른 출석부로 이미 옮겼어요. "
    "Teacher Manager에서 현재 출석부를 열어 보내 주세요."
)
SHEET_AUTH_REQUIRED_MESSAGE = (
    "이 출석부에서 Google Chat을 보내기 위한 연결이 끝나지 않았어요. [Google 연결]의 [출결]에서 연결 상태를 확인해 주세요."
)
SPACE_BLOCKED_MESSAGE = (
    "Google에서 이 방 만들기 요청을 허용하지 않았어요. Google Chat에서 직접 방을 만든 뒤 목록을 다시 불러와 주세요."
)
SPACE_NAME_TAKEN_MESSAGE = "Google에서 다른 방 만들기 요청과 충돌했다고 알려 왔어요. Google Chat에서 이미 만들어진 방이 있는지 확인해 주세요."
SPACE_NAME_EMPTY_MESSAGE = "방 이름을 적어 주세요."
# 403(SPACE_CREATE_FORBIDDEN)·409(SPACE_NAME_TAKEN)가 아닌 나머지 실패는 전부 이 코드로 온다
# (services/central-chat-sender의 spaceCreateError 기본값).
SPACE_CREATE_FAILED_MESSAGE = "방 만들기 결과를 확인하지 못했어요. Google Chat에서 방이 만들어졌는지 확인해 주세요."
SPACE_CREATE_STALE_MESSAGE = "학급 단톡방 선택이 다른 창에서 바뀌었어요. 현재 상태를 다시 확인해 주세요."
GROUP_CHAT_TOO_MANY_MESSAGE = "Google Chat 그룹 채팅에는 선생님 외에 학생을 최대 49명까지 넣을 수 있어요."
GROUP_CHAT_EMAIL_INVALID_MESSAGE = (
    "학생 이메일 가운데 형식이 맞지 않는 주소가 있어 그룹 채팅을 만들지 않았어요. "
    "[학생명단] 탭 C열의 이메일을 확인해 주세요."
)
GROUP_CHAT_MEMBERS_REJECTED_MESSAGE = (
    "Google에서 학생 이메일 가운데 받을 수 없는 주소가 있다고 알려 왔어요. "
    "그룹 채팅은 만들지 않았어요. [학생명단] 탭 C열의 이메일을 확인해 주세요."
)
GOEDU_ACCOUNT_REQUIRED_MESSAGE = google_account.GOEDU_ACCOUNT_REQUIRED_MESSAGE
CHAT_AUTH_UNKNOWN_MESSAGE = "Google Chat 발송 권한을 확인하지 못했어요. 기존 연결은 그대로입니다."
CHAT_AUTH_PERMISSION_MESSAGE = "Google Chat 발송 권한이 부족해요. [연결하기]를 눌러 같은 Google 계정으로 필요한 권한을 허용해 주세요."
CHAT_AUTH_REFRESH_MESSAGE = "자동발송을 계속할 수 있도록 승인이 필요해요. [연결하기]를 눌러 같은 Google 계정으로 승인해 주세요."
CHAT_AUTH_ACCOUNT_MESSAGE = "출석부를 만든 계정과 Google Chat 발송 계정이 달라요. [연결하기]를 눌러 출석부를 만든 Google 계정을 선택해 주세요. 기존 연결은 그대로입니다."
CHAT_SHEET_ACCESS_MESSAGE = "Google Chat 발송 계정으로 현재 출석부를 열 수 없어요. 출석부 접근 권한을 확인한 뒤 [연결하기]를 눌러 주세요. 기존 연결은 그대로입니다."
SERVER_ANSWER_MESSAGES = {
    "SHEET_MOVED": SHEET_MOVED_MESSAGE,
    "SHEET_AUTH_REQUIRED": SHEET_AUTH_REQUIRED_MESSAGE,
    "SPACE_CREATE_FORBIDDEN": SPACE_BLOCKED_MESSAGE,
    "SPACE_NAME_TAKEN": SPACE_NAME_TAKEN_MESSAGE,
    "SPACE_DISPLAY_NAME_REQUIRED": SPACE_NAME_EMPTY_MESSAGE,
    "SPACE_CREATE_FAILED": SPACE_CREATE_FAILED_MESSAGE,
    "SPACE_CREATE_STALE": SPACE_CREATE_STALE_MESSAGE,
    "GROUP_CHAT_MEMBERS_REJECTED": GROUP_CHAT_MEMBERS_REJECTED_MESSAGE,
    "GROUP_CHAT_MEMBER_COUNT": GROUP_CHAT_TOO_MANY_MESSAGE,
    "GROUP_CHAT_MEMBERS_INVALID": GROUP_CHAT_EMAIL_INVALID_MESSAGE,
    "GOEDU_ACCOUNT_REQUIRED": GOEDU_ACCOUNT_REQUIRED_MESSAGE,
    "AUTH_GRANTS_UNKNOWN": CHAT_AUTH_UNKNOWN_MESSAGE,
    "AUTH_CHECK_FAILED": CHAT_AUTH_UNKNOWN_MESSAGE,
    "AUTH_PERMISSION_REQUIRED": CHAT_AUTH_PERMISSION_MESSAGE,
    "AUTH_REFRESH_REQUIRED": CHAT_AUTH_REFRESH_MESSAGE,
    "AUTH_ACCOUNT_MISMATCH": CHAT_AUTH_ACCOUNT_MESSAGE,
    "SPREADSHEET_ACCESS_DENIED": CHAT_SHEET_ACCESS_MESSAGE,
}
UNKNOWN_SERVER_ANSWER_MESSAGE = (
    "Google Chat 연결 요청을 처리하지 못했어요."
)
CHAT_STATUS_FAILURE_MESSAGE = (
    "학급 단톡방 상태를 확인하지 못했어요. Google Chat에서 방을 열어 확인해 주세요."
)
CHAT_HANDOVER_RECOVERY_REQUIRED_MESSAGE = (
    "Google Chat 연결이 중간에 멈춰 원래 대상 출석부 설정을 안전하게 확인할 수 없어요. "
    "다시 누르지 말고 출결 문제 해결에서 연결 상태를 확인해 주세요."
)
_SAFE_CENTRAL_MESSAGES = {
    NOT_PREPARED_MESSAGE,
    CONFIG_BROKEN_MESSAGE,
    CONFIG_VALUE_MISSING_MESSAGE,
    SERVER_ERROR_MESSAGE,
    UNKNOWN_SERVER_ANSWER_MESSAGE,
    CHAT_STATUS_FAILURE_MESSAGE,
    CHAT_HANDOVER_RECOVERY_REQUIRED_MESSAGE,
    *SERVER_ANSWER_MESSAGES.values(),
}


class CentralChatError(RuntimeError):
    pass


def _safe_central_error_detail(error, fallback: str = UNKNOWN_SERVER_ANSWER_MESSAGE) -> str:
    code = str(getattr(error, "server_code", "") or "")
    if code in SERVER_ANSWER_MESSAGES:
        return SERVER_ANSWER_MESSAGES[code]
    detail = str(error or "")
    return detail if detail in _SAFE_CENTRAL_MESSAGES else fallback


_SCC_EMAIL_RE = re.compile(r'[^@\s<>,;:"()[\]{}\\/]+@[^@\s<>,;:"()[\]{}\\/.]+(?:\.[^@\s<>,;:"()[\]{}\\/.]+)+')


# Everything else (ATTENDANCE_SEND_RESULT_UNKNOWN, ATTENDANCE_REQUEST_CONFLICT, 429, 5xx,
# timeouts, resets, unreadable replies) is unknown and never resent automatically.


def scc_email_key(email: str) -> str:
    return hashlib.sha256(str(email or "").strip().casefold().encode("utf-8")).hexdigest()


def _scc_label(student: dict) -> str:
    return f"{student['number']}{student['name']}"


def scc_summarize(roster: list[dict], students: dict) -> dict:
    """Counts and 번호+이름 labels for the screen; emails never leave this function."""
    targets, unknown, failed, no_email, fixed, seen = [], [], [], [], [], set()
    sent = total = 0
    roster_keys = {scc_email_key(row["email"]) for row in roster if row.get("email")}
    rejected_labels = {str(entry.get("label") or "") for key, entry in students.items()
                       if isinstance(entry, dict) and entry.get("state") == "rejected"
                       and key not in roster_keys and entry.get("label")}
    for student in roster:
        email = student["email"]
        if not email or not _SCC_EMAIL_RE.fullmatch(email):
            no_email.append({"label": _scc_label(student)})
            continue
        key = scc_email_key(email)
        if key in seen:
            continue
        seen.add(key)
        total += 1
        state = str((students.get(key) or {}).get("state") or "")
        row = {"key": key, "label": _scc_label(student)}
        if state == "" and _scc_label(student) in rejected_labels:
            # The teacher changed this student's email after a rejection (fixed, not yet greeted).
            row["fixed"] = True
            fixed.append(row)
        if state == "sent":
            sent += 1
        elif state in ("unknown", "pending"):
            unknown.append(row)
        else:
            row["failed"] = state == "rejected"
            if row["failed"]:
                failed.append(row)
            targets.append(row)
    fresh = len(targets) - len(failed)
    return {"state": "ready", "total": total, "sent": sent, "targets": targets,
            "failed": len(failed), "failed_labels": [row["label"] for row in failed],
            "fixed": fixed, "new": fresh if sent else 0, "unknown": unknown, "no_email": no_email}


CLASS_GREETING_DEFAULT = (
    "안녕하세요, {반이름} 여러분! 담임 선생님이에요. 이 채팅방은 우리 반 단톡방이에요. "
    "학급 공지와 안내는 앞으로 여기에 올릴게요. 결석·지각·조퇴 같은 출결이나 개인적인 일은 "
    "이 방이 아니라 선생님과의 1:1 채팅으로 보내 주세요. 잘 부탁해요!"
)
PERSONAL_GREETING_DEFAULT = (
    "안녕하세요, 담임 선생님이에요. 이 채팅은 선생님과 1:1로 이야기하는 곳이에요. "
    "결석·지각·조퇴 같은 출결이나 개인적인 일은 여기로 보내 주세요."
)


def class_greeting_defaults(profile: dict) -> tuple[str, str]:
    grade = str(profile.get("담임학년") or "").strip()
    room = str(profile.get("담임반") or "").strip()
    label = f"{grade}학년 {room}반" if grade and room else "우리 반"
    return CLASS_GREETING_DEFAULT.replace("{반이름}", label), PERSONAL_GREETING_DEFAULT


# Windows-user protection for the server session file (attendance_session_store).

def _dpapi_protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise CentralChatError(CHAT_HANDOVER_RECOVERY_REQUIRED_MESSAGE)
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DataBlob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source = ctypes.create_string_buffer(data)
    source_blob = DataBlob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
    protected = DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(source_blob), None, None, None, None, 0, ctypes.byref(protected)
    ):
        raise CentralChatError(CHAT_HANDOVER_RECOVERY_REQUIRED_MESSAGE)
    try:
        return ctypes.string_at(protected.pbData, protected.cbData)
    finally:
        kernel32.LocalFree(protected.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise CentralChatError(CHAT_HANDOVER_RECOVERY_REQUIRED_MESSAGE)
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source = ctypes.create_string_buffer(data)
    source_blob = DataBlob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
    unprotected = DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source_blob), None, None, None, None, 0, ctypes.byref(unprotected)
    ):
        raise CentralChatError(CHAT_HANDOVER_RECOVERY_REQUIRED_MESSAGE)
    try:
        return ctypes.string_at(unprotected.pbData, unprotected.cbData)
    finally:
        kernel32.LocalFree(unprotected.pbData)
