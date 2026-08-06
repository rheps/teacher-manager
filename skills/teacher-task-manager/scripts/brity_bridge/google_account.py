"""Teacher Manager가 허용하는 경기도교육청 Google 계정 규칙."""

from __future__ import annotations

import re


GOEDU_ACCOUNT_REQUIRED_MESSAGE = (
    "이 계정으로는 진행할 수 없어요. 교육디지털원패스 및 경기도교육청 "
    "클라우드 지원시스템 계정으로 다시 로그인해 주세요. (@goedu.kr)"
)
GOEDU_STUDENT_REQUIRED_MESSAGE = (
    "학생 Google Chat 계정은 @goedu.kr 주소여야 해요. "
    "학생의 교육디지털원패스·경기도교육용 클라우드·Google Workspace 가입을 확인해 주세요."
)

_GOEDU_EMAIL = re.compile(r"^[^@\s]+@goedu\.kr$", re.IGNORECASE)
_ANY_EMAIL = re.compile(
    r"([^@\s\"'<>,;:()\[\]{}]+@[^@\s\"'<>,;:()\[\]{}]+)",
    re.IGNORECASE,
)


def is_goedu_email(value: object) -> bool:
    """공백이나 위장 주소 없이 정확한 ``이름@goedu.kr``인지 확인한다."""

    return bool(_GOEDU_EMAIL.fullmatch(str(value or "").strip()))


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
