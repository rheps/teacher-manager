"""배경에서 필요한 인터넷 이름을 미리 찾아 Windows의 이름 캐시를 따뜻하게 둔다.

학교 노트북은 유선 LAN용 고정 IP 설정을 그대로 두고 Wi-Fi로 옮겨 다닌다. 그 유선
어댑터가 연결됐다고 보이는데 자기 DNS 서버에는 닿지 못하면, Windows는 이름마다 유선
쪽에 먼저 묻고 약 10초를 기다린 뒤에야 Wi-Fi 쪽에 묻는다(2026-09-05 측정 11.1초).
한 번 찾은 이름은 5분(TTL) 동안 캐시에 남아 0초에 끝난다. 프로그램은 컴퓨터의
네트워크 설정을 바꾸지 않는다(사용자 결정: 고정 IP·유선 우선은 Brity 메신저에
필요하다). 대신 필요한 이름을 미리 병렬로 찾아 두고 5분이 지나기 전에 다시 찾아
gws와 자체 내려받기가 늘 캐시된 이름을 만나게 한다.
"""
from __future__ import annotations

import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import urlsplit

WARM_HOSTS: tuple[str, ...] = (
    # Google 로그인·API (gws)
    "accounts.google.com",
    "oauth2.googleapis.com",
    "www.googleapis.com",
    "sheets.googleapis.com",
    "script.googleapis.com",
    "drive.googleapis.com",
    "docs.googleapis.com",
    "tasks.googleapis.com",
    "calendar.googleapis.com",
    "chat.googleapis.com",
    "people.googleapis.com",
    "generativelanguage.googleapis.com",
    # 프로그램 자체 내려받기 (새 판 확인, gws 갱신, 승인된 스킬)
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "codeload.github.com",
    # AI 에이전트용 Node
    "nodejs.org",
)
REFRESH_SECONDS = 240.0  # Windows 이름 캐시 TTL 300초보다 먼저 다시 찾는다
SLOW_SECONDS = 3.0  # 첫 확인이 이보다 오래 걸리면 '느린 컴퓨터'로 적는다
DEFAULT_WAIT_SECONDS = 15.0  # gws를 부르기 전에 첫 확인을 기다리는 최대 시간(느린 첫 확인은 약 12초, 2026-09-05 측정)
_MAX_WORKERS = 32

Resolver = Callable[[str, int], object]


@dataclass(frozen=True)
class WarmResult:
    durations: dict[str, float]
    failures: tuple[str, ...]

    @property
    def slowest_seconds(self) -> float:
        return max(self.durations.values(), default=0.0)


def _default_resolver(host: str, port: int):
    return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)


def hosts_from_urls(*urls: str) -> tuple[str, ...]:
    """주소 문자열에서 호스트 이름만 고른다. 비었거나 이상한 값은 조용히 넘긴다."""

    hosts = []
    for url in urls:
        try:
            host = urlsplit(str(url or "").strip()).hostname
        except ValueError:
            host = None
        if host and host not in hosts:
            hosts.append(host)
    return tuple(hosts)


def warm_once(
    hosts: Iterable[str] = WARM_HOSTS,
    *,
    resolver: Resolver | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> WarmResult:
    """모든 이름을 동시에 찾고 각각 걸린 시간을 적는다. 실패는 기록만 하고 예외를 내지 않는다."""

    names = tuple(dict.fromkeys(str(host) for host in hosts if host))
    lookup = resolver or _default_resolver
    if not names:
        return WarmResult({}, ())

    def one(host: str) -> tuple[str, float, bool]:
        started = clock()
        try:
            lookup(host, 443)
            ok = True
        except Exception:  # noqa: BLE001 - 이름 하나 실패가 나머지를 막지 않는다
            ok = False
        return host, clock() - started, ok

    durations: dict[str, float] = {}
    failures: list[str] = []
    # 이름마다 최대 10여 초씩 막히는 자리라 이름 수만큼 스레드를 써서 한 번에 끝낸다.
    with ThreadPoolExecutor(max_workers=min(len(names), _MAX_WORKERS), thread_name_prefix="dns-warm") as pool:
        for host, seconds, ok in pool.map(one, names):
            durations[host] = seconds
            if not ok:
                failures.append(host)
    return WarmResult(durations, tuple(failures))


class DnsWarmer:
    """첫 확인을 기다릴 수 있고, 이후에는 캐시가 식기 전에 스스로 다시 찾는다."""

    def __init__(
        self,
        hosts: Iterable[str] = WARM_HOSTS,
        *,
        resolver: Resolver | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        refresh_seconds: float = REFRESH_SECONDS,
    ) -> None:
        self.hosts = tuple(dict.fromkeys(str(host) for host in hosts if host))
        self._resolver = resolver
        self._sleeper = sleeper
        self._clock = clock
        self._refresh_seconds = refresh_seconds
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._started_at: float | None = None
        self._first_lookup_seconds: float | None = None
        self._passes = 0
        self._last: WarmResult | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._started_at = self._clock()
            self._thread = threading.Thread(target=self.run_forever, name="dns-warm", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                self.warm()
            except Exception:  # noqa: BLE001 - 예열 실패가 프로그램을 멈추면 안 된다
                self._ready.set()
            self._sleeper(self._refresh_seconds)

    def warm(self) -> WarmResult:
        with self._lock:
            if self._started_at is None:
                self._started_at = self._clock()
        pass_started = self._clock()
        result = warm_once(self.hosts, resolver=self._resolver, clock=self._clock)
        with self._lock:
            self._passes += 1
            self._last = result
            if self._first_lookup_seconds is None:
                self._first_lookup_seconds = self._clock() - pass_started
        self._ready.set()
        return result

    def wait_ready(self, max_seconds: float) -> bool:
        """첫 확인이 끝났으면 True. 너무 오래 걸리면 기다림을 멈추고 False."""

        return self._ready.wait(max_seconds)

    def status(self) -> dict:
        with self._lock:
            started = self._started_at
            first = self._first_lookup_seconds
            passes = self._passes
            last = self._last
        if started is None:
            state, elapsed = "off", 0.0
        elif first is None:
            state, elapsed = "warming", max(0.0, self._clock() - started)
        else:
            state, elapsed = ("slow" if first >= SLOW_SECONDS else "ok"), first
        return {
            "state": state,
            "first_lookup_seconds": first,
            "elapsed_seconds": elapsed,
            "passes": passes,
            "failures": list(last.failures) if last else [],
        }


_shared: DnsWarmer | None = None
_shared_lock = threading.Lock()


def start_shared(*, extra_hosts: Iterable[str] = (), **kwargs) -> DnsWarmer:
    """프로세스에 하나만 둔다. 이미 돌고 있으면 그것을 돌려준다."""

    global _shared
    with _shared_lock:
        if _shared is None:
            warmer = DnsWarmer(tuple(WARM_HOSTS) + tuple(extra_hosts), **kwargs)
            warmer.start()
            _shared = warmer
        return _shared


def wait_shared_ready(max_seconds: float = DEFAULT_WAIT_SECONDS) -> bool:
    """예열이 없으면(테스트·소스 실행) 기다리지 않는다."""

    warmer = _shared
    if warmer is None:
        return True
    return warmer.wait_ready(max_seconds)


def shared_status() -> dict:
    warmer = _shared
    if warmer is None:
        return {"state": "off", "first_lookup_seconds": None, "elapsed_seconds": 0.0, "passes": 0, "failures": []}
    return warmer.status()
