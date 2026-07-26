"""시트에 붙어 있는 Apps Script가 쓸 수 있을 만큼 새 판인지 본다.

release.json의 `minimumAppsScriptVersion`이 여기서 쓰인다. 쓰던 시트에 다시
연결할 때, 그 시트의 스크립트가 이 값보다 오래된 판이면 멈춘다. 새 프로그램이
옛 모양 시트를 그대로 몰고 가면 자료가 어긋난다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


APP_VERSION_PATTERN = re.compile(r"""const\s+APP_VERSION\s*=\s*['"]([^'"]+)['"]""")
VERSION_NUMBER_PATTERN = re.compile(r"^\d+(?:\.\d+)*$")
RELEASE_FILE_NAME = "release.json"
MINIMUM_KEY = "minimumAppsScriptVersion"


def app_version_in_source(source: Any) -> str | None:
    """스크립트 원본에 적힌 `const APP_VERSION` 값을 읽는다. 없으면 None."""

    if not isinstance(source, str):
        return None
    match = APP_VERSION_PATTERN.search(source)
    if not match:
        return None
    found = match.group(1).strip()
    return found or None


def _numbers(value: Any) -> tuple[int, ...] | None:
    if not isinstance(value, str):
        return None
    clean = value.strip()
    if not VERSION_NUMBER_PATTERN.fullmatch(clean):
        return None
    return tuple(int(part) for part in clean.split("."))


def is_at_least(found: Any, minimum: Any) -> bool:
    """`found`가 `minimum` 이상인지 숫자로 비교한다.

    글자로 비교하면 '5.10.0'이 '5.9.0'보다 작아진다. 읽을 수 없는 값은
    통과시키지 않는다 — 모르는 판은 오래된 판으로 본다.
    """

    left = _numbers(found)
    right = _numbers(minimum)
    if left is None or right is None:
        return False
    width = max(len(left), len(right))
    left = left + (0,) * (width - len(left))
    right = right + (0,) * (width - len(right))
    return left >= right


def minimum_apps_script_version(bundle_root: Path) -> str:
    """동봉된 release.json에서 하한선을 읽는다. 없으면 멈춘다."""

    path = Path(bundle_root) / RELEASE_FILE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path} 에서 배포 정보를 읽지 못했습니다.") from exc
    value = data.get(MINIMUM_KEY) if isinstance(data, dict) else None
    if _numbers(value) is None:
        raise ValueError(
            f"{path} 의 {MINIMUM_KEY} 값이 5.8.1 같은 숫자 판 번호가 아닙니다."
        )
    return str(value).strip()


__all__ = [
    "app_version_in_source",
    "is_at_least",
    "minimum_apps_script_version",
]
