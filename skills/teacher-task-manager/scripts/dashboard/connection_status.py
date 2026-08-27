"""Shared data contract for connection checks shown by the dashboard."""

from dataclasses import asdict, dataclass
from typing import Iterable


VALID_CATEGORIES = frozenset(
    {
        "ok",
        "degraded",
        "not_configured",
        "saved_data_mismatch",
        "login_account_mismatch",
        "external_unreachable",
        "feature_unavailable",
    }
)


def _validate_category(category: str) -> None:
    if not isinstance(category, str) or category not in VALID_CATEGORIES:
        raise ValueError(f"unsupported connection category: {category!r}")


@dataclass(frozen=True)
class ComparedValue:
    label: str = ""
    value: str = ""


@dataclass(frozen=True)
class ConnectionAction:
    id: str = ""
    label: str = ""


@dataclass(frozen=True)
class ConnectionSource:
    kind: str
    checked_at: str
    account: str = ""
    ttl_seconds: int = 180


@dataclass(frozen=True)
class ConnectionStatus:
    id: str
    group: str
    label: str
    connected: bool
    level: str
    category: str
    reason_code: str
    reason_ko: str
    expected: ComparedValue
    actual: ComparedValue
    action: ConnectionAction
    source: ConnectionSource
    notice_code: str = ""

    def __post_init__(self) -> None:
        _validate_category(self.category)


@dataclass(frozen=True)
class ConnectionReport:
    checked_at: str
    items: tuple[ConnectionStatus, ...]

    def as_payload(self) -> dict:
        primary = primary_group_issue(self.items, "attendance")
        return {
            "checked_at": self.checked_at,
            "items": [asdict(item) for item in self.items],
            "primary_group_issue": asdict(primary) if primary is not None else None,
        }


def connected_status(
    *,
    id: str,
    group: str,
    label: str,
    source: ConnectionSource,
    level: str = "ok",
    category: str = "ok",
    reason_code: str = "",
    reason_ko: str = "",
    expected: ComparedValue = ComparedValue(),
    actual: ComparedValue = ComparedValue(),
    action: ConnectionAction = ConnectionAction(),
    notice_code: str = "",
) -> ConnectionStatus:
    return ConnectionStatus(
        id=id,
        group=group,
        label=label,
        connected=True,
        level=level,
        category=category,
        reason_code=reason_code,
        reason_ko=reason_ko,
        expected=expected,
        actual=actual,
        action=action,
        source=source,
        notice_code=notice_code,
    )


def blocked_status(
    *,
    id: str,
    group: str,
    label: str,
    category: str,
    reason_code: str,
    reason_ko: str,
    expected: ComparedValue,
    actual: ComparedValue,
    action: ConnectionAction,
    source: ConnectionSource,
    level: str = "blocked",
    notice_code: str = "",
) -> ConnectionStatus:
    return ConnectionStatus(
        id=id,
        group=group,
        label=label,
        connected=False,
        level=level,
        category=category,
        reason_code=reason_code,
        reason_ko=reason_ko,
        expected=expected,
        actual=actual,
        action=action,
        source=source,
        notice_code=notice_code,
    )


# Earlier checks prevent later checks from producing misleading repair advice.
# Codes not listed below are still handled safely and sort after known causes.
ATTENDANCE_REASON_PRIORITY = (
    "GWS_RUNTIME_MISSING",
    "OAUTH_CLIENT_MISSING",
    "OAUTH_CLIENT_CONFLICT",
    "ACCOUNT_STORAGE_UNSAFE",
    "LOGIN_REQUIRED",
    "ACCOUNT_DOMAIN_NOT_ALLOWED",
    "SCOPE_GRANT_STALE",
    "EXTERNAL_UNREACHABLE",
    "ATTENDANCE_NOT_CONFIGURED",
    "SHEET_URL_ID_MISMATCH",
    "SAVED_ACCOUNT_MISMATCH",
    "SCHOOL_YEAR_MISMATCH",
    "GRADE_CLASS_MISMATCH",
    "SHEET_NOT_FOUND",
    "SHEET_ACCESS_DENIED",
    "CANONICAL_MARKER_MISMATCH",
    "FIRST_SETUP_NOT_DONE",
    "FIRST_SETUP_MARKER_MISSING",
    "FIRST_SETUP_MARKER_DUPLICATED",
    "FIRST_SETUP_ACCOUNT_MISMATCH",
    "SHEET_UNREACHABLE",
    "FIRST_SETUP_INCOMPLETE",
    "SCRIPT_UPDATE_AVAILABLE",
    "SCRIPT_FINISHING_REQUIRED",
    "SCRIPT_CUSTOMIZED",
    "SCRIPT_UNREACHABLE",
    "AI_TRIGGER_MISSING",
    "AI_TRIGGER_DUPLICATED",
    "AI_TARGET_MISMATCH",
    "SHEET_COPY_OUT_OF_SYNC",
    "DOC_NOT_CONFIGURED",
    "DOC_NOT_FOUND",
    "DOC_ACCESS_DENIED",
    "DOC_URL_ID_MISMATCH",
    "SHEET_CONFIG_DOC_MISMATCH",
    "TASK_LIST_NOT_CONFIGURED",
    "TASK_LIST_NOT_FOUND",
    "TASK_LIST_ACCESS_DENIED",
    "TASK_LIST_CONTEXT_MISMATCH",
    "SHEET_CONFIG_TASK_MISMATCH",
    "SERVER_UNREACHABLE",
    "SHEET_NOT_REGISTERED",
    "SERVER_ACCOUNT_MISMATCH",
    "SERVER_SHEET_MISMATCH",
    "SPACE_NOT_SELECTED",
    "SPACE_NOT_FOUND",
    "SPACE_SERVER_SHEET_MISMATCH",
    "SPACE_LIST_UNREACHABLE",
    "PERSONAL_QUEUE_SHEET_MISSING",
    "CLASS_QUEUE_SHEET_MISSING",
    "QUEUE_SCHEMA_MISMATCH",
)
_ATTENDANCE_PRIORITY = {
    reason_code: index for index, reason_code in enumerate(ATTENDANCE_REASON_PRIORITY)
}


def primary_group_issue(
    items: Iterable[ConnectionStatus], group: str
) -> ConnectionStatus | None:
    issues = [
        (index, item)
        for index, item in enumerate(items)
        if item.group == group and item.connected is False
    ]
    if not issues:
        return None
    priorities = _ATTENDANCE_PRIORITY if group == "attendance" else {}
    return min(
        issues,
        key=lambda indexed_item: (
            priorities.get(indexed_item[1].reason_code, len(priorities)),
            indexed_item[0],
        ),
    )[1]
