from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from brity_bridge import atomic_io

# 로그에는 이 단계 이름과 짧은 코드만 남긴다. 메시지 본문·개인정보는 절대 넣지 않는다.
STAGES = (
    "foreground",
    "menu",
    "clipboard",
    "parse",
    "attachment",
    "duplicate",
    "profile",
    "gemini",
    "check",
    "execute",
    "done",
)

_HASH_PREFIX_LEN = 12
_DETAIL_MAX_LEN = 200


def append_log(logs_dir: Path, ok: bool, stage: str, source_hash: str, detail: str = "") -> Path:
    if stage not in STAGES:
        raise ValueError(f"unknown stage: {stage}")
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    path = logs_dir / f"bridge-{now:%Y-%m-%d}.log"
    clean_detail = " ".join(str(detail).split())[:_DETAIL_MAX_LEN]
    line = "\t".join(
        [
            f"{now:%Y-%m-%d %H:%M:%S}",
            "OK" if ok else "FAIL",
            stage,
            (source_hash or "")[:_HASH_PREFIX_LEN],
            clean_detail,
        ]
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return path


def write_last_status(state_dir: Path, summary: dict) -> None:
    atomic_io.atomic_write_text(
        Path(state_dir) / "last-status.json",
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    )


def read_last_status(state_dir: Path) -> dict | None:
    try:
        raw = json.loads((Path(state_dir) / "last-status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None
