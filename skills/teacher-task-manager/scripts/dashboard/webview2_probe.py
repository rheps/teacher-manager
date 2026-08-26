"""설치된 프로그램이 WebView2로 실제 왕복 동작하는지 조용히 확인한다.

레지스트리에 적힌 판 번호나 설치 명령의 종료값만 믿지 않는다. 인터넷을 열지 않는
1×1 숨김 창에서 Python→JavaScript와 JavaScript→Python 왕복을 끝낸 뒤, 그 창이
만든 작업이 종료된 것을 확인하고 이 검사 전용 임시 폴더만 정리한다.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Sequence


PROBE_DIR_PREFIX = "TeacherManager-WebView2-Probe-"
PROBE_MARKER_NAME = "owned-probe.json"
PROBE_ATTEMPT_TIMEOUT_SECONDS = 90.0
PROBE_OVERALL_TIMEOUT_SECONDS = 240.0
PROBE_RETRY_COUNT = 2
_NONCE_RE = re.compile(r"^[0-9a-f]{32,64}$")
_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){3}$")
_LOCAL_HTML = "<!doctype html><meta charset='utf-8'><title>Teacher Manager WebView2 check</title>"
_PUBLIC_RENDERERS = {"edgechromium", "mshtml"}
_ERROR_CODE_RE = re.compile(r"^[A-Z0-9_]{0,64}$")


@dataclass(frozen=True)
class WebView2ProbeResult:
    ok: bool
    renderer: str
    selected_version: str
    stage: Literal[
        "version", "renderer", "loaded", "python_to_js",
        "js_to_python", "shutdown", "complete"
    ]
    error_code: str
    attempt: int

    def public_dict(self) -> dict:
        return {
            "ok": self.ok,
            "renderer": self.renderer,
            "selectedVersion": self.selected_version,
            "stage": self.stage,
            "errorCode": self.error_code,
            "attempt": self.attempt,
        }


class _ProbeApi:
    def __init__(self, expected_nonce: str):
        self.expected_nonce = expected_nonce

    def ping(self, nonce: str) -> str:
        return self.expected_nonce if nonce == self.expected_nonce else ""


def make_probe_nonce() -> str:
    return uuid.uuid4().hex


def _version_tuple(value: str) -> tuple[int, int, int, int] | None:
    if not isinstance(value, str) or not _VERSION_RE.fullmatch(value):
        return None
    try:
        parts = tuple(int(part) for part in value.split("."))
    except ValueError:
        return None
    if any(part < 0 or part > 2_147_483_647 for part in parts):
        return None
    return parts  # type: ignore[return-value]


def _loader_architecture() -> str:
    machine = platform.machine().lower()
    if "arm" in machine and ctypes.sizeof(ctypes.c_void_p) == 8:
        return "win-arm64"
    return "win-x64" if ctypes.sizeof(ctypes.c_void_p) == 8 else "win-x86"


def get_stable_webview2_version(webview_module) -> str:
    """WebView2 Loader가 Stable 채널에서 실제 선택하는 판 번호를 읽는다."""
    if os.name != "nt":
        return ""
    try:
        package_root = Path(webview_module.__file__).resolve().parent
        loader_path = (
            package_root / "lib" / "runtimes" / _loader_architecture()
            / "native" / "WebView2Loader.dll"
        )
        loader = ctypes.WinDLL(str(loader_path))
        function = loader.GetAvailableCoreWebView2BrowserVersionString
        function.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_wchar_p)]
        function.restype = ctypes.c_long
        selected = ctypes.c_wchar_p()
        result = int(function(None, ctypes.byref(selected)))
        if result != 0 or not selected.value:
            return ""
        value = str(selected.value)
        try:
            ctypes.windll.ole32.CoTaskMemFree(ctypes.cast(selected, ctypes.c_void_p))
        except Exception:
            pass
        return value if _version_tuple(value) is not None else ""
    except Exception:
        return ""


def _browser_process_id(window) -> int:
    """pywebview가 만든 WebView2 대표 작업 번호를 읽되 밖으로 노출하지 않는다."""
    try:
        direct = int(getattr(window, "browser_process_id", 0))
        if direct > 0:
            return direct
    except Exception:
        pass
    try:
        browser_view = window.gui.BrowserView.instances[window.uid]
        control = browser_view.browser.webview
        # WebView2의 CoreWebView2 값은 창을 다루는 Windows 쪽 실에서만 읽을 수 있다.
        from System import Func, Int32

        process_id = control.Invoke(
            Func[Int32](lambda: Int32(control.CoreWebView2.BrowserProcessId))
        )
        return int(process_id)
    except Exception:
        return 0


def _wait_for_process_exit(process_id: int, timeout_seconds: float) -> bool:
    if os.name != "nt" or process_id <= 0:
        return False
    synchronize = 0x00100000
    wait_object_0 = 0
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(synchronize, False, process_id)
        if not handle:
            # 87은 해당 PID가 더는 없다는 뜻이다. 권한 거부처럼 다른 이유로
            # 열지 못한 경우를 "끝났다"고 짐작하면 안 된다.
            return int(kernel32.GetLastError()) == 87
        try:
            milliseconds = max(0, min(int(timeout_seconds * 1000), 0xFFFFFFFE))
            return int(kernel32.WaitForSingleObject(handle, milliseconds)) == wait_object_0
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def _is_junction(path: Path) -> bool:
    checker = getattr(os.path, "isjunction", None)
    try:
        return bool(checker and checker(path))
    except OSError:
        return True


def _is_reparse_point(path: Path) -> bool:
    """Windows가 다른 곳으로 돌려보내는 모든 폴더 표식을 보수적으로 거부한다."""
    try:
        details = os.lstat(path)
        return bool(int(getattr(details, "st_file_attributes", 0)) & 0x400)
    except OSError:
        return True


def _cleanup_owned_probe_dir(candidate: Path, temp_root: Path, nonce: str) -> bool:
    """이번 검사가 직접 만든 정확한 한 폴더만 지운다."""
    try:
        root = temp_root.resolve(strict=True)
        # resolve()는 junction을 따라간다. 그러면 검사 폴더가 아니라 연결 대상에
        # rmtree가 닿을 수 있으므로, 원래 이름을 유지한 채 먼저 검사한다.
        path = Path(os.path.abspath(os.fspath(candidate)))
        if path.parent != root or not path.name.startswith(PROBE_DIR_PREFIX):
            return False
        # WebView2는 창과 모든 자식 작업이 끝난 뒤에도 Windows의 검사 프로그램이
        # 새 파일을 잠깐 확인할 수 있다. 최대 30초 동안 이 검사 전용 폴더만 다시
        # 확인하며 기다린다.
        for attempt in range(300):
            if not os.path.lexists(path):
                # private_mode인 pywebview가 종료하면서 먼저 안전하게 지웠다.
                return True
            # WebView2가 파일 손잡이를 잠깐 늦게 놓을 수 있다. 다시 지우기 전에도
            # 매번 연결 폴더와 소유 표시를 재확인해 다른 위치를 따라가지 않는다.
            if path.is_symlink() or _is_junction(path) or _is_reparse_point(path):
                return False
            marker = path / PROBE_MARKER_NAME
            if (
                marker.is_symlink()
                or _is_junction(marker)
                or _is_reparse_point(marker)
                or not marker.is_file()
            ):
                return False
            raw = marker.read_bytes()
            if len(raw) > 256:
                return False
            data = json.loads(raw.decode("utf-8"))
            if data != {"nonce": nonce}:
                return False
            try:
                shutil.rmtree(path)
            except OSError:
                if attempt == 299:
                    return False
                time.sleep(0.1)
                continue
            if not os.path.lexists(path):
                return True
            if attempt == 299:
                return False
            time.sleep(0.1)
        return False
    except Exception:
        return False


def _safe_destroy(window) -> None:
    try:
        window.destroy()
    except Exception:
        pass


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _public_renderer(value: object) -> str:
    renderer = str(value or "")
    return renderer if renderer in _PUBLIC_RENDERERS else ""


def _run_probe_attempt(
    *,
    webview_module,
    selected_version: str,
    attempt: int,
    attempt_timeout_seconds: float,
    temp_root: Path,
    nonce: str,
    probe_dir: Path | None = None,
    parent_manages_shutdown: bool = False,
) -> WebView2ProbeResult:
    if probe_dir is None:
        probe_dir = Path(tempfile.mkdtemp(prefix=PROBE_DIR_PREFIX, dir=temp_root))
        marker = probe_dir / PROBE_MARKER_NAME
        marker.write_text(json.dumps({"nonce": nonce}, separators=(",", ":")), encoding="utf-8")
    else:
        probe_dir = Path(os.path.abspath(os.fspath(probe_dir)))
        marker = probe_dir / PROBE_MARKER_NAME
        try:
            raw = marker.read_bytes()
            marker_data = json.loads(raw.decode("utf-8"))
        except Exception as error:
            raise ValueError("invalid owned probe folder") from error
        if (
            probe_dir.parent != temp_root
            or not probe_dir.name.startswith(PROBE_DIR_PREFIX)
            or probe_dir.is_symlink()
            or _is_junction(probe_dir)
            or _is_reparse_point(probe_dir)
            or marker.is_symlink()
            or _is_junction(marker)
            or _is_reparse_point(marker)
            or marker_data != {"nonce": nonce}
        ):
            raise ValueError("invalid owned probe folder")
    api = _ProbeApi(nonce)
    window = None
    done = threading.Event()
    state = {
        "renderer": "",
        "stage": "loaded",
        "error": "INITIALIZATION_FAILED",
        "browser_pid": 0,
        "handshake_ok": False,
        "start_entered": False,
    }
    deadline = time.monotonic() + max(0.01, attempt_timeout_seconds)
    cleanup_allowed = True
    cleaned = False

    try:
        window = webview_module.create_window(
            "Teacher Manager WebView2 check",
            html=_LOCAL_HTML,
            js_api=api,
            width=1,
            height=1,
            min_size=(1, 1),
            hidden=True,
            resizable=False,
            focus=False,
        )

        def worker():
            try:
                renderer = str(getattr(webview_module, "renderer", "") or "")
                state["renderer"] = renderer
                if renderer != "edgechromium":
                    state["stage"] = "renderer"
                    state["error"] = "RENDERER_NOT_EDGECHROMIUM"
                    return
                if not window.events.loaded.wait(_remaining(deadline)):
                    state["stage"] = "loaded"
                    state["error"] = "LOADED_TIMEOUT"
                    return
                state["browser_pid"] = _browser_process_id(window)
                python_value = window.evaluate_js(
                    f"window.__teacherManagerProbeNonce={json.dumps(nonce)};"
                    "window.__teacherManagerProbeNonce"
                )
                if python_value != nonce:
                    state["stage"] = "python_to_js"
                    state["error"] = "PYTHON_TO_JS_MISMATCH"
                    return
                callback_done = threading.Event()
                callback_value = {"value": None}

                def receive(value):
                    callback_value["value"] = value
                    callback_done.set()

                window.evaluate_js(
                    f"window.pywebview.api.ping({json.dumps(nonce)})",
                    callback=receive,
                )
                if not callback_done.wait(_remaining(deadline)):
                    state["stage"] = "js_to_python"
                    state["error"] = "JS_TO_PYTHON_TIMEOUT"
                    return
                if callback_value["value"] != nonce:
                    state["stage"] = "js_to_python"
                    state["error"] = "JS_TO_PYTHON_MISMATCH"
                    return
                state["handshake_ok"] = True
                state["stage"] = "complete"
                state["error"] = ""
            except Exception:
                # 오류 원문에는 계정 경로 등이 섞일 수 있어 밖으로 보내지 않는다.
                if state["stage"] == "loaded":
                    state["error"] = "INITIALIZATION_FAILED"
                else:
                    state["error"] = f"{str(state['stage']).upper()}_FAILED"
            finally:
                _safe_destroy(window)

        def watchdog():
            if not done.wait(max(0.01, attempt_timeout_seconds)):
                if state["error"] == "INITIALIZATION_FAILED":
                    state["error"] = "LOADED_TIMEOUT"
                _safe_destroy(window)

        watcher = threading.Thread(target=watchdog, daemon=True)
        watcher.start()
        try:
            state["start_entered"] = True
            cleanup_allowed = False
            webview_module.start(
                func=worker,
                gui="edgechromium",
                private_mode=True,
                storage_path=str(probe_dir),
            )
        except Exception:
            state["stage"] = "loaded"
            state["error"] = "INITIALIZATION_FAILED"
        finally:
            done.set()
            watcher.join(timeout=0.2)

        closed = bool(window.events.closed.wait(_remaining(deadline)))
        browser_pid = int(state["browser_pid"] or 0)
        if browser_pid <= 0:
            # loaded가 오기 전에 실패했더라도 창 구현이 작업 번호를 제공할 수
            # 있으면 종료를 확인한 뒤 원래 실패 이유를 보존하고 재시도한다.
            browser_pid = _browser_process_id(window)
            state["browser_pid"] = browser_pid
        renderer = str(state["renderer"] or "")
        if parent_manages_shutdown:
            # Worker 자신이 살아 있는 동안 WebView2의 crashpad가 남을 수 있다.
            # 이 길에서는 부모 Job이 worker와 모든 후손을 끝낸 뒤 폴더를 지운다.
            if not closed:
                state["stage"] = "shutdown"
                state["error"] = "WINDOW_NOT_CLOSED"
                state["handshake_ok"] = False
        elif renderer == "edgechromium":
            if browser_pid <= 0:
                state["stage"] = "shutdown"
                state["error"] = "RENDERER_PROCESS_UNKNOWN"
                state["handshake_ok"] = False
            elif not closed or not _wait_for_process_exit(browser_pid, _remaining(deadline)):
                state["stage"] = "shutdown"
                state["error"] = "RENDERER_NOT_STOPPED"
                state["handshake_ok"] = False
            else:
                cleanup_allowed = True
        elif renderer and closed:
            # edgechromium이 아닌 화면은 실패지만 WebView2 자식 작업은 만들지 않았다.
            cleanup_allowed = True
        else:
            state["stage"] = "shutdown"
            state["error"] = "RENDERER_PROCESS_UNKNOWN"
            state["handshake_ok"] = False
    finally:
        if window is not None:
            _safe_destroy(window)
        if cleanup_allowed and not parent_manages_shutdown:
            cleaned = _cleanup_owned_probe_dir(probe_dir, temp_root, nonce)

    if parent_manages_shutdown:
        if state["handshake_ok"]:
            return WebView2ProbeResult(
                True, "edgechromium", selected_version, "complete", "", attempt
            )
        return WebView2ProbeResult(
            False,
            _public_renderer(state["renderer"]),
            selected_version,
            state["stage"],  # type: ignore[arg-type]
            str(state["error"]),
            attempt,
        )
    if not cleanup_allowed:
        return WebView2ProbeResult(
            False, _public_renderer(state["renderer"]), selected_version,
            "shutdown", str(state["error"]), attempt,
        )
    if not cleaned:
        return WebView2ProbeResult(
            False, _public_renderer(state["renderer"]), selected_version,
            "shutdown", "PROBE_FOLDER_NOT_CLEANED", attempt,
        )
    if state["handshake_ok"]:
        return WebView2ProbeResult(
            True, "edgechromium", selected_version, "complete", "", attempt
        )
    return WebView2ProbeResult(
        False,
        _public_renderer(state["renderer"]),
        selected_version,
        state["stage"],  # type: ignore[arg-type]
        str(state["error"]),
        attempt,
    )


def run_webview2_probe(
    *,
    webview_module,
    minimum_version: str,
    attempt_timeout_seconds: float = PROBE_ATTEMPT_TIMEOUT_SECONDS,
    retry_count: int = PROBE_RETRY_COUNT,
    temp_root: Path | None = None,
    nonce_factory: Callable[[], str] = make_probe_nonce,
    probe_dir: Path | None = None,
    parent_manages_shutdown: bool = False,
) -> WebView2ProbeResult:
    selected_version = ""
    attempt = 1
    try:
        os.environ["WEBVIEW2_RELEASE_CHANNELS"] = "0"
        minimum = _version_tuple(minimum_version)
        selected_version = get_stable_webview2_version(webview_module)
        selected = _version_tuple(selected_version)
        if minimum is None:
            return WebView2ProbeResult(False, "", "", "version", "MINIMUM_VERSION_INVALID", 1)
        if selected is None:
            return WebView2ProbeResult(False, "", "", "version", "SELECTED_VERSION_INVALID", 1)
        if selected < minimum:
            return WebView2ProbeResult(
                False, "", selected_version, "version", "SELECTED_VERSION_TOO_OLD", 1
            )

        try:
            root = (temp_root or Path(tempfile.gettempdir())).resolve(strict=True)
        except Exception:
            return WebView2ProbeResult(
                False, "", selected_version, "shutdown", "TEMP_ROOT_UNAVAILABLE", 1
            )
        last = WebView2ProbeResult(False, "", selected_version, "loaded", "INITIALIZATION_FAILED", 1)
        retries = max(0, min(int(retry_count), 2))
        if probe_dir is not None or parent_manages_shutdown:
            retries = 0
        for index in range(retries + 1):
            attempt = index + 1
            nonce = nonce_factory()
            if not isinstance(nonce, str) or not _NONCE_RE.fullmatch(nonce):
                return WebView2ProbeResult(
                    False, "", selected_version, "loaded", "NONCE_INVALID", attempt
                )
            last = _run_probe_attempt(
                webview_module=webview_module,
                selected_version=selected_version,
                attempt=attempt,
                attempt_timeout_seconds=min(
                    max(float(attempt_timeout_seconds), 0.01),
                    PROBE_ATTEMPT_TIMEOUT_SECONDS,
                ),
                temp_root=root,
                nonce=nonce,
                probe_dir=probe_dir,
                parent_manages_shutdown=parent_manages_shutdown,
            )
            if last.ok:
                return last
            if last.stage not in {"loaded", "python_to_js", "js_to_python"}:
                break
        return last
    except Exception:
        return WebView2ProbeResult(
            False, "", selected_version, "shutdown", "PROBE_INTERNAL_ERROR", attempt
        )


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", ctypes.c_uint32),
        ("TotalProcesses", ctypes.c_uint32),
        ("ActiveProcesses", ctypes.c_uint32),
        ("TotalTerminatedProcesses", ctypes.c_uint32),
    ]


class _WindowsProbeJob:
    """한 worker와 그 worker가 만든 후손만 담는 Windows 울타리."""

    _KILL_ON_JOB_CLOSE = 0x00002000
    _EXTENDED_LIMIT_INFORMATION = 9
    _BASIC_ACCOUNTING_INFORMATION = 1

    def __init__(self):
        if os.name != "nt":
            raise OSError("Windows Job Object unavailable")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # ctypes의 기본 반환형은 32비트 정수다. 64비트 Windows 손잡이를 그대로
        # 받도록 폭을 지정하지 않으면 우연히 잘린 다른 값으로 종료를 확인하게 된다.
        self._kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        self._kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        self._kernel32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32,
        ]
        self._kernel32.SetInformationJobObject.restype = ctypes.c_int
        self._kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        self._kernel32.QueryInformationJobObject.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
        ]
        self._kernel32.QueryInformationJobObject.restype = ctypes.c_int
        self._kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self._kernel32.TerminateJobObject.restype = ctypes.c_int
        self._kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self._kernel32.CloseHandle.restype = ctypes.c_int
        self._handle = self._kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise OSError("Job Object unavailable")
        limits = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = self._KILL_ON_JOB_CLOSE
        if not self._kernel32.SetInformationJobObject(
            self._handle,
            self._EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            self.close()
            raise OSError("Job Object limit unavailable")

    def assign(self, worker) -> bool:
        try:
            process_handle = int(worker._handle)  # subprocess Windows handle
            return bool(self._kernel32.AssignProcessToJobObject(self._handle, process_handle))
        except Exception:
            return False

    def _active_processes(self) -> int | None:
        if not self._handle:
            return None
        info = _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        if not self._kernel32.QueryInformationJobObject(
            self._handle,
            self._BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
            None,
        ):
            return None
        return int(info.ActiveProcesses)

    def finish_tree(self, timeout_seconds: float) -> bool:
        """이 Job에 든 후손만 끝내고 0개가 된 것을 실제로 확인한다."""
        if not self._handle:
            return False
        active = self._active_processes()
        if active is None:
            return False
        if active > 0 and not self._kernel32.TerminateJobObject(self._handle, 36):
            return False
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while True:
            active = self._active_processes()
            if active == 0:
                return True
            if active is None or time.monotonic() >= deadline:
                return False
            time.sleep(0.02)

    def close(self) -> None:
        handle, self._handle = getattr(self, "_handle", None), None
        if handle:
            try:
                self._kernel32.CloseHandle(handle)
            except Exception:
                pass


def _worker_command(*, minimum_version: str, probe_dir: Path, nonce: str) -> list[str]:
    arguments = [
        "--probe-webview2",
        "--minimum-version",
        minimum_version,
        "--probe-webview2-worker",
        "--probe-dir",
        str(probe_dir),
        "--probe-nonce",
        nonce,
    ]
    if bool(getattr(sys, "frozen", False)):
        return [sys.executable, *arguments]
    return [sys.executable, str(Path(__file__).resolve().with_name("__main__.py")), *arguments]


def _parse_worker_result(stdout: bytes, stderr: bytes, returncode: int) -> WebView2ProbeResult | None:
    if stderr or len(stdout) > 4096:
        return None
    lines = stdout.splitlines()
    if len(lines) != 1:
        return None
    try:
        data = json.loads(lines[0].decode("ascii"))
    except Exception:
        return None
    if not isinstance(data, dict) or set(data) != {
        "ok", "renderer", "selectedVersion", "stage", "errorCode", "attempt"
    }:
        return None
    if type(data["ok"]) is not bool or type(data["attempt"]) is not int:
        return None
    renderer = _public_renderer(data["renderer"])
    selected_version = str(data["selectedVersion"] or "")
    stage = str(data["stage"] or "")
    error_code = str(data["errorCode"] or "")
    if (
        renderer != str(data["renderer"] or "")
        or (selected_version and _version_tuple(selected_version) is None)
        or stage not in _EXIT_CODES
        or not _ERROR_CODE_RE.fullmatch(error_code)
        or data["attempt"] != 1
        or int(returncode) != _EXIT_CODES[stage]
    ):
        return None
    if data["ok"] != (stage == "complete" and not error_code):
        return None
    if stage == "complete":
        if renderer != "edgechromium" or _version_tuple(selected_version) is None:
            return None
    elif not error_code:
        return None
    return WebView2ProbeResult(
        data["ok"], renderer, selected_version, stage, error_code, 1  # type: ignore[arg-type]
    )


def _stop_ungated_worker(worker, deadline: float) -> bool:
    """울타리에 넣기 전인 작업자는 시작 신호를 닫고 정확히 그 작업만 끝낸다."""
    try:
        worker.communicate(input=b"", timeout=max(0.01, _remaining(deadline)))
    except Exception:
        try:
            worker.kill()
        except Exception:
            return False
        try:
            worker.communicate(timeout=max(0.01, _remaining(deadline)))
        except Exception:
            return False
    return getattr(worker, "returncode", None) is not None


def run_supervised_webview2_probe(
    *,
    minimum_version: str,
    attempt_timeout_seconds: float = PROBE_ATTEMPT_TIMEOUT_SECONDS,
    retry_count: int = PROBE_RETRY_COUNT,
    temp_root: Path | None = None,
    nonce_factory: Callable[[], str] = make_probe_nonce,
    process_factory=subprocess.Popen,
    job_factory=_WindowsProbeJob,
    worker_command_factory=_worker_command,
) -> WebView2ProbeResult:
    """각 시도를 별도 worker Job에 넣고 후손이 0개가 된 뒤에만 성공한다."""
    try:
        root = (temp_root or Path(tempfile.gettempdir())).resolve(strict=True)
        minimum = _version_tuple(minimum_version)
        retries = max(0, min(int(retry_count), 2))
        timeout = min(
            max(float(attempt_timeout_seconds), 0.01),
            PROBE_ATTEMPT_TIMEOUT_SECONDS,
        )
    except Exception:
        return WebView2ProbeResult(
            False, "", "", "shutdown", "PROBE_INTERNAL_ERROR", 1
        )
    if minimum is None:
        return WebView2ProbeResult(
            False, "", "", "version", "MINIMUM_VERSION_INVALID", 1
        )

    last = WebView2ProbeResult(False, "", "", "loaded", "WORKER_START_FAILED", 1)
    for index in range(retries + 1):
        attempt = index + 1
        try:
            nonce = nonce_factory()
            if not isinstance(nonce, str) or not _NONCE_RE.fullmatch(nonce):
                return WebView2ProbeResult(
                    False, "", "", "loaded", "NONCE_INVALID", attempt
                )
            probe_dir = Path(tempfile.mkdtemp(prefix=PROBE_DIR_PREFIX, dir=root))
            (probe_dir / PROBE_MARKER_NAME).write_text(
                json.dumps({"nonce": nonce}, separators=(",", ":")), encoding="utf-8"
            )
        except Exception:
            return WebView2ProbeResult(
                False, "", "", "shutdown", "PROBE_INTERNAL_ERROR", attempt
            )

        job = None
        worker = None
        assigned = False
        tree_stopped = False
        worker_result = None
        deadline = time.monotonic() + timeout
        try:
            command = worker_command_factory(
                minimum_version=minimum_version, probe_dir=probe_dir, nonce=nonce
            )
            environment = os.environ.copy()
            environment["WEBVIEW2_RELEASE_CHANNELS"] = "0"
            worker = process_factory(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            job = job_factory()
            assigned = bool(job.assign(worker))
            if not assigned:
                tree_stopped = _stop_ungated_worker(worker, deadline)
                last = WebView2ProbeResult(
                    False, "", "", "shutdown", "WORKER_JOB_UNAVAILABLE", attempt
                )
            else:
                try:
                    stdout, stderr = worker.communicate(
                        input=b"1", timeout=max(0.01, _remaining(deadline))
                    )
                    worker_result = _parse_worker_result(stdout, stderr, worker.returncode)
                    selected_tuple = (
                        _version_tuple(worker_result.selected_version)
                        if worker_result is not None and worker_result.ok
                        else None
                    )
                    if (
                        worker_result is not None
                        and worker_result.ok
                        and (selected_tuple is None or selected_tuple < minimum)
                    ):
                        worker_result = None
                    if worker_result is None:
                        last = WebView2ProbeResult(
                            False, "", "", "shutdown", "WORKER_OUTPUT_INVALID", attempt
                        )
                    else:
                        last = WebView2ProbeResult(
                            worker_result.ok,
                            worker_result.renderer,
                            worker_result.selected_version,
                            worker_result.stage,
                            worker_result.error_code,
                            attempt,
                        )
                except subprocess.TimeoutExpired:
                    last = WebView2ProbeResult(
                        False, "", "", "loaded", "WORKER_TIMEOUT", attempt
                    )
                tree_stopped = job.finish_tree(max(0.01, _remaining(deadline)))
        except Exception:
            last = WebView2ProbeResult(
                False, "", "", "loaded", "WORKER_START_FAILED", attempt
            )
            if assigned and job is not None:
                try:
                    tree_stopped = job.finish_tree(max(0.01, _remaining(deadline)))
                except Exception:
                    tree_stopped = False
            elif worker is not None:
                tree_stopped = _stop_ungated_worker(worker, deadline)
            else:
                tree_stopped = True
        finally:
            if job is not None:
                job.close()

        if not tree_stopped:
            return WebView2ProbeResult(
                False, "", last.selected_version,
                "shutdown", "WORKER_TREE_NOT_STOPPED", attempt,
            )
        if not _cleanup_owned_probe_dir(probe_dir, root, nonce):
            return WebView2ProbeResult(
                False, "", last.selected_version,
                "shutdown", "PROBE_FOLDER_NOT_CLEANED", attempt,
            )
        if last.ok:
            return last
        if last.stage not in {"loaded", "python_to_js", "js_to_python"}:
            break
    return last


_EXIT_CODES = {
    "version": 31,
    "renderer": 32,
    "loaded": 33,
    "python_to_js": 34,
    "js_to_python": 35,
    "shutdown": 36,
    "complete": 0,
}


class _SilentArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


class _DiscardOutput:
    def write(self, value) -> int:
        return len(str(value))

    def flush(self) -> None:
        pass


def _write_windows_stdout(payload: bytes) -> bool:
    """창 없는 설치본에서도 부모가 연결한 표준 출력 손잡이에 한 줄을 쓴다."""
    if os.name != "nt" or not payload:
        return False
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.argtypes = [ctypes.c_uint32]
        kernel32.GetStdHandle.restype = ctypes.c_void_p
        kernel32.WriteFile.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
        ]
        kernel32.WriteFile.restype = ctypes.c_int
        handle = kernel32.GetStdHandle(ctypes.c_uint32(-11).value)
        if not handle or handle == ctypes.c_void_p(-1).value:
            return False
        buffer = ctypes.create_string_buffer(payload)
        written = ctypes.c_uint32()
        ok = kernel32.WriteFile(
            handle,
            ctypes.cast(buffer, ctypes.c_void_p),
            len(payload),
            ctypes.byref(written),
            None,
        )
        return bool(ok) and int(written.value) == len(payload)
    except Exception:
        return False


def _read_windows_stdin_byte() -> bytes:
    """창 없는 내부 작업자가 부모의 시작 신호 한 바이트만 읽는다."""
    if os.name != "nt":
        return b""
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.argtypes = [ctypes.c_uint32]
        kernel32.GetStdHandle.restype = ctypes.c_void_p
        kernel32.ReadFile.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
        ]
        kernel32.ReadFile.restype = ctypes.c_int
        handle = kernel32.GetStdHandle(ctypes.c_uint32(-10).value)
        if not handle or handle == ctypes.c_void_p(-1).value:
            return b""
        buffer = ctypes.create_string_buffer(1)
        read_count = ctypes.c_uint32()
        ok = kernel32.ReadFile(
            handle,
            ctypes.cast(buffer, ctypes.c_void_p),
            1,
            ctypes.byref(read_count),
            None,
        )
        return buffer.raw[:1] if ok and read_count.value == 1 else b""
    except Exception:
        return b""


def _read_worker_gate(stream) -> bytes:
    try:
        if stream is not None:
            binary = getattr(stream, "buffer", stream)
            value = binary.read(1)
            if isinstance(value, str):
                value = value.encode("ascii", errors="ignore")
            if isinstance(value, bytes):
                return value
    except Exception:
        pass
    return _read_windows_stdin_byte()


def _sanitize_public_result(result: WebView2ProbeResult) -> WebView2ProbeResult:
    """마지막 출력 자리에서도 정해진 값만 허용해 사적인 오류 조각을 막는다."""
    try:
        stage = str(result.stage)
        renderer = _public_renderer(result.renderer)
        selected_version = str(result.selected_version or "")
        if _version_tuple(selected_version) is None:
            selected_version = ""
        error_code = str(result.error_code or "")
        attempt = int(result.attempt)
        if attempt not in {1, 2}:
            attempt = 1
        if stage == "complete":
            if (
                result.ok is True
                and renderer == "edgechromium"
                and selected_version
                and not error_code
            ):
                return WebView2ProbeResult(
                    True, renderer, selected_version, "complete", "", attempt
                )
        elif (
            stage in _EXIT_CODES
            and stage != "complete"
            and result.ok is False
            and bool(error_code)
            and _ERROR_CODE_RE.fullmatch(error_code)
        ):
            return WebView2ProbeResult(
                False,
                renderer,
                selected_version,
                stage,  # type: ignore[arg-type]
                error_code,
                attempt,
            )
    except Exception:
        pass
    return WebView2ProbeResult(
        False, "", "", "shutdown", "PROBE_INTERNAL_ERROR", 1
    )


def _write_result_line(result: WebView2ProbeResult, stream) -> None:
    result = _sanitize_public_result(result)
    line = json.dumps(result.public_dict(), ensure_ascii=True, separators=(",", ":")) + "\n"
    try:
        if stream is not None:
            stream.write(line)
            stream.flush()
            return
    except Exception:
        pass
    payload = line.encode("ascii")
    if _write_windows_stdout(payload):
        return
    try:
        os.write(1, payload)
    except Exception:
        pass


def webview2_probe_main(
    argv: Sequence[str],
    *,
    webview_module=None,
    overall_timeout_seconds: float = PROBE_OVERALL_TIMEOUT_SECONDS,
    force_exit: Callable[[int], object] = os._exit,
) -> int:
    parser = _SilentArgumentParser(add_help=False)
    parser.add_argument("--probe-webview2", action="store_true")
    parser.add_argument("--minimum-version", default="")
    parser.add_argument("--probe-webview2-worker", action="store_true")
    parser.add_argument("--probe-dir", default="")
    parser.add_argument("--probe-nonce", default="")
    try:
        args, unknown = parser.parse_known_args(list(argv))
    except (SystemExit, ValueError):
        args, unknown = None, ["invalid"]
    os.environ["WEBVIEW2_RELEASE_CHANNELS"] = "0"
    invalid_worker_arguments = bool(
        args is not None
        and (
            (args.probe_webview2_worker and (not args.probe_dir or not args.probe_nonce))
            or (
                not args.probe_webview2_worker
                and (bool(args.probe_dir) or bool(args.probe_nonce))
            )
        )
    )
    if args is None or unknown or not args.probe_webview2 or invalid_worker_arguments:
        result = WebView2ProbeResult(False, "", "", "version", "PROBE_ARGUMENT_INVALID", 1)
    else:
        public_stream = sys.stdout
        emitted = threading.Event()
        hard_timeout = threading.Event()
        output_lock = threading.Lock()

        def emit_once(value: WebView2ProbeResult) -> None:
            with output_lock:
                if emitted.is_set():
                    return
                _write_result_line(value, public_stream)
                emitted.set()

        def end_process_at_deadline() -> None:
            hard_timeout.set()
            timeout_result = WebView2ProbeResult(
                False, "", "", "shutdown", "PROBE_OVERALL_TIMEOUT", 1
            )
            emit_once(timeout_result)
            try:
                force_exit(_EXIT_CODES["shutdown"])
            except BaseException:
                # 실제 기본값 os._exit은 돌아오지 않는다. 시험용 함수가 돌아오거나
                # 오류를 내더라도 공개 결과를 두 줄 쓰지 않게 표시만 남긴다.
                pass

        try:
            timeout = min(max(float(overall_timeout_seconds), 0.01), PROBE_OVERALL_TIMEOUT_SECONDS)
        except (TypeError, ValueError):
            timeout = PROBE_OVERALL_TIMEOUT_SECONDS
        timer = threading.Timer(timeout, end_process_at_deadline)
        timer.daemon = True
        timer.start()
        discard = _DiscardOutput()
        try:
            with redirect_stdout(discard), redirect_stderr(discard):
                if args.probe_webview2_worker:
                    if (
                        not _NONCE_RE.fullmatch(str(args.probe_nonce))
                        or _read_worker_gate(sys.stdin) != b"1"
                    ):
                        result = WebView2ProbeResult(
                            False, "", "", "loaded", "WORKER_GATE_INVALID", 1
                        )
                    else:
                        owned_dir = Path(os.path.abspath(str(args.probe_dir)))
                        try:
                            import webview as worker_webview
                        except BaseException:
                            worker_webview = None
                        if worker_webview is None:
                            result = WebView2ProbeResult(
                                False, "", "", "renderer", "PYWEBVIEW_UNAVAILABLE", 1
                            )
                        else:
                            system_temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
                            result = run_webview2_probe(
                                webview_module=worker_webview,
                                minimum_version=args.minimum_version,
                                retry_count=0,
                                temp_root=system_temp_root,
                                nonce_factory=lambda: str(args.probe_nonce),
                                probe_dir=owned_dir,
                                parent_manages_shutdown=True,
                            )
                elif webview_module is None:
                    # 공개 검사 과정에는 WebView를 들이지 않는다. 별도 작업자를
                    # 먼저 Windows 울타리에 넣은 뒤 시작시켜야 남은 작업도 끝낼 수 있다.
                    result = run_supervised_webview2_probe(
                        minimum_version=args.minimum_version,
                    )
                else:
                    result = run_webview2_probe(
                        webview_module=webview_module,
                        minimum_version=args.minimum_version,
                    )
        except BaseException:
            result = WebView2ProbeResult(
                False, "", "", "shutdown", "PROBE_INTERNAL_ERROR", 1
            )
        finally:
            timer.cancel()
        if hard_timeout.is_set():
            return _EXIT_CODES["shutdown"]
        result = _sanitize_public_result(result)
        emit_once(result)
        return _EXIT_CODES[result.stage]

    result = _sanitize_public_result(result)
    _write_result_line(result, sys.stdout)
    return _EXIT_CODES[result.stage]
