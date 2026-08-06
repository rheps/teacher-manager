"""한 명령과 그 명령이 만든 모든 후손을 Windows 울타리 안에서 실행한다."""
from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


WORKER_SWITCH = "--ai-process-worker"
TREE_NOT_STOPPED = 125
WORKER_START_FAILED = 127


@dataclass(frozen=True)
class SupervisedResult:
    code: int
    output: str
    tree_stopped: bool


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


class _WindowsJob:
    """시작 신호를 기다리는 작업자와 그 후손만 담는 Windows 울타리."""

    _KILL_ON_JOB_CLOSE = 0x00002000
    _EXTENDED_LIMIT_INFORMATION = 9
    _BASIC_ACCOUNTING_INFORMATION = 1

    def __init__(self):
        if os.name != "nt":
            raise OSError("Windows Job Object unavailable")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        self._kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        self._kernel32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32,
        ]
        self._kernel32.SetInformationJobObject.restype = ctypes.c_int
        self._kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        self._kernel32.QueryInformationJobObject.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32,
            ctypes.c_void_p,
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

    def assign(self, process) -> bool:
        try:
            return bool(self._kernel32.AssignProcessToJobObject(
                self._handle, int(process._handle)
            ))
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

    def finish_tree(self, timeout: float) -> bool:
        if not self._handle:
            return False
        active = self._active_processes()
        if active is None:
            return False
        if active > 0 and not self._kernel32.TerminateJobObject(self._handle, 125):
            return False
        deadline = time.monotonic() + max(0.0, timeout)
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
            self._kernel32.CloseHandle(handle)


def _worker_command(arguments: Sequence[str]) -> list[str]:
    if bool(getattr(sys, "frozen", False)):
        return [sys.executable, WORKER_SWITCH, *map(str, arguments)]
    return [sys.executable, str(Path(__file__).resolve()), "--worker", *map(str, arguments)]


def _text(stdout: bytes | str | None, stderr: bytes | str | None = None) -> str:
    def one(value) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value or "")

    return one(stdout) + one(stderr)


def _stop_ungated(process, timeout: float) -> bool:
    try:
        process.communicate(input=b"", timeout=max(0.05, timeout))
    except Exception:
        try:
            process.kill()
            process.communicate(timeout=max(0.05, timeout))
        except Exception:
            return False
    return process.returncode is not None


def _run_windows(
    arguments: Sequence[str],
    *,
    timeout: float,
    env: Mapping[str, str] | None,
    cwd: Path | str | None,
) -> SupervisedResult:
    worker = None
    job = None
    deadline = time.monotonic() + max(0.01, float(timeout))
    try:
        worker = subprocess.Popen(
            _worker_command(arguments),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd) if cwd is not None else None,
            env=dict(env) if env is not None else None,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        job = _WindowsJob()
        if not job.assign(worker):
            stopped = _stop_ungated(worker, max(0.05, deadline - time.monotonic()))
            return SupervisedResult(TREE_NOT_STOPPED, "작업 울타리를 준비하지 못했습니다.", stopped)
        try:
            stdout, stderr = worker.communicate(
                input=b"1", timeout=max(0.01, deadline - time.monotonic())
            )
            code = int(worker.returncode)
            output = _text(stdout, stderr)
        except subprocess.TimeoutExpired as error:
            code = 124
            output = _text(error.stdout, error.stderr) or "실행 시간이 너무 오래 걸려 중단했습니다."
        stopped = job.finish_tree(5.0)
        if not stopped:
            return SupervisedResult(
                TREE_NOT_STOPPED,
                output + "\n자식 작업이 모두 끝났는지 확인하지 못했습니다.",
                False,
            )
        try:
            if worker.returncode is None:
                worker.communicate(timeout=1.0)
        except Exception:
            pass
        return SupervisedResult(code, output, True)
    except OSError as error:
        stopped = True if worker is None else _stop_ungated(worker, 1.0)
        return SupervisedResult(WORKER_START_FAILED, str(error), stopped)
    finally:
        if job is not None:
            job.close()


def _run_portable(
    arguments: Sequence[str],
    *,
    timeout: float,
    env: Mapping[str, str] | None,
    cwd: Path | str | None,
) -> SupervisedResult:
    process = None
    try:
        process = subprocess.Popen(
            list(map(str, arguments)),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd) if cwd is not None else None,
            env=dict(env) if env is not None else None,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=max(0.01, float(timeout)))
            return SupervisedResult(process.returncode, _text(stdout, stderr), True)
        except subprocess.TimeoutExpired as error:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5.0)
            return SupervisedResult(
                124,
                _text(error.stdout, error.stderr) or "실행 시간이 너무 오래 걸려 중단했습니다.",
                True,
            )
    except OSError as error:
        return SupervisedResult(WORKER_START_FAILED, str(error), process is None)


def run_supervised_command(
    arguments: Sequence[str],
    *,
    timeout: float,
    env: Mapping[str, str] | None = None,
    cwd: Path | str | None = None,
) -> SupervisedResult:
    if not arguments:
        return SupervisedResult(WORKER_START_FAILED, "실행할 명령이 없습니다.", True)
    runner = _run_windows if os.name == "nt" else _run_portable
    return runner(arguments, timeout=timeout, env=env, cwd=cwd)


def _write_worker_output(raw: bytes) -> None:
    try:
        os.write(1, raw)
        return
    except Exception:
        pass
    try:
        stream = getattr(sys.stdout, "buffer", sys.stdout)
        stream.write(raw)
        stream.flush()
    except Exception:
        pass


def worker_main(argv: Sequence[str]) -> int:
    arguments = list(map(str, argv))
    if not arguments:
        return WORKER_START_FAILED
    try:
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        gate = stream.read(1)
    except Exception:
        gate = b""
    if gate != b"1":
        return TREE_NOT_STOPPED
    try:
        completed = subprocess.run(
            arguments,
            capture_output=True,
            check=False,
        )
    except OSError as error:
        _write_worker_output(str(error).encode("utf-8", errors="replace"))
        return WORKER_START_FAILED
    _write_worker_output(bytes(completed.stdout or b"") + bytes(completed.stderr or b""))
    return int(completed.returncode)


if __name__ == "__main__":
    arguments = sys.argv[1:]
    if arguments[:1] == ["--worker"]:
        raise SystemExit(worker_main(arguments[1:]))
    raise SystemExit(WORKER_START_FAILED)
