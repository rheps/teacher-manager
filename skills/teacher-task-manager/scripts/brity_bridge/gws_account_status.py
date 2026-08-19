"""GWS 로그인 상태 출력에서 현재 계정 하나만 안전하게 확인한다."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any


EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])[\w.!#$%&'*+/=?^`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?"
    r"[.][A-Za-z]{2,}(?![\w.-])"
)

RunCommand = Callable[[Sequence[str]], tuple[int, str]]


def _need(condition: Any, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _need(key not in result, "Google 로그인 상태 JSON에 같은 항목이 두 번 있습니다.")
        result[key] = value
    return result


def _bad_json_number(value: str):
    raise ValueError(f"Google 로그인 상태 JSON의 숫자 모양이 올바르지 않습니다: {value}")


def _json_values_in_text(output: str) -> list[Any]:
    """안내 문장이 앞뒤에 섞여도 완전한 JSON 부분만 차례로 읽는다."""

    decoder = json.JSONDecoder(
        object_pairs_hook=_strict_pairs,
        parse_constant=_bad_json_number,
    )
    values: list[Any] = []
    cursor = 0
    while cursor < len(output):
        starts = [
            index
            for index in (output.find("{", cursor), output.find("[", cursor))
            if index >= 0
        ]
        if not starts:
            break
        start = min(starts)
        try:
            value, end = decoder.raw_decode(output[start:])
        except ValueError:
            cursor = start + 1
            continue
        values.append(value)
        cursor = start + end
    return values


def _auth_status_objects(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        if "logged_in" in value or "user" in value:
            return [value]
        found: list[Mapping[str, Any]] = []
        for nested in value.values():
            found.extend(_auth_status_objects(nested))
        return found
    if isinstance(value, list):
        found = []
        for nested in value:
            found.extend(_auth_status_objects(nested))
        return found
    return []


def _strict_email(value: Any, label: str) -> str:
    _need(isinstance(value, str), f"{label}이 글자가 아닙니다.")
    clean = value.strip()
    _need(clean == value, f"{label} 앞뒤에 공백이 있습니다.")
    matches = list(EMAIL_PATTERN.finditer(clean))
    _need(
        len(matches) == 1 and matches[0].group(0) == clean,
        f"{label}의 이메일 모양이 올바르지 않습니다.",
    )
    return clean


def _legacy_output_confirms_login(output: str, account: str) -> bool:
    """JSON이 없던 예전 GWS의 분명한 로그인 완료 문구만 인정한다."""

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    lowered = output.casefold()
    negative_markers = (
        "logged out",
        "signed out",
        "not logged in",
        "not signed in",
        "not authenticated",
        "no credentials",
        "login required",
        "authentication required",
    )
    if any(marker in lowered for marker in negative_markers):
        return False

    account_key = account.casefold()

    def line_parts(line: str) -> tuple[str, str] | None:
        matches = list(EMAIL_PATTERN.finditer(line))
        if len(matches) != 1:
            return None
        match = matches[0]
        if line[match.end():].strip():
            return None
        return (
            line[:match.start()].strip().casefold().rstrip(":").strip(),
            match.group(0).casefold(),
        )

    for line in lines:
        parts = line_parts(line)
        if parts and parts[0] in {"signed in as", "logged in as"}:
            return parts[1] == account_key

    has_logged_in_line = any(line.casefold() == "logged in" for line in lines)
    has_matching_user_line = any(
        parts is not None and parts[0] == "user" and parts[1] == account_key
        for parts in (line_parts(line) for line in lines)
    )
    return has_logged_in_line and has_matching_user_line


def current_gws_account(run_command: RunCommand, gws_executable: str) -> str:
    """`gws auth status`를 한 번 읽고 로그인된 계정 하나만 돌려준다."""

    try:
        reply = run_command([gws_executable, "auth", "status"])
    except Exception as error:
        raise ValueError("현재 Google 계정 확인 요청이 실패했습니다.") from error
    _need(
        isinstance(reply, tuple) and len(reply) == 2,
        "현재 Google 계정 확인 결과의 모양이 올바르지 않습니다.",
    )
    code, output = reply
    _need(
        isinstance(code, int) and not isinstance(code, bool) and code == 0,
        "현재 Google 계정 확인 요청이 성공하지 않았습니다.",
    )
    _need(isinstance(output, str), "현재 Google 계정 확인 결과가 글자가 아닙니다.")

    found: dict[str, str] = {}
    for match in EMAIL_PATTERN.finditer(output):
        email = match.group(0)
        found.setdefault(email.casefold(), email)
    _need(len(found) == 1, "현재 Google 계정을 하나로 분명하게 확인하지 못했습니다.")
    raw_account = next(iter(found.values()))

    status_objects: list[Mapping[str, Any]] = []
    for value in _json_values_in_text(output):
        status_objects.extend(_auth_status_objects(value))
    _need(
        len(status_objects) <= 1,
        "현재 Google 로그인 상태를 나타내는 JSON 객체가 여러 개입니다.",
    )
    if not status_objects:
        _need(
            _legacy_output_confirms_login(output, raw_account),
            "현재 Google 로그인 완료를 확인하는 문구가 없습니다.",
        )
        return raw_account

    status = status_objects[0]
    _need("user" in status, "현재 Google 로그인 상태 JSON에 계정 항목이 없습니다.")
    signals = 0
    if "logged_in" in status:
        _need(
            status["logged_in"] is True,
            "현재 Google 로그인 완료 여부를 참으로 확인하지 못했습니다.",
        )
        signals += 1
    if "token_valid" in status:
        _need(
            status["token_valid"] is True,
            "현재 Google 로그인 토큰이 유효하지 않습니다.",
        )
        signals += 1
    if "auth_method" in status:
        method = status["auth_method"]
        _need(
            isinstance(method, str) and method.strip() not in {"", "none"},
            "현재 Google 로그인 방식을 확인하지 못했습니다.",
        )
        signals += 1
    _need(signals > 0, "현재 Google 로그인 완료를 확인할 항목이 하나도 없습니다.")

    status_account = _strict_email(status["user"], "현재 Google 로그인 상태의 계정")
    _need(
        status_account.casefold() == raw_account.casefold(),
        "현재 Google 로그인 상태의 계정과 출력 속 계정이 다릅니다.",
    )
    return raw_account


__all__ = ["current_gws_account"]
