"""Teacher Manager Google 계정 주소 형식 검사. 계정 인증은 Google 로그인으로 확인한다."""

from __future__ import annotations

import re


GOEDU_ACCOUNT_REQUIRED_MESSAGE = (
    "이 계정으로는 진행할 수 없어요. Google "
    "계정으로 다시 로그인해 주세요."
)

_GOOGLE_EMAIL = re.compile(
    r'^[^@\s<>,;:"()[\]{}\\/]+@[^@\s<>,;:"()[\]{}\\/.]+(?:\.[^@\s<>,;:"()[\]{}\\/.]+)+$', re.IGNORECASE
)
_ANY_EMAIL = re.compile(
    r"([^@\s\"'<>,;:()\[\]{}]+@[^@\s\"'<>,;:()\[\]{}]+)",
    re.IGNORECASE,
)


def is_goedu_email(value: object) -> bool:
    """주소 한 개의 형식만 확인한다. Google 계정 여부는 로그인 결과로 확인하며 기존 이름은 호환용이다."""

    return bool(_GOOGLE_EMAIL.fullmatch(str(value or "").strip()))


def extract_email(text: object) -> str:
    """GWS 로그인 상태 문장에서 이메일 한 개만 안전하게 꺼낸다.

    결과에 주소가 여러 개 섞였으면 어느 계정인지 짐작하지 않고 빈 값으로 둔다.
    """

    source = str(text or "")
    matches = []
    for match in _ANY_EMAIL.finditer(source):
        candidate = match.group(1).strip().rstrip(".,;)]}")
        if candidate and candidate not in matches:
            matches.append(candidate)
    return matches[0] if len(matches) == 1 else ""


def require_goedu_email(value: object) -> str:
    """허용 계정이면 정리한 주소를 돌려주고, 아니면 쉬운 안내로 멈춘다."""

    email = str(value or "").strip()
    if not is_goedu_email(email):
        raise RuntimeError(GOEDU_ACCOUNT_REQUIRED_MESSAGE)
    return email
