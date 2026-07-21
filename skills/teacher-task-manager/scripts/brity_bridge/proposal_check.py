from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

TITLE_MAX = 1000
BODY_MAX = 30000

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RFC3339_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TASK_PERIOD_SUFFIX_RE = re.compile(r"\s*\(\d+(?:\s*[-~]\s*\d+)?교시\)\s*$")

CALENDAR_TARGET_ID_FIELDS = {
    "work_calendar": "work_calendar_id",
    "school_calendar": "school_calendar_id",
}
TASK_TARGET_ID_FIELDS = {
    "homeroom_tasks": "homeroom_tasks_id",
    "work_tasks": "work_tasks_id",
}
NOTICE_AUDIENCES = ("personal", "class")


class CheckError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class CheckedAction:
    kind: str
    target: str
    google_id: str
    payload: dict
    action_key: str


def build_action_key(kind: str, target: str, date_text: str, title: str) -> str:
    digest = hashlib.sha256(title.encode("utf-8")).hexdigest()[:8]
    return f"{kind}:{target}:{date_text}:{digest}"


def task_title_without_period_suffix(title: str) -> str:
    return _TASK_PERIOD_SUFFIX_RE.sub("", str(title or "")).strip()


def _check_text(value: str, limit: int, label: str, problems: list[str]) -> None:
    if len(value) > limit:
        problems.append(f"{label}이 {limit:,}자를 넘음")
    if _CONTROL_RE.search(value):
        problems.append(f"{label}에 제어 문자가 있음")


def _parse_timed(value: str, label: str, problems: list[str]) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        problems.append(f"{label} 형식이 잘못됨")
        return None
    if parsed.tzinfo is None:
        problems.append(f"{label}에 시간대 표시가 없음")
        return None
    return parsed


def _parse_date(value: str, label: str, problems: list[str]) -> date | None:
    if not _DATE_RE.match(value):
        problems.append(f"{label}은 종일 일정이라 YYYY-MM-DD여야 함")
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        problems.append(f"{label} 형식이 잘못됨")
        return None


def _check_event(event: dict, index: int, profile_calendars: dict, problems: list[str]) -> CheckedAction | None:
    label = f"calendar_events[{index}]"
    target = event["target"]
    id_field = CALENDAR_TARGET_ID_FIELDS.get(target)
    if id_field is None:
        problems.append(f"{label} 허용되지 않은 대상")
        return None
    google_id = profile_calendars.get(id_field, "")
    if not google_id:
        problems.append(f"{label} 대상 {target}의 실제 ID가 설정에 없음")
        return None

    _check_text(event["summary"], TITLE_MAX, f"{label} 제목", problems)
    _check_text(event["description"], BODY_MAX, f"{label} 설명", problems)

    if event["all_day"]:
        start = _parse_date(event["start"], f"{label} 시작", problems)
        end = _parse_date(event["end"], f"{label} 끝", problems)
        if start is None or end is None:
            return None
        if end < start:
            problems.append(f"{label} 끝 날짜가 시작보다 빠름")
            return None
        payload = {
            "summary": event["summary"],
            "description": event["description"],
            "start": {"date": start.isoformat()},
            "end": {"date": (end + timedelta(days=1)).isoformat()},
        }
        date_text = start.isoformat()
    else:
        start = _parse_timed(event["start"], f"{label} 시작", problems)
        end = _parse_timed(event["end"], f"{label} 끝", problems)
        if start is None or end is None:
            return None
        if end <= start:
            problems.append(f"{label} 끝 시각이 시작보다 뒤가 아님")
            return None
        payload = {
            "summary": event["summary"],
            "description": event["description"],
            "start": {"dateTime": event["start"], "timeZone": "Asia/Seoul"},
            "end": {"dateTime": event["end"], "timeZone": "Asia/Seoul"},
        }
        date_text = start.date().isoformat()

    return CheckedAction(
        kind="calendar",
        target=target,
        google_id=google_id,
        payload=payload,
        action_key=build_action_key("calendar", target, date_text, event["summary"]),
    )


