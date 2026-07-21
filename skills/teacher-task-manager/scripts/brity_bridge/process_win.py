from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Sequence


_SECRET_FLAGS = {
    "--api-key", "--apikey", "--key", "--token", "--access-token",
    "--refresh-token", "--authorization", "-k",
}


def _hidden_options(options: dict | None = None) -> dict:
    merged = dict(options or {})
    if sys.platform != "win32":
        return merged
    merged["creationflags"] = int(merged.get("creationflags", 0)) | subprocess.CREATE_NO_WINDOW
    startup = merged.get("startupinfo") or subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    merged["startupinfo"] = startup
    return merged


def hidden_process_kwargs() -> dict:
    """Windows에서는 검은 명령창을 만들지 않는 실행값을 돌려준다."""
    return _hidden_options()


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def run_captured(
    args: Sequence[str], *, cwd=None, timeout=None, runner=subprocess.run
) -> tuple[int, str]:
    options = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if cwd is not None:
        options["cwd"] = str(cwd)
    if timeout is not None:
        options["timeout"] = timeout
    try:
        completed = runner(list(args), **_hidden_options(options))
    except subprocess.TimeoutExpired as error:
        output = _as_text(error.stdout) + _as_text(error.stderr)
        return 124, output or "실행 시간이 너무 오래 걸려 중단했습니다."
    except OSError as error:
        return 127, str(error)
    output = _as_text(getattr(completed, "stdout", ""))
    output += _as_text(getattr(completed, "stderr", ""))
    return int(completed.returncode), output


def popen_hidden(args: Sequence[str], *, popen=subprocess.Popen, **kwargs):
    return popen(list(args), **_hidden_options(kwargs))


def parse_first_json(output: str):
    """명령 출력에서 첫 JSON 값만 읽는다.

    gws는 로그인 저장 방식에 따라 "Using keyring backend: keyring" 같은 안내 줄을
    stderr로 덧붙이고, run_captured는 stdout·stderr를 합쳐 돌려준다 — 그 줄이
    JSON 앞뒤 어디에 붙어도 견뎌야 한다. 빈 출력은 빈 dict, JSON이 없으면 ValueError.
    """
    text = str(output or "").strip()
    if not text:
        return {}
    starts = [index for index in (text.find("{"), text.find("[")) if index != -1]
    if not starts:
        raise ValueError("JSON이 없는 출력: " + text[:120])
    value, _end = json.JSONDecoder().raw_decode(text[min(starts):])
    return value


def _safe_argument_text(args: Sequence[str]) -> tuple[str, set[str]]:
    safe: list[str] = []
    secrets: set[str] = set()
    hide_next = False
    for raw in args:
        value = str(raw)
        if hide_next:
            secrets.add(value)
            safe.append("[숨김]")
            hide_next = False
            continue
        name, separator, attached = value.partition("=")
        if name.lower() in _SECRET_FLAGS:
            if separator:
                secrets.add(attached)
                safe.append(f"{name}=[숨김]")
            else:
                safe.append(value)
                hide_next = True
            continue
        safe.append(value)
    return " ".join(safe), secrets


def _redact(text: str, secrets: set[str]) -> str:
    cleaned = str(text or "")
    for secret in sorted((value for value in secrets if value), key=len, reverse=True):
        cleaned = cleaned.replace(secret, "[숨김]")
    cleaned = re.sub(r"(?i)Bearer\s+[^\s,;\"']+", "Bearer [숨김]", cleaned)
    cleaned = re.sub(
        r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|secret)"
        r"(\s*[\"']?\s*[:=]\s*[\"']?)([^\s,;}\"']+)",
        r"\1\2[숨김]",
        cleaned,
    )
    cleaned = re.sub(r"[\w.+-]+@[\w.-]+", "[이메일 숨김]", cleaned)
    cleaned = re.sub(
        r"(?i)([A-Z]:\\[^\\\s]+\\)[^\\\s]+",
        lambda match: match.group(1) + "[사용자]",
        cleaned,
    )
    cleaned = re.sub(r"/home/[^/\s]+", "/home/[사용자]", cleaned)
    return cleaned


def safe_log_text(args: Sequence[str], output: str) -> str:
    command, secrets = _safe_argument_text(args)
    return f"실행: {_redact(command, secrets)}\n결과: {_redact(output, secrets)}"


def write_process_log(
    log_dir: Path, args: Sequence[str], returncode: int, output: str
) -> Path:
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone()
    path = log_dir / f"process-{now:%Y-%m-%d}.log"
    safe = safe_log_text(args, str(output or "")[-600:]).replace("\n", " | ")
    with path.open("a", encoding="utf-8") as file:
        file.write(f"{now:%Y-%m-%d %H:%M:%S} 종료={int(returncode)} {safe}\n")
    return path
