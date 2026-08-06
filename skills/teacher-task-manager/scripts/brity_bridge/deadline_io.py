"""한 번 정한 전체 마감시간 안에서만 외부 응답을 읽는다.

소켓의 ``read``는 운영체제나 보안 프로그램 안에서 오래 멈출 수 있다. 읽기마다
새 제한 시간을 주면 한 조각씩 늦게 오는 응답이 설치 잠금을 끝없이 잡을 수도
있다. 실제 파일 쓰기는 호출한 쪽만 하게 두고, 기다리는 일만 보조 작업에 맡긴다.
마감 뒤 보조 작업이 늦게 끝나더라도 받은 글자는 파일에 쓰이지 않는다.
"""
from __future__ import annotations

import queue
import threading
import time


class TotalDeadlineExpired(TimeoutError):
    """처음 정한 전체 기다림 시간이 지났다."""


def read_before(source, size: int, deadline: float) -> bytes:
    """``source.read`` 한 번을 절대 시각 ``deadline``까지만 기다린다.

    Python의 일반 파일/인터넷 응답 읽기는 진행 중인 호출을 안전하게 끊을 방법이
    없다. 그래서 읽기만 daemon 작업에 맡기고, 결과를 파일에 쓸 권한은 호출한
    작업에만 둔다. 시간 안에 끝난 예외는 원래 예외 그대로 다시 올린다.
    """
    remaining = float(deadline) - time.monotonic()
    if remaining <= 0:
        raise TotalDeadlineExpired("download total deadline expired")

    result: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def read_once() -> None:
        try:
            result.put((True, source.read(size)))
        except BaseException as error:  # 호출한 쪽에서 기존 분류 규칙으로 바꾼다.
            result.put((False, error))

    reader = threading.Thread(
        target=read_once,
        name="TeacherManagerDownloadRead",
        daemon=True,
    )
    reader.start()
    try:
        succeeded, value = result.get(timeout=remaining)
    except queue.Empty as error:
        raise TotalDeadlineExpired("download total deadline expired") from error

    # 결과가 마감과 거의 동시에 도착해도 전체 제한을 넘겼으면 사용하지 않는다.
    if time.monotonic() > float(deadline):
        raise TotalDeadlineExpired("download total deadline expired")
    if not succeeded:
        raise value  # type: ignore[misc]
    return bytes(value)