def _check_task(task: dict, index: int, profile: dict, problems: list[str]) -> CheckedAction | None:
    label = f"tasks[{index}]"
    target = task["target"]
    id_field = TASK_TARGET_ID_FIELDS.get(target)
    if id_field is None:
        problems.append(f"{label} 허용되지 않은 대상")
        return None
    if target == "homeroom_tasks" and not profile.get("homeroom", {}).get("enabled"):
        problems.append(f"{label} 비담임 설정이라 담임 Tasks를 만들 수 없음")
        return None
    google_id = profile.get("calendars", {}).get(id_field, "")
    if not google_id:
        problems.append(f"{label} 대상 {target}의 실제 ID가 설정에 없음")
        return None

    title = task_title_without_period_suffix(task["title"])
    _check_text(title, TITLE_MAX, f"{label} 제목", problems)
    _check_text(task["notes"], BODY_MAX, f"{label} 메모", problems)
    if not _RFC3339_UTC_RE.match(task["due"]):
        problems.append(f"{label} due 형식이 잘못됨 (RFC3339 UTC 필요)")
        return None
    if not title:
        problems.append(f"{label} 제목이 비어 있음")
        return None

    return CheckedAction(
        kind="task",
        target=target,
        google_id=google_id,
        payload={"title": title, "notes": task["notes"]},
        action_key=build_action_key("task", target, task["due"][:10], title),
    )


def _notice_payload(notice, homeroom_enabled: bool) -> dict | None:
    """학생 안내 한 줄 검사 — 문제가 있으면 그 줄만 조용히 버린다(전체 실패 아님)."""
    if not isinstance(notice, dict) or not homeroom_enabled:
        return None
    audience = str(notice.get("audience", "")).strip()
    if audience not in NOTICE_AUDIENCES:
        return None
    content = str(notice.get("content", "")).strip()
    if not content or len(content) > BODY_MAX or _CONTROL_RE.search(content):
        return None
    name = str(notice.get("name", "")).strip()
    if audience == "personal" and not name:
        return None
    payload = {"content": content}
    if audience == "personal":
        payload["name"] = name
    return payload


def check_proposal(
    proposal: dict, profile: dict, notice_sheet_id: str = "", today: date | None = None
) -> list[CheckedAction]:
    problems: list[str] = []
    actions: list[CheckedAction] = []
    profile_calendars = profile.get("calendars", {})

    for index, event in enumerate(proposal.get("calendar_events", [])):
        action = _check_event(event, index, profile_calendars, problems)
        if action is not None:
            actions.append(action)

    for index, task in enumerate(proposal.get("tasks", [])):
        action = _check_task(task, index, profile, problems)
        if action is not None:
            actions.append(action)

    if problems:
        raise CheckError(problems)

    homeroom_enabled = bool(profile.get("homeroom", {}).get("enabled"))
    date_text = (today or date.today()).isoformat()
    for notice in proposal.get("student_notices") or []:
        payload = _notice_payload(notice, homeroom_enabled)
        if payload is None:
            continue
        audience = str(notice.get("audience", "")).strip()
        actions.append(
            CheckedAction(
                kind="notice",
                target=audience,
                google_id=notice_sheet_id,
                payload=payload,
                action_key=build_action_key(
                    "notice", audience, date_text,
                    payload.get("name", "") + "|" + payload["content"],
                ),
            )
        )
    return actions


def add_work_task_copies(actions: list[CheckedAction], profile: dict) -> list[CheckedAction]:
    """업무 캘린더 일정마다 업무 할일 목록에 같은 제목의 체크리스트 항목을 덧붙인다.

    업무Tasks목록이 아직 연결되지 않았으면 아무것도 더하지 않는다. AI 제안이 아니라
    이 단계에서 복제해야 업무 일정이 항상 캘린더와 체크리스트 양쪽에 등록된다.
    """
    tasklist_id = str(profile.get("calendars", {}).get("work_tasks_id") or "")
    result = list(actions)
    if not tasklist_id:
        return result

    existing_keys = {action.action_key for action in actions}
    for action in actions:
        if action.kind != "calendar" or action.target != "work_calendar":
            continue
        start = action.payload.get("start", {})
        date_text = start.get("date") or str(start.get("dateTime", ""))[:10]
        if not date_text:
            continue
        title = task_title_without_period_suffix(action.payload.get("summary", ""))
        key = build_action_key("task", "work_tasks", date_text, title)
        if key in existing_keys:
            continue
        existing_keys.add(key)
        result.append(
            CheckedAction(
                kind="task",
                target="work_tasks",
                google_id=tasklist_id,
                payload={
                    "title": title,
                    "notes": action.payload.get("description", ""),
                },
                action_key=key,
            )
        )
    return result
