"""현재 정본 출석부와 그 하위 연결을 읽기만 하여 상태 행으로 만든다."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from brity_bridge import bundle_paths, paths, process_win
from dashboard import central_chat
from dashboard.connection_status import (
    ComparedValue,
    ConnectionAction,
    ConnectionSource,
    blocked_status,
    connected_status,
)


_SHEET_MIME_TYPE = "application/vnd.google-apps.spreadsheet"
_CANONICAL_ROLE_KEY = "teacherManagerAttendanceRole"
_CANONICAL_ROLE = "canonical-v1"
_CANONICAL_YEAR_KEY = "teacherManagerAttendanceSchoolYear"
_SETTINGS_RANGE = "설정!A1:D200"
_PERSONAL_QUEUE = "메신저 개인톡 내용"
_CLASS_QUEUE = "메신저 단체톡 내용"
_PERSONAL_HEADERS = (
    "보낼 날짜", "번호", "이름", "쪽지 종류", "쪽지 내용", "들어온 곳", "상태",
    "연결 표시", "보낸 시각", "결과",
)
_CLASS_HEADERS = (
    "보낼 날짜", "안내 종류", "안내 내용", "들어온 곳", "상태", "보낸 시각", "결과",
)
_ROW_LABELS = {
    "attendance.sheet": "출결 DB 관리",
    "attendance.docs": "결석 신고서 자동완성",
    "attendance.tasks": "조종례시 출결서류 미제출 안내",
    "attendance.chat": "미제출 출결서류 지참 요청 문자 전송",
    "attendance.class-space": "학급 단톡방",
    "attendance.personal-queue": "개인톡 대기 시트",
    "attendance.class-queue": "단체톡 대기 시트",
    "attendance.script": "출결 자동 기능",
    "attendance.ai-trigger": "AI 출결 입력 감지기",
}
_ROW_IDS = tuple(_ROW_LABELS)

_ALLOWED_ACTION_IDS = frozenset({
    "goto-settings-google",
    "goto-work-calendar",
    "goto-school-calendar",
    "goto-work-tasks",
    "goto-homeroom-tasks",
    "goto-gemini",
    "goto-identity-year",
    "goto-identity-homeroom",
    "attendance-connection-choose",
    "attendance-open-first-setup",
    "attendance-script-check",
    "chat-connect",
    "class-space-reload",
    "retry-connection-check",
})

_FAILURE_VALUES = {
    "ATTENDANCE_NOT_CONFIGURED": (
        ComparedValue("필요한 정본", "현재 출석부 Sheet ID"),
        ComparedValue("저장된 정본", "없음"),
    ),
    "SHEET_NOT_FOUND": (
        ComparedValue("저장된 Sheet ID", "확인 대상"),
        ComparedValue("Google 조회 결과", "없음"),
    ),
    "SHEET_ACCESS_DENIED": (
        ComparedValue("저장된 Sheet ID", "확인 대상"),
        ComparedValue("Google 조회 결과", "접근 거부"),
    ),
    "SHEET_UNREACHABLE": (
        ComparedValue("필요한 확인", "현재 정본 읽기"),
        ComparedValue("Google 조회 결과", "읽기 실패"),
    ),
    "FIRST_SETUP_NOT_DONE": (
        ComparedValue("필요한 처음 설정", "현재 정본에서 완료"),
        ComparedValue("Sheet 확인 결과", "완료 안 됨"),
    ),
    "FIRST_SETUP_MARKER_MISSING": (
        ComparedValue("필요한 확인 표시", "TM 연결번호 1개"),
        ComparedValue("Sheet 확인 표시", "없음"),
    ),
    "FIRST_SETUP_MARKER_DUPLICATED": (
        ComparedValue("필요한 확인 표시", "정확히 1개"),
        ComparedValue("Sheet 확인 표시", "2개 이상"),
    ),
    "DOC_NOT_CONFIGURED": (
        ComparedValue("필요한 Docs ID", "결석 신고서 문서"),
        ComparedValue("저장된 Docs ID", "없음"),
    ),
    "DOC_NOT_FOUND": (
        ComparedValue("저장된 Docs ID", "확인 대상"),
        ComparedValue("Google 조회 결과", "없음"),
    ),
    "DOC_ACCESS_DENIED": (
        ComparedValue("저장된 Docs ID", "확인 대상"),
        ComparedValue("Google 조회 결과", "접근 거부"),
    ),
    "TASK_LIST_NOT_CONFIGURED": (
        ComparedValue("필요한 Tasks ID", "출결 안내 목록"),
        ComparedValue("저장된 Tasks ID", "없음"),
    ),
    "TASK_LIST_NOT_FOUND": (
        ComparedValue("저장된 Tasks ID", "확인 대상"),
        ComparedValue("Google 조회 결과", "없음"),
    ),
    "TASK_LIST_ACCESS_DENIED": (
        ComparedValue("저장된 Tasks ID", "확인 대상"),
        ComparedValue("Google 조회 결과", "접근 거부"),
    ),
    "SCRIPT_UPDATE_AVAILABLE": (
        ComparedValue("필요한 상태", "정식 최신판"),
        ComparedValue("현재 상태", "업데이트 필요"),
    ),
    "SCRIPT_FINISHING_REQUIRED": (
        ComparedValue("필요한 상태", "정식 최신판 배포 완료"),
        ComparedValue("현재 상태", "마지막 연결 필요"),
    ),
    "SCRIPT_CUSTOMIZED": (
        ComparedValue("필요한 상태", "확인된 정식 파일"),
        ComparedValue("현재 상태", "사용자 변경 있음"),
    ),
    "SCRIPT_UNREACHABLE": (
        ComparedValue("필요한 확인", "Apps Script 현재 상태"),
        ComparedValue("현재 확인", "읽기 실패"),
    ),
    "AI_TRIGGER_MISSING": (
        ComparedValue("필요한 감지기", "현재 정본 대상 1개"),
        ComparedValue("확인된 감지기", "0개"),
    ),
    "AI_TRIGGER_DUPLICATED": (
        ComparedValue("필요한 감지기", "현재 정본 대상 1개"),
        ComparedValue("확인된 감지기", "2개 이상"),
    ),
    "AI_TARGET_MISMATCH": (
        ComparedValue("필요한 대상", "현재 정본 Sheet"),
        ComparedValue("감지기 대상", "다른 Sheet"),
    ),
    "SERVER_UNREACHABLE": (
        ComparedValue("필요한 확인", "Chat 서버 현재 상태"),
        ComparedValue("서버 조회 결과", "읽기 실패"),
    ),
    "SHEET_NOT_REGISTERED": (
        ComparedValue("필요한 등록", "현재 정본 Sheet"),
        ComparedValue("서버 등록 결과", "없음"),
    ),
    "SHEET_MOVED": (
        ComparedValue("필요한 서버 대상", "현재 정본 Sheet"),
        ComparedValue("서버 확인 결과", "이전 Sheet에서 이동됨"),
    ),
    "SHEET_AUTH_REQUIRED": (
        ComparedValue("필요한 서버 인증", "현재 정본 Sheet 등록"),
        ComparedValue("서버 확인 결과", "인증 필요"),
    ),
    "GOEDU_ACCOUNT_REQUIRED": (
        ComparedValue("필요한 서버 계정", "@goedu.kr 학교 계정"),
        ComparedValue("서버 확인 결과", "허용 계정 아님"),
    ),
    "SPACE_NOT_SELECTED": (
        ComparedValue("필요한 학급방", "전체 ID 1개"),
        ComparedValue("저장된 학급방", "없음"),
    ),
    "SPACE_LIST_UNREACHABLE": (
        ComparedValue("저장된 학급방 ID", "확인 대상"),
        ComparedValue("Google 조회 결과", "읽기 실패"),
    ),
    "SPACE_NOT_FOUND": (
        ComparedValue("저장된 학급방 ID", "확인 대상"),
        ComparedValue("Google 조회 결과", "없음"),
    ),
    "PERSONAL_QUEUE_SHEET_MISSING": (
        ComparedValue("필요한 탭", _PERSONAL_QUEUE),
        ComparedValue("현재 탭", "없음"),
    ),
    "CLASS_QUEUE_SHEET_MISSING": (
        ComparedValue("필요한 탭", _CLASS_QUEUE),
        ComparedValue("현재 탭", "없음"),
    ),
    "EXTERNAL_UNREACHABLE": (
        ComparedValue("필요한 확인", "저장된 Google 대상"),
        ComparedValue("Google 조회 결과", "읽기 실패"),
    ),
}

_UPSTREAM_FAILURE_VALUES = {
    "GWS_RUNTIME_MISSING": (
        ComparedValue("필요한 Google 도구", "GWS 실행 파일"),
        ComparedValue("현재 Google 도구", "없음"),
    ),
    "ACCOUNT_STORAGE_UNSAFE": (
        ComparedValue("정식 저장 위치", "현재 Windows 사용자 폴더 안"),
        ComparedValue("현재 저장 위치", "안전하지 않은 위치"),
    ),
    "OAUTH_CLIENT_MISSING": (
        ComparedValue("필요한 OAuth 설정", "데스크톱 OAuth 1개"),
        ComparedValue("현재 OAuth 설정", "없음"),
    ),
    "OAUTH_CLIENT_CONFLICT": (
        ComparedValue("필요한 OAuth 설정", "데스크톱 OAuth 1개"),
        ComparedValue("현재 OAuth 설정", "둘 이상 충돌"),
    ),
    "LOGIN_REQUIRED": (
        ComparedValue("필요한 Google 계정", "@goedu.kr 로그인"),
        ComparedValue("현재 로그인 상태", "로그아웃"),
    ),
    "ACCOUNT_DOMAIN_NOT_ALLOWED": (
        ComparedValue("허용 Google 계정", "@goedu.kr 학교 계정"),
        ComparedValue("현재 로그인 계정", "허용되지 않은 계정"),
    ),
    "SCOPE_GRANT_STALE": (
        ComparedValue("필요한 Google 권한", "현재 계정의 최신 권한"),
        ComparedValue("현재 권한 상태", "확인 필요"),
    ),
    "EXTERNAL_UNREACHABLE": (
        ComparedValue("필요한 확인", "Google 로그인 상태"),
        ComparedValue("현재 확인", "읽기 실패"),
    ),
}


def _default_script_status(record: dict, google, config_dir: Path, run_command):
    from attendance_script_update import inspect_attendance_script_update

    return inspect_attendance_script_update(
        record.get("spreadsheet_id"),
        record.get("script_id"),
        record.get("deployment_id"),
        assets_dir=bundle_paths.bundle_root() / "assets",
        runner=lambda args, _cwd=None: _run_raw(run_command, args),
        gws_executable=google.gws_executable,
    )


def _default_ai_trigger_status(record: dict, google, config_dir: Path, run_command):
    from attendance_ai_setup import inspect_attendance_ai_setup

    return inspect_attendance_ai_setup(
        runner=lambda args, _cwd=None: _run_raw(run_command, args),
        workdir=Path(config_dir),
        gws_executable=google.gws_executable,
        spreadsheet_id=record.get("spreadsheet_id", ""),
    )


def _default_chat_status(config_dir: Path, google, _record: dict, run_command):
    return central_chat.chat_status(
        Path(config_dir),
        run_command,
        gws_executable=google.gws_executable,
    )


def _default_space_list(config_dir: Path, google, _record: dict, run_command):
    return central_chat.list_spaces(
        Path(config_dir),
        run_command,
        gws_executable=google.gws_executable,
    )


@dataclass
class AttendanceProbeDeps:
    run_command: Callable[[list[str]], Any]
    script_status_reader: Callable[[dict, Any, Path], Any] | None = None
    ai_trigger_reader: Callable[[dict, Any, Path], Any] | None = None
    chat_status_reader: Callable[[Path, Any, dict], dict] | None = None
    space_list_reader: Callable[[Path, Any, dict], list] | None = None


def _value(payload: Any, key: str, default: Any = "") -> Any:
    if isinstance(payload, dict):
        return payload.get(key, default)
    return getattr(payload, key, default)


def _source(checked_at: str, google, *, kind: str = "external-live"):
    return ConnectionSource(
        kind=kind,
        checked_at=checked_at,
        account=str(getattr(google, "account", "") or "").strip(),
    )


def _blocked(
    item_id: str,
    *,
    reason_code: str,
    reason_ko: str,
    source: ConnectionSource,
    category: str = "saved_data_mismatch",
    action_id: str = "retry-connection-check",
    action_label: str = "다시 확인",
    expected: ComparedValue = ComparedValue(),
    actual: ComparedValue = ComparedValue(),
    level: str = "blocked",
):
    default_expected, default_actual = _FAILURE_VALUES.get(
        reason_code,
        (
            ComparedValue("필요한 상태", "정상 연결"),
            ComparedValue("현재 상태", "연결 안 됨"),
        ),
    )
    if not expected.label or not expected.value:
        expected = default_expected
    if not actual.label or not actual.value:
        actual = default_actual
    if action_id not in _ALLOWED_ACTION_IDS:
        action_id, action_label = "retry-connection-check", "다시 확인"
    return blocked_status(
        id=item_id,
        group="attendance",
        label=_ROW_LABELS[item_id],
        category=category,
        reason_code=reason_code,
        reason_ko=reason_ko,
        expected=expected,
        actual=actual,
        action=ConnectionAction(action_id, action_label),
        source=source,
        level=level,
    )


def _copy_status(status, item_id: str):
    expected, actual = status.expected, status.actual
    if status.group == "google" and status.reason_code in _UPSTREAM_FAILURE_VALUES:
        expected, actual = _UPSTREAM_FAILURE_VALUES[status.reason_code]
        if status.reason_code == "ACCOUNT_DOMAIN_NOT_ALLOWED":
            actual = ComparedValue(
                actual.label,
                str(getattr(status.source, "account", "") or actual.value),
            )
    return _blocked(
        item_id,
        reason_code=status.reason_code,
        reason_ko=status.reason_ko,
        category=status.category,
        source=status.source,
        action_id=status.action.id,
        action_label=status.action.label,
        expected=expected,
        actual=actual,
        level=status.level,
    )


def _all_blocked(status):
    return tuple(_copy_status(status, item_id) for item_id in _ROW_IDS)


def _dependents_blocked(sheet_status, status):
    return (sheet_status,) + tuple(
        _copy_status(status, item_id) for item_id in _ROW_IDS if item_id != "attendance.sheet"
    )


def _connected(item_id: str, source: ConnectionSource, *, notice_code: str = "",
               expected: ComparedValue = ComparedValue(), actual: ComparedValue = ComparedValue()):
    degraded = bool(notice_code)
    return connected_status(
        id=item_id,
        group="attendance",
        label=_ROW_LABELS[item_id],
        source=source,
        level="degraded" if degraded else "ok",
        category="degraded" if degraded else "ok",
        notice_code=notice_code,
        expected=expected,
        actual=actual,
    )


def _profile_values(config_dir: Path) -> dict[str, str]:
    import csv

    result: dict[str, str] = {}
    try:
        with (Path(config_dir) / "teacher-profile.csv").open(
            "r", newline="", encoding="utf-8-sig"
        ) as handle:
            for row in csv.DictReader(handle):
                key = str(row.get("항목") or "").strip()
                if key:
                    result[key] = str(row.get("값") or "").strip()
    except OSError:
        pass
    return result


def _load_record(config_dir: Path) -> dict:
    from attendance_install_record import load_attendance_install_record

    return load_attendance_install_record(
        paths.attendance_install_record_path(Path(config_dir))
    )


def _url_id(value: Any, *, kind: str) -> str:
    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError:
        return ""
    if parsed.scheme != "https" or parsed.hostname != "docs.google.com":
        return ""
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    prefix = ["spreadsheets", "d"] if kind == "sheet" else ["document", "d"]
    return parts[2].strip() if len(parts) >= 3 and parts[:2] == prefix else ""


def _run_raw(run_command, args: list[str]) -> tuple[int, str]:
    result = run_command(list(args))
    if isinstance(result, tuple):
        if len(result) < 2:
            return 1, ""
        try:
            code = int(result[0])
        except (TypeError, ValueError):
            code = 1
        return code, str(result[1] or "")
    return 0, str(result or "")


def _read_json(deps: AttendanceProbeDeps, args: list[str]) -> tuple[int, Any, str]:
    code, output = _run_raw(deps.run_command, args)
    if code != 0:
        return code, None, output
    try:
        return 0, process_win.parse_first_json(output), output
    except (TypeError, ValueError):
        return 1, None, ""


def _params_read(deps, google, *parts_and_params):
    *parts, params = parts_and_params
    args = [
        google.gws_executable,
        *parts,
        "get",
        "--params",
        json.dumps(params, ensure_ascii=False, separators=(",", ":")),
        "--format",
        "json",
    ]
    return _read_json(deps, args)


def _remote_kind(output: str) -> str:
    text = str(output or "").casefold()
    if any(word in text for word in ("403", "permission", "access denied", "forbidden")):
        return "denied"
    if any(word in text for word in ("404", "not-found", "not found")):
        return "missing"
    return "unreachable"


def _settings_values(rows: Any) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    if not isinstance(rows, list):
        return found
    for row in rows:
        if not isinstance(row, list) or not row:
            continue
        key = str(row[0] or "").strip()
        if key:
            found.setdefault(key, []).append(str(row[1] or "").strip() if len(row) > 1 else "")
    return found


def _one(settings: dict[str, list[str]], key: str) -> str:
    values = settings.get(key, [])
    return values[0] if len(values) == 1 else ""


def _format_year(value: Any) -> str:
    text = str(value or "").strip()
    return f"{text}학년도" if text else ""


def _format_homeroom(grade: Any, klass: Any) -> str:
    grade_text = str(grade or "").strip()
    class_text = str(klass or "").strip()
    return f"{grade_text}학년 {class_text}반" if grade_text or class_text else ""


def _sheet_blocker(config_dir: Path, google, deps, checked_at: str):
    source = _source(checked_at, google)
    try:
        record = _load_record(config_dir)
    except Exception:
        return None, None, _blocked(
            "attendance.sheet",
            reason_code="ATTENDANCE_NOT_CONFIGURED",
            reason_ko="현재 연결된 정식 출석부가 없어요.",
            category="not_configured",
            source=_source(checked_at, google, kind="local-read"),
            action_id="attendance-connection-choose",
            action_label="연결 확인/바꾸기",
        )
    sheet_id = str(record.get("spreadsheet_id") or "").strip()
    if not sheet_id:
        return record, None, _blocked(
            "attendance.sheet", reason_code="ATTENDANCE_NOT_CONFIGURED",
            reason_ko="현재 연결된 정식 출석부가 없어요.", category="not_configured",
            source=_source(checked_at, google, kind="local-read"),
            action_id="attendance-connection-choose", action_label="연결 확인/바꾸기",
        )
    linked_id = _url_id(record.get("spreadsheet_url"), kind="sheet")
    if linked_id != sheet_id:
        return record, None, _blocked(
            "attendance.sheet", reason_code="SHEET_URL_ID_MISMATCH",
            reason_ko="저장된 출석부 번호와 열기 주소의 번호가 달라요.", source=source,
            action_id="attendance-connection-choose", action_label="연결 확인/바꾸기",
            expected=ComparedValue("저장된 Sheet ID", sheet_id),
            actual=ComparedValue("URL 속 Sheet ID", linked_id),
        )
    saved_account = str(record.get("setup_account") or "").strip()
    current_account = str(getattr(google, "account", "") or "").strip()
    if not saved_account or saved_account.casefold() != current_account.casefold():
        return record, None, _blocked(
            "attendance.sheet", reason_code="SAVED_ACCOUNT_MISMATCH",
            reason_ko="처음 연결한 계정과 현재 Google 계정이 달라요.",
            category="login_account_mismatch", source=source,
            action_id="goto-settings-google", action_label="Google 로그인 열기",
            expected=ComparedValue("처음 연결 계정", saved_account),
            actual=ComparedValue("현재 로그인 계정", current_account),
        )
    profile = _profile_values(config_dir)
    profile_year = str(profile.get("학년도") or "").strip()
    record_year = str(record.get("school_year") or "").strip()
    if profile_year != record_year:
        return record, None, _blocked(
            "attendance.sheet", reason_code="SCHOOL_YEAR_MISMATCH",
            reason_ko=(f"내 정보의 학년도는 {_format_year(profile_year)}인데, "
                       f"연결된 출석부는 {_format_year(record_year)}예요."), source=source,
            action_id="goto-identity-year", action_label="내 정보의 학년도 고치기",
            expected=ComparedValue("내 정보 학년도", _format_year(profile_year)),
            actual=ComparedValue("연결된 출석부 학년도", _format_year(record_year)),
        )
    profile_grade = str(profile.get("담임학년") or "").strip()
    profile_class = str(profile.get("담임반") or "").strip()
    record_grade = str(record.get("homeroom_grade") or "").strip()
    record_class = str(record.get("homeroom_class") or "").strip()
    if (profile_grade, profile_class) != (record_grade, record_class):
        return record, None, _blocked(
            "attendance.sheet", reason_code="GRADE_CLASS_MISMATCH",
            reason_ko="내 정보의 담임 학급과 연결된 출석부의 담임 학급이 달라요.", source=source,
            action_id="goto-identity-homeroom", action_label="내 정보의 담임 학급 고치기",
            expected=ComparedValue("내 정보 담임 학급", _format_homeroom(profile_grade, profile_class)),
            actual=ComparedValue("출석부 담임 학급", _format_homeroom(record_grade, record_class)),
        )
    code, metadata, output = _params_read(
        deps, google, "drive", "files",
        {"fileId": sheet_id, "fields": "id,name,mimeType,trashed,ownedByMe,appProperties"},
    )
    if code != 0 or not isinstance(metadata, dict):
        kind = _remote_kind(output)
        reason = "SHEET_ACCESS_DENIED" if kind == "denied" else "SHEET_NOT_FOUND" if kind == "missing" else "SHEET_UNREACHABLE"
        return record, None, _blocked(
            "attendance.sheet", reason_code=reason,
            reason_ko="현재 계정으로 저장된 출석부를 확인하지 못했어요.",
            category="external_unreachable" if kind == "unreachable" else "saved_data_mismatch",
            source=source,
            action_id="goto-settings-google" if kind == "denied" else "attendance-connection-choose" if kind == "missing" else "retry-connection-check",
            action_label="Google 로그인 열기" if kind == "denied" else "연결 확인/바꾸기" if kind == "missing" else "다시 확인",
            expected=ComparedValue("저장된 Sheet ID", sheet_id),
            level="unknown" if kind == "unreachable" else "blocked",
        )
    properties = metadata.get("appProperties") if isinstance(metadata.get("appProperties"), dict) else {}
    actual_id = str(metadata.get("id") or "").strip()
    actual_mime_type = str(metadata.get("mimeType") or "").strip()
    actual_year = str(properties.get(_CANONICAL_YEAR_KEY) or "").strip()
    actual_role = str(properties.get(_CANONICAL_ROLE_KEY) or "").strip()
    actual_kind = str(record.get("workbook_role") or "").strip()
    marker_comparisons = (
        (actual_id == sheet_id,
         ComparedValue("저장된 Sheet ID", sheet_id),
         ComparedValue("Google 파일 ID", actual_id or "없음")),
        (actual_mime_type == _SHEET_MIME_TYPE,
         ComparedValue("정식 Google 파일 종류", _SHEET_MIME_TYPE),
         ComparedValue("실제 Google 파일 종류", actual_mime_type or "없음")),
        (metadata.get("trashed") is False,
         ComparedValue("정식 휴지통 상태", "휴지통 아님"),
         ComparedValue(
             "Google 현재 상태",
             "휴지통에 있음" if metadata.get("trashed") is True else "확인 안 됨",
         )),
        (metadata.get("ownedByMe") is True,
         ComparedValue("필요한 소유권", "현재 계정 소유"),
         ComparedValue(
             "Google 소유권",
             "현재 계정 소유 아님" if metadata.get("ownedByMe") is False else "확인 안 됨",
         )),
        (actual_year == record_year,
         ComparedValue("정식 표식 학년도", record_year or "없음"),
         ComparedValue("Sheet 표식 학년도", actual_year or "없음")),
        (actual_role == _CANONICAL_ROLE,
         ComparedValue("정식 role", _CANONICAL_ROLE),
         ComparedValue("Sheet 실제 role", actual_role or "없음")),
        (actual_kind == _CANONICAL_ROLE,
         ComparedValue("정식 기록 kind", _CANONICAL_ROLE),
         ComparedValue("설치 기록 kind", actual_kind or "없음")),
    )
    marker_mismatch = next(
        ((expected, actual) for matches, expected, actual in marker_comparisons if not matches),
        None,
    )
    if marker_mismatch is not None:
        expected, actual = marker_mismatch
        return record, metadata, _blocked(
            "attendance.sheet", reason_code="CANONICAL_MARKER_MISMATCH",
            reason_ko="저장된 파일이 Teacher Manager의 정식 출석부인지 확인되지 않아요.", source=source,
            action_id="attendance-connection-choose", action_label="연결 확인/바꾸기",
            expected=expected,
            actual=actual,
        )
    code, settings_reply, output = _params_read(
        deps, google, "sheets", "spreadsheets", "values",
        {"spreadsheetId": sheet_id, "range": _SETTINGS_RANGE},
    )
    if code != 0 or not isinstance(settings_reply, dict):
        return record, metadata, _blocked(
            "attendance.sheet", reason_code="SHEET_UNREACHABLE",
            reason_ko="출석부의 설정 내용을 지금 읽지 못했어요.",
            category="external_unreachable", source=source, level="unknown",
        )
    return record, (metadata, _settings_values(settings_reply.get("values", []))), None


def _first_setup_blocker(record: dict, settings: dict[str, list[str]], source):
    from attendance_workbook_identity import attendance_connection_code

    expected_code = attendance_connection_code(record.get("spreadsheet_id"))
    codes = settings.get("ATTENDANCE_CONNECTION_CODE", [])
    if not codes:
        return _blocked(
            "attendance.docs", reason_code="FIRST_SETUP_MARKER_MISSING",
            reason_ko="출석부의 처음 설정 확인 표시가 없어요.", source=source,
            action_id="attendance-open-first-setup", action_label="처음 설정 열기",
        )
    if len(codes) != 1:
        return _blocked(
            "attendance.docs", reason_code="FIRST_SETUP_MARKER_DUPLICATED",
            reason_ko="출석부의 처음 설정 확인 표시가 둘 이상이라 안전하게 고르지 못해요.", source=source,
            action_id="attendance-open-first-setup", action_label="처음 설정 열기",
        )
    if codes[0] != expected_code:
        return _blocked(
            "attendance.docs", reason_code="CANONICAL_MARKER_MISMATCH",
            reason_ko="출석부의 연결 확인번호가 현재 정본과 달라요.", source=source,
            action_id="attendance-connection-choose", action_label="연결 확인/바꾸기",
            expected=ComparedValue("현재 정본 연결 확인번호", expected_code),
            actual=ComparedValue("Sheet 연결 확인번호", codes[0]),
        )
    done_values = settings.get("FIRST_TIME_SETUP_DONE", [])
    if not done_values:
        return _blocked(
            "attendance.docs", reason_code="FIRST_SETUP_NOT_DONE",
            reason_ko="출석부의 처음 한 번 설정이 아직 끝나지 않았어요.", source=source,
            action_id="attendance-open-first-setup", action_label="처음 설정 열기",
        )
    if len(done_values) != 1:
        return _blocked(
            "attendance.docs", reason_code="FIRST_SETUP_MARKER_DUPLICATED",
            reason_ko="처음 설정 완료 표시가 둘 이상이라 안전하게 확인하지 못해요.", source=source,
            action_id="attendance-open-first-setup", action_label="처음 설정 열기",
        )
    parts = done_values[0].split()
    saved_account = str(record.get("setup_account") or "").strip()
    if len(parts) < 2 or parts[0] != expected_code:
        return _blocked(
            "attendance.docs", reason_code="FIRST_SETUP_NOT_DONE",
            reason_ko="출석부의 처음 한 번 설정이 현재 정본에서 끝나지 않았어요.", source=source,
            action_id="attendance-open-first-setup", action_label="처음 설정 열기",
        )
    if parts[1].casefold() != saved_account.casefold():
        return _blocked(
            "attendance.docs", reason_code="FIRST_SETUP_ACCOUNT_MISMATCH",
            reason_ko="처음 설정을 마친 계정과 현재 출석부의 설치 계정이 달라요.", source=source,
            action_id="attendance-open-first-setup", action_label="처음 설정 열기",
            expected=ComparedValue("출석부 설치 계정", saved_account),
            actual=ComparedValue("처음 설정 완료 계정", parts[1]),
        )
    return None


def _resource_failure(item_id, *, kind, saved_id, source, missing_code, denied_code, action_id, action_label):
    target_label = "저장된 Docs ID" if item_id == "attendance.docs" else "저장된 Tasks ID"
    actual_value = "접근 거부" if kind == "denied" else "없음" if kind == "missing" else "읽기 실패"
    if kind == "denied":
        reason = denied_code
        action_id, action_label = "goto-settings-google", "Google 로그인 열기"
        category, level = "saved_data_mismatch", "blocked"
    elif kind == "missing":
        reason = missing_code
        category, level = "saved_data_mismatch", "blocked"
    else:
        reason = "EXTERNAL_UNREACHABLE"
        action_id, action_label = "retry-connection-check", "다시 확인"
        category, level = "external_unreachable", "unknown"
    return _blocked(
        item_id, reason_code=reason, reason_ko="저장된 Google 대상을 지금 확인하지 못했어요.",
        category=category, source=source, action_id=action_id, action_label=action_label,
        expected=ComparedValue(target_label, saved_id),
        actual=ComparedValue("Google 조회 결과", actual_value), level=level,
    )


def _docs_status(record, settings, google, deps, source):
    item_id = "attendance.docs"
    saved_id = str(record.get("template_doc_id") or "").strip()
    if not saved_id:
        return _blocked(item_id, reason_code="DOC_NOT_CONFIGURED", reason_ko="결석 신고서 문서가 아직 연결되지 않았어요.", category="not_configured", source=source, action_id="attendance-open-first-setup", action_label="처음 설정 열기")
    url_id = _url_id(record.get("template_doc_url"), kind="doc")
    if url_id != saved_id:
        return _blocked(item_id, reason_code="DOC_URL_ID_MISMATCH", reason_ko="저장된 문서 번호와 주소의 번호가 달라요.", source=source, action_id="attendance-open-first-setup", action_label="처음 설정 열기", expected=ComparedValue("저장된 Docs ID", saved_id), actual=ComparedValue("URL 속 Docs ID", url_id))
    sheet_id = _one(settings, "TEMPLATE_DOC_ID")
    if sheet_id != saved_id:
        return _blocked(item_id, reason_code="SHEET_CONFIG_DOC_MISMATCH", reason_ko="출석부 설정의 문서 번호와 설치 기록이 달라요.", source=source, action_id="attendance-open-first-setup", action_label="처음 설정 열기", expected=ComparedValue("설치 기록 Docs ID", saved_id), actual=ComparedValue("Sheet 설정 Docs ID", sheet_id))
    code, reply, output = _params_read(deps, google, "docs", "documents", {"documentId": saved_id})
    if code != 0 or not isinstance(reply, dict) or str(reply.get("documentId") or "") != saved_id:
        return _resource_failure(item_id, kind=_remote_kind(output) if code else "missing", saved_id=saved_id, source=source, missing_code="DOC_NOT_FOUND", denied_code="DOC_ACCESS_DENIED", action_id="attendance-open-first-setup", action_label="처음 설정 열기")
    return _connected(item_id, source)


def _tasks_status(config_dir, record, settings, google, deps, source):
    item_id = "attendance.tasks"
    saved_id = str(record.get("task_list_id") or "").strip()
    if not saved_id:
        return _blocked(item_id, reason_code="TASK_LIST_NOT_CONFIGURED", reason_ko="출결 안내용 할 일 목록이 아직 연결되지 않았어요.", category="not_configured", source=source, action_id="attendance-open-first-setup", action_label="처음 설정 열기")
    sheet_id = _one(settings, "TASK_LIST_ID")
    if sheet_id != saved_id:
        return _blocked(item_id, reason_code="SHEET_CONFIG_TASK_MISMATCH", reason_ko="출석부 설정의 할 일 목록 번호와 설치 기록이 달라요.", source=source, action_id="attendance-open-first-setup", action_label="처음 설정 열기", expected=ComparedValue("설치 기록 Tasks ID", saved_id), actual=ComparedValue("Sheet 설정 Tasks ID", sheet_id))
    profile_id = str(_profile_values(config_dir).get("담임안내Tasks목록ID") or "").strip()
    if profile_id != saved_id:
        return _blocked(item_id, reason_code="TASK_LIST_CONTEXT_MISMATCH", reason_ko="현재 내 정보의 담임 할 일 목록과 출석부 목록이 달라요.", source=source, action_id="goto-homeroom-tasks", action_label="담임 Tasks 고르기", expected=ComparedValue("현재 내 정보 Tasks ID", profile_id), actual=ComparedValue("출석부 Tasks ID", saved_id))
    code, reply, output = _params_read(deps, google, "tasks", "tasklists", {"tasklist": saved_id})
    if code != 0 or not isinstance(reply, dict) or str(reply.get("id") or "") != saved_id:
        return _resource_failure(item_id, kind=_remote_kind(output) if code else "missing", saved_id=saved_id, source=source, missing_code="TASK_LIST_NOT_FOUND", denied_code="TASK_LIST_ACCESS_DENIED", action_id="attendance-open-first-setup", action_label="처음 설정 열기")
    return _connected(item_id, source)


def _script_status(record, google, config_dir, deps, source):
    reader = deps.script_status_reader
    try:
        payload = reader(record, google, config_dir) if reader else _default_script_status(record, google, config_dir, deps.run_command)
        state = str(_value(payload, "state", "") or "")
    except Exception:
        state = "hold"
    reasons = {
        "update_available": ("SCRIPT_UPDATE_AVAILABLE", "출결 기능 업데이트"),
        "finishing_required": ("SCRIPT_FINISHING_REQUIRED", "출결 기능 업데이트"),
        "customized": ("SCRIPT_CUSTOMIZED", "출결 기능 업데이트"),
        "hold": ("SCRIPT_UNREACHABLE", "다시 확인"),
    }
    if state == "current":
        return _connected("attendance.script", source)
    reason, label = reasons.get(state, ("SCRIPT_UNREACHABLE", "다시 확인"))
    return _blocked(
        "attendance.script", reason_code=reason,
        reason_ko="출결 자동 기능의 현재 상태를 확인하거나 최신판으로 맞춰야 해요.",
        category="external_unreachable" if reason == "SCRIPT_UNREACHABLE" else "feature_unavailable",
        source=source, action_id="retry-connection-check" if reason == "SCRIPT_UNREACHABLE" else "attendance-script-check",
        action_label=label, level="unknown" if reason == "SCRIPT_UNREACHABLE" else "blocked",
    )


def _ai_status(record, google, config_dir, deps, source):
    reader = deps.ai_trigger_reader
    try:
        payload = reader(record, google, config_dir) if reader else _default_ai_trigger_status(record, google, config_dir, deps.run_command)
    except Exception:
        payload = {}
    count = _value(payload, "trigger_count", 0)
    target_matches = bool(_value(payload, "spreadsheet_matches", False)) and bool(_value(payload, "target_matches", False))
    ok = _value(payload, "ok", False) is True and type(count) is int and count == 1 and target_matches
    if ok:
        return _connected("attendance.ai-trigger", source)
    reason = "AI_TRIGGER_MISSING" if type(count) is not int or count < 1 else "AI_TRIGGER_DUPLICATED" if count > 1 else "AI_TARGET_MISMATCH"
    actual_value = (
        f"{count}개" if type(count) is int and reason != "AI_TARGET_MISMATCH"
        else "다른 Sheet"
    )
    return _blocked(
        "attendance.ai-trigger", reason_code=reason,
        reason_ko="AI 출결 입력 감지기를 현재 정본에 정확히 하나 연결해야 해요.",
        category="feature_unavailable", source=source,
        action_id="attendance-script-check", action_label="AI 출결 입력 연결 확인",
        expected=ComparedValue(
            "필요한 감지기" if reason != "AI_TARGET_MISMATCH" else "필요한 대상",
            "현재 정본 대상 1개" if reason != "AI_TARGET_MISMATCH" else "현재 정본 Sheet",
        ),
        actual=ComparedValue(
            "확인된 감지기" if reason != "AI_TARGET_MISMATCH" else "감지기 대상",
            actual_value,
        ),
    )


def _server_sheet_file_id(value: Any) -> str:
    text = str(value or "").strip()
    return text.split(":", 1)[0] if text else ""


def _chat_rows(config_dir, record, settings, google, deps, source):
    reader = deps.chat_status_reader
    try:
        status = reader(config_dir, google, record) if reader else _default_chat_status(config_dir, google, record, deps.run_command)
    except Exception:
        status = {"connected": False, "registered": False, "reason_code": "SERVER_UNREACHABLE"}
    canonical_id = str(record.get("spreadsheet_id") or "")
    local_server_id = str(_value(status, "sheet_id", "") or _one(settings, "CENTRAL_CHAT_SHEET_ID")).strip()
    server_sheet_id = str(_value(status, "server_sheet_id", "") or "").strip()
    account = str(_value(status, "account", "") or "").strip()
    current_account = str(getattr(google, "account", "") or "").strip()
    original_reason = str(_value(status, "reason_code", "") or "")
    if original_reason == "SERVER_UNREACHABLE":
        chat = _blocked("attendance.chat", reason_code="SERVER_UNREACHABLE", reason_ko="Google Chat 발송 서버 상태를 지금 확인하지 못했어요.", category="external_unreachable", source=source, level="unknown")
    elif not bool(_value(status, "registered", False)):
        chat = _blocked("attendance.chat", reason_code="SHEET_NOT_REGISTERED", reason_ko="현재 출석부가 Chat 발송 서버에 등록되지 않았어요.", category="not_configured", source=source, action_id="chat-connect", action_label="Google Chat 연결하기")
    elif account.casefold() != current_account.casefold() or account.casefold() != str(record.get("setup_account") or "").casefold():
        chat = _blocked("attendance.chat", reason_code="SERVER_ACCOUNT_MISMATCH", reason_ko="Chat 발송 서버의 계정과 현재 학교 계정이 달라요.", category="login_account_mismatch", source=source, action_id="chat-connect", action_label="Google Chat 연결하기", expected=ComparedValue("현재 학교 계정", current_account), actual=ComparedValue("서버 연결 계정", account))
    elif _server_sheet_file_id(local_server_id) != canonical_id or (server_sheet_id and _server_sheet_file_id(server_sheet_id) != canonical_id):
        chat = _blocked("attendance.chat", reason_code="SERVER_SHEET_MISMATCH", reason_ko="Chat 발송 서버가 현재 정본 출석부와 연결되지 않았어요.", source=source, action_id="chat-connect", action_label="Google Chat 연결하기", expected=ComparedValue("현재 정본 Sheet ID", canonical_id), actual=ComparedValue("서버 Sheet ID", server_sheet_id or local_server_id))
    elif not bool(_value(status, "connected", False)):
        safe_reason = original_reason if original_reason in {"SHEET_MOVED", "SHEET_AUTH_REQUIRED", "GOEDU_ACCOUNT_REQUIRED"} else "SERVER_UNREACHABLE"
        chat = _blocked("attendance.chat", reason_code=safe_reason, reason_ko="Google Chat 발송 연결을 다시 확인해야 해요.", category="external_unreachable" if safe_reason == "SERVER_UNREACHABLE" else "saved_data_mismatch", source=source, action_id="chat-connect", action_label="Google Chat 연결하기", level="unknown" if safe_reason == "SERVER_UNREACHABLE" else "blocked")
    else:
        chat = _connected("attendance.chat", source)

    local_space_id = str(_value(status, "class_space_id", "") or _one(settings, "CLASS_CHAT_SPACE_ID")).strip()
    class_space_matches = _value(status, "class_space_matches", None)
    if not local_space_id:
        class_space = _blocked("attendance.class-space", reason_code="SPACE_NOT_SELECTED", reason_ko="학급 단톡방을 아직 고르지 않았어요.", category="not_configured", source=source, action_id="class-space-reload", action_label="학급 단톡방 고르기")
    elif class_space_matches is False:
        class_space = _blocked("attendance.class-space", reason_code="SPACE_SERVER_SHEET_MISMATCH", reason_ko="출석부와 발송 서버의 학급방 번호가 달라요.", source=source, action_id="class-space-reload", action_label="학급 단톡방 고르기", expected=ComparedValue("Sheet 학급방 ID", local_space_id), actual=ComparedValue("서버 비교 결과", "불일치"))
    else:
        lister = deps.space_list_reader
        try:
            spaces = lister(config_dir, google, record) if lister else _default_space_list(config_dir, google, record, deps.run_command)
        except Exception:
            spaces = None
        if spaces is None:
            class_space = _blocked("attendance.class-space", reason_code="SPACE_LIST_UNREACHABLE", reason_ko="현재 계정의 학급방 목록을 읽지 못했어요.", category="external_unreachable", source=source, level="unknown", expected=ComparedValue("저장된 학급방 ID", local_space_id), actual=ComparedValue("Google 조회 결과", "읽기 실패"))
        else:
            names = {str(space.get("name") or "") for space in spaces if isinstance(space, dict)}
            if local_space_id not in names:
                class_space = _blocked("attendance.class-space", reason_code="SPACE_NOT_FOUND", reason_ko="현재 계정에서 저장된 학급 단톡방을 찾지 못했어요.", source=source, action_id="class-space-reload", action_label="학급 단톡방 고르기", expected=ComparedValue("저장된 학급방 ID", local_space_id))
            else:
                matched = next(
                    space for space in spaces
                    if isinstance(space, dict) and str(space.get("name") or "") == local_space_id
                )
                saved_name = str(_value(status, "class_space_name", "") or "").strip()
                actual_name = str(matched.get("displayName") or "").strip()
                stale = bool(saved_name and actual_name and saved_name != actual_name)
                class_space = _connected(
                    "attendance.class-space", source,
                    notice_code="SPACE_LABEL_STALE" if stale else "",
                    expected=ComparedValue("저장된 표시 이름", saved_name) if stale else ComparedValue(),
                    actual=ComparedValue("Google 현재 이름", actual_name) if stale else ComparedValue(),
                )
    return chat, class_space


def _queue_rows(record, google, deps, source):
    sheet_id = str(record.get("spreadsheet_id") or "")
    code, reply, output = _params_read(
        deps, google, "sheets", "spreadsheets",
        {"spreadsheetId": sheet_id, "fields": "sheets.properties.title"},
    )
    if code != 0 or not isinstance(reply, dict):
        status = _blocked("attendance.personal-queue", reason_code="EXTERNAL_UNREACHABLE", reason_ko="출석부의 대기 시트 목록을 지금 읽지 못했어요.", category="external_unreachable", source=source, level="unknown", expected=ComparedValue("필요한 탭 목록", f"{_PERSONAL_QUEUE}, {_CLASS_QUEUE}"), actual=ComparedValue("Sheet 탭 조회 결과", "읽기 실패"))
        return status, _copy_status(status, "attendance.class-queue")
    titles = {
        str(item.get("properties", {}).get("title") or "")
        for item in reply.get("sheets", []) if isinstance(item, dict)
    }
    result = []
    for item_id, title, headers, missing_code in (
        ("attendance.personal-queue", _PERSONAL_QUEUE, _PERSONAL_HEADERS, "PERSONAL_QUEUE_SHEET_MISSING"),
        ("attendance.class-queue", _CLASS_QUEUE, _CLASS_HEADERS, "CLASS_QUEUE_SHEET_MISSING"),
    ):
        if title not in titles:
            result.append(_blocked(item_id, reason_code=missing_code, reason_ko=f"출석부에 {title} 탭이 없어요.", source=source, action_id="attendance-script-check", action_label="출결 기능 업데이트"))
            continue
        code, header_reply, output = _params_read(
            deps, google, "sheets", "spreadsheets", "values",
            {"spreadsheetId": sheet_id, "range": f"'{title}'!1:1"},
        )
        values = header_reply.get("values", []) if isinstance(header_reply, dict) else []
        actual = tuple(str(value) for value in values[0]) if values and isinstance(values[0], list) else ()
        if code != 0:
            result.append(_blocked(item_id, reason_code="EXTERNAL_UNREACHABLE", reason_ko=f"{title}의 첫 줄을 지금 읽지 못했어요.", category="external_unreachable", source=source, level="unknown", expected=ComparedValue("읽어야 할 첫 줄", title), actual=ComparedValue("Sheet 조회 결과", "읽기 실패")))
        elif actual != headers:
            result.append(_blocked(item_id, reason_code="QUEUE_SCHEMA_MISMATCH", reason_ko=f"{title}의 첫 줄 항목이 정식 모양과 달라요.", source=source, action_id="attendance-script-check", action_label="출결 기능 업데이트", expected=ComparedValue("정식 첫 줄", ", ".join(headers)), actual=ComparedValue("Sheet 실제 첫 줄", ", ".join(actual) or "빈 첫 줄")))
        else:
            result.append(_connected(item_id, source))
    return tuple(result)


def read_attendance_connection_statuses(
    config_dir: Path, google, deps: AttendanceProbeDeps
) -> tuple:
    """현재 설치 기록의 전체 Sheet ID 하나만 따라가며, 어떤 자료도 쓰지 않는다."""

    config_dir = Path(config_dir)
    checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
    if getattr(google, "blocker", None) is not None:
        return _all_blocked(google.blocker)
    record, sheet_data, blocker = _sheet_blocker(config_dir, google, deps, checked_at)
    if blocker is not None:
        return _all_blocked(blocker)
    metadata, settings = sheet_data
    source = _source(checked_at, google)
    expected_name = str(record.get("workbook_name") or "").strip()
    actual_name = str(metadata.get("name") or "").strip()
    sheet = _connected(
        "attendance.sheet", source,
        notice_code="WORKBOOK_NAME_MISMATCH" if expected_name != actual_name else "",
        expected=ComparedValue("정식 표시 이름", expected_name) if expected_name != actual_name else ComparedValue(),
        actual=ComparedValue("Google 현재 이름", actual_name) if expected_name != actual_name else ComparedValue(),
    )
    first_setup = _first_setup_blocker(record, settings, source)
    if first_setup is not None:
        if first_setup.reason_code == "CANONICAL_MARKER_MISMATCH":
            return _all_blocked(first_setup)
        return _dependents_blocked(sheet, first_setup)
    docs = _docs_status(record, settings, google, deps, source)
    tasks = _tasks_status(config_dir, record, settings, google, deps, source)
    chat, class_space = _chat_rows(config_dir, record, settings, google, deps, source)
    personal_queue, class_queue = _queue_rows(record, google, deps, source)
    script = _script_status(record, google, config_dir, deps, source)
    ai_trigger = _ai_status(record, google, config_dir, deps, source)
    return (
        sheet,
        docs,
        tasks,
        chat,
        class_space,
        personal_queue,
        class_queue,
        script,
        ai_trigger,
    )


__all__ = ["AttendanceProbeDeps", "read_attendance_connection_statuses"]
