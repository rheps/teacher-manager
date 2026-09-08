"""Teacher Manager Google account identity parsing and address validation."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from typing import Any

GOEDU_ACCOUNT_REQUIRED_MESSAGE = (
    "이 계정으로는 진행할 수 없어요. Google 계정으로 다시 로그인해 주세요."
)
_LOCAL_PUNCTUATION = frozenset("!#$%&'*+-/=?^_`{|}~")
_LEGACY_EMAIL = re.compile(r"[^\s@]+@[^\s@]+")
_STRUCTURED_KEYS = frozenset({"user", "logged_in", "token_valid", "auth_method"})
_IDENTITY_KEYS = frozenset({"user", "account", "email"})


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _has_control(value: str) -> bool:
    return any(unicodedata.category(char) == "Cc" for char in value)


def _valid_local_part(value: str) -> bool:
    if not value or value.startswith(".") or value.endswith(".") or ".." in value:
        return False
    return all(char == "." or char.isalnum() or char in _LOCAL_PUNCTUATION for char in value)


def _valid_domain(value: str) -> bool:
    labels = value.split(".")
    if len(labels) < 2 or any(not label for label in labels):
        return False
    return all(
        not label.startswith("-")
        and not label.endswith("-")
        and all(char == "-" or char.isalnum() for char in label)
        for label in labels
    )


def is_goedu_email(value: object) -> bool:
    """Validate one address. The legacy name remains for caller compatibility."""
    if not isinstance(value, str) or _has_control(value):
        return False
    email = value.strip()
    if email.count("@") != 1:
        return False
    local, domain = email.split("@", 1)
    return _valid_local_part(local) and _valid_domain(domain)


def _auth_objects(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        found = [value] if _STRUCTURED_KEYS.intersection(value) else []
        for nested in value.values():
            found.extend(_auth_objects(nested))
        return found
    if isinstance(value, list):
        found = []
        for nested in value:
            found.extend(_auth_objects(nested))
        return found
    return []


def _structured_email(value: Any) -> str:
    objects = _auth_objects(value)
    if len(objects) != 1:
        return ""
    identities = [objects[0][key] for key in _IDENTITY_KEYS if key in objects[0]]
    if len(identities) != 1:
        return ""
    account = identities[0]
    if not isinstance(account, str) or not is_goedu_email(account):
        return ""
    return account.strip()


def _legacy_email(text: str) -> str:
    candidates: dict[str, str] = {}
    for match in _LEGACY_EMAIL.finditer(text):
        candidate = match.group(0)
        if is_goedu_email(candidate):
            candidates.setdefault(candidate.casefold(), candidate)
    return next(iter(candidates.values())) if len(candidates) == 1 else ""


def _container_end(text: str, start: int) -> int:
    """Skip a failed container as one unit; never salvage its nested identity."""
    depth = 0
    quoted = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
            if depth == 0:
                return index + 1
    return len(text)


def extract_email(text: object) -> str:
    """Read an exact structured identity, with a separate legacy-text fallback."""
    if not isinstance(text, str):
        return ""
    decoder = json.JSONDecoder(object_pairs_hook=_unique_object)
    decoded: list[tuple[int, int, Any]] = []
    malformed_auth_json = False
    start = 0
    while start < len(text):
        char = text[start]
        if char not in "[{":
            start += 1
            continue
        try:
            value, length = decoder.raw_decode(text[start:])
        except (TypeError, ValueError):
            end = _container_end(text, start)
            container = text[start:end]
            if any(f'"{key}"' in container for key in _STRUCTURED_KEYS):
                malformed_auth_json = True
            start = end
            continue
        decoded.append((start, start + length, value))
        start += length

    auth_values = [item for item in decoded if _auth_objects(item[2])]
    if malformed_auth_json or auth_values:
        if malformed_auth_json or len(auth_values) != 1:
            return ""
        start, end, value = auth_values[0]
        outside = text[:start] + text[end:]
        if any(is_goedu_email(match.group(0)) for match in _LEGACY_EMAIL.finditer(outside)):
            return ""
        return _structured_email(value)
    return _legacy_email(text)


def require_goedu_email(value: object) -> str:
    """Return the same address, apart from documented surrounding whitespace."""
    if not isinstance(value, str) or not is_goedu_email(value):
        raise RuntimeError(GOEDU_ACCOUNT_REQUIRED_MESSAGE)
    return value.strip()


__all__ = ["GOEDU_ACCOUNT_REQUIRED_MESSAGE", "extract_email", "is_goedu_email", "require_goedu_email"]
