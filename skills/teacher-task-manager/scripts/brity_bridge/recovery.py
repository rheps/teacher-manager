"""Shared, context-aware recovery records and retry handling.

This module deliberately has no dashboard dependency.  Callers provide the
application version so the common contract can be used by every bridge stage.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone, timedelta
import re
import time
from typing import Any, Callable, Mapping, TypeVar
import uuid


LOCAL_DELAYS = (0.0, 0.5, 1.5)
NETWORK_DELAYS = (0.0, 2.0, 5.0)
SUPPORT_EMAIL = "contact@big-silver.xyz"
SUPPORT_WEB = "https://big-silver.xyz"
KOREA_TIME = timezone(timedelta(hours=9))

T = TypeVar("T")


@dataclass(frozen=True)
class IssueAction:
    key: str
    label: str


@dataclass(frozen=True)
class UserIssue:
    state: str
    operation: str
    title: str
    message: str
    change_status: str
    actions: tuple[IssueAction, ...]
    resume: str = ""
    attempt_count: int = 0
    last_failed_at: str = ""
    reason: str = ""
    app_version: str = ""
    diagnostic_id: str = ""
    # 교사가 지금 할 수 있는 일. 화면은 이 문장들만 행동 안내로 그린다.
    steps: tuple[str, ...] = ()
    # 개발자에게 자동 보고 대기열에 들어갔는지. 화면의 "보고됐어요" 한 줄 근거.
    reported: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "actions": [asdict(row) for row in self.actions],
            "steps": list(self.steps),
        }

    def with_guidance(
        self,
        *,
        steps: tuple[str, ...],
        actions: tuple[IssueAction, ...] | None = None,
        reason: str | None = None,
    ) -> "UserIssue":
        return replace(
            self,
            steps=tuple(str(step) for step in steps if str(step).strip()),
            actions=self.actions if actions is None else tuple(actions),
            reason=self.reason if reason is None else str(reason),
        )

    def mark_reported(self) -> "UserIssue":
        return replace(self, reported=True)

    @classmethod
    def needs_user(
        cls,
        *,
        operation: str,
        title: str,
        message: str,
        change_status: str,
        actions: tuple[IssueAction, ...],
        resume: str = "",
    ) -> "UserIssue":
        return cls(
            state="needs_user",
            operation=operation,
            title=title,
            message=message,
            change_status=change_status,
            actions=tuple(actions),
            resume=resume,
        )


class UserActionRequired(Exception):
    """Raised when the teacher must take an explicit, observable action."""

    def __init__(self, issue: UserIssue):
        if not isinstance(issue, UserIssue):
            raise TypeError("issue must be a UserIssue")
        self.issue = issue
        super().__init__(issue.message)


class RetryableOperationError(Exception):
    """A known, safe-to-report failure that may be retried or verified."""

    def __init__(self, code: str, safe_reason: str):
        if not isinstance(code, str) or not code.strip():
            raise ValueError("code must not be empty")
        if not isinstance(safe_reason, str) or not safe_reason.strip():
            raise ValueError("safe_reason must not be empty")
        self.code = code.strip()
        self.safe_reason = safe_reason.strip()
        super().__init__(self.safe_reason)


class FinalOperationFailure(Exception):
    """Raised after the shared runner exhausts its three operation cycles."""

    def __init__(self, issue: UserIssue):
        if not isinstance(issue, UserIssue):
            raise TypeError("issue must be a UserIssue")
        self.issue = issue
        super().__init__(issue.message)


# 분류되지 않은 결함도 교사에게는 쉬운 말로만 보인다. 개발자 표현을 화면에 쓰지 않는다.
UNEXPECTED_MESSAGE = "프로그램 안에서 처리하지 못한 문제가 있었어요."


def _required_text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value.strip()


def _timestamp(now: Callable[[], datetime] | datetime | None) -> str:
    value = now() if callable(now) else now
    if value is None:
        value = datetime.now(KOREA_TIME)
    if not isinstance(value, datetime):
        raise TypeError("now must return a datetime")
    if value.tzinfo is None:
        value = value.replace(tzinfo=KOREA_TIME)
    return value.astimezone(KOREA_TIME).strftime("%Y-%m-%d %H:%M:%S")


def final_issue(
    operation: str,
    title: str,
    attempts: int,
    reason: str,
    change_status: str,
    app_version: str,
    now: Callable[[], datetime] | datetime | None = None,
    steps: tuple[str, ...] = (),
) -> UserIssue:
    """Build a safe, user-facing final failure record."""

    operation = _required_text("operation", operation)
    title = _required_text("title", title)
    reason = _required_text("reason", reason)
    change_status = _required_text("change_status", change_status)
    if not isinstance(attempts, int) or attempts < 1:
        raise ValueError("attempts must be a positive integer")
    if not isinstance(app_version, str):
        raise ValueError("app_version must be text")
    return UserIssue(
        state="failed",
        operation=operation,
        title=title,
        message=f"총 {attempts}회 시도했지만 끝내지 못했습니다.",
        change_status=change_status,
        actions=(),
        attempt_count=attempts,
        last_failed_at=_timestamp(now),
        reason=reason,
        app_version=app_version,
        diagnostic_id=str(uuid.uuid4()),
        steps=tuple(steps),
    )


def unexpected_final_issue(
    operation: str,
    title: str,
    change_status: str,
    app_version: str,
    now: Callable[[], datetime] | datetime | None = None,
    steps: tuple[str, ...] = (),
) -> UserIssue:
    """Build a failure for an unclassified developer defect.

    No operation cycles were completed by this helper, so it intentionally
    reports zero attempts instead of borrowing the normal three-attempt claim.
    The attempt count stays in the record for the local journal and the
    developer report only; the screen never shows it.
    """

    operation = _required_text("operation", operation)
    title = _required_text("title", title)
    change_status = _required_text("change_status", change_status)
    return UserIssue(
        state="failed",
        operation=operation,
        title=title,
        message=UNEXPECTED_MESSAGE,
        change_status=change_status,
        actions=(),
        attempt_count=0,
        last_failed_at=_timestamp(now),
        reason=UNEXPECTED_MESSAGE,
        app_version=app_version,
        diagnostic_id=str(uuid.uuid4()),
        steps=tuple(steps),
    )


def run_operation(
    operation: str,
    title: str,
    action: Callable[[], T],
    *,
    verify: Callable[[], tuple[bool, T]] | None = None,
    delays,
    change_status: str,
    app_version: str,
    now: Callable[[], datetime] | datetime | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> T:
    """Run a known operation over exactly three cycles.

    For an ambiguous write, each later cycle first performs read-back.  A
    read-back error means completion cannot be proven, so that cycle ends
    without issuing another write request.
    """

    try:
        delays = tuple(delays)
    except TypeError as exc:
        raise ValueError("delays must contain exactly three entries") from exc
    if len(delays) != 3:
        raise ValueError("delays must contain exactly three entries")
    last: RetryableOperationError | None = None
    for attempt_number, delay in enumerate(delays, start=1):
        if delay:
            sleeper(delay)
        if attempt_number > 1 and verify is not None:
            try:
                complete, value = verify()
            except UserActionRequired:
                raise
            except RetryableOperationError as error:
                last = error
                continue
            if complete:
                return value
        try:
            return action()
        except UserActionRequired:
            raise
        except RetryableOperationError as error:
            last = error
    # 마지막 재시도 예외를 원인으로 달아 두면 로컬 오류 기록이 어떤 확인이 어떻게
    # 어긋났는지 사슬을 따라 남길 수 있다. 화면 issue 자체에는 들어가지 않는다.
    raise FinalOperationFailure(
        final_issue(
            operation=operation,
            title=title,
            attempts=len(delays),
            reason=last.safe_reason if last else "확인된 원인이 없습니다.",
            change_status=change_status,
            app_version=app_version,
            now=now,
        )
    ) from last


def _issue_value(issue: UserIssue | Mapping[str, Any], key: str, default: Any = "") -> Any:
    if isinstance(issue, UserIssue):
        return getattr(issue, key, default)
    if isinstance(issue, Mapping):
        return issue.get(key, default)
    raise TypeError("issue must be a UserIssue or mapping")


def _safe_operation_id(value: Any) -> str:
    if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]{1,63}", value):
        return value
    return "unknown_operation"


def _safe_attempt_count(value: Any) -> int:
    if isinstance(value, int) and 0 <= value <= 3:
        return value
    return 0


def _safe_timestamp(value: Any) -> str:
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", value):
        return value
    return "알 수 없음"


def _safe_app_version(value: Any) -> str:
    if isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+){1,3}", value):
        return value
    return "알 수 없음"


def _safe_diagnostic_id(value: Any) -> str:
    if isinstance(value, str) and re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        value,
    ):
        return value
    return "알 수 없음"


def support_report_text(issue: UserIssue | Mapping[str, Any]) -> str:
    """Render safe fixed metadata, never caller-supplied display text."""

    report = [
        "Teacher Manager 오류 정보",
        f"작업: {_safe_operation_id(_issue_value(issue, 'operation'))}",
        f"시도 횟수: {_safe_attempt_count(_issue_value(issue, 'attempt_count'))}",
        f"마지막 시도 시각: {_safe_timestamp(_issue_value(issue, 'last_failed_at'))}",
        f"Teacher Manager 판 번호: {_safe_app_version(_issue_value(issue, 'app_version'))}",
        f"오류 식별번호: {_safe_diagnostic_id(_issue_value(issue, 'diagnostic_id'))}",
    ]
    report.extend(
        (
            f"메일 문의: {SUPPORT_EMAIL}",
            f"웹사이트 문의: {SUPPORT_WEB}",
        )
    )
    return "\n".join(report)
