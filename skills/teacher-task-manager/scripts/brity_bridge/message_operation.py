"""One local lock per Brity message for shortcut and dashboard recovery paths."""

from __future__ import annotations

import hashlib
import re
from contextlib import contextmanager
from pathlib import Path

from brity_bridge import component_lock


_SOURCE_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class MessageOperationBusy(RuntimeError):
    """Another window or process already owns this exact message operation."""


def lock_path(state_dir: Path, source_hash: str) -> Path:
    source_hash = str(source_hash or "").casefold()
    if not source_hash:
        raise ValueError("메시지 식별값이 올바르지 않습니다.")
    safe_name = (
        source_hash
        if _SOURCE_HASH_RE.fullmatch(source_hash)
        else hashlib.sha256(source_hash.encode("utf-8")).hexdigest()
    )
    return Path(state_dir) / "message-operation-locks" / f"{safe_name}.lock"


@contextmanager
def message_operation_lock(
    state_dir: Path, source_hash: str, *, timeout: float = 0.0
):
    """Allow only one active operation for a message across threads/processes."""

    try:
        with component_lock.exclusive_file_lock(
            lock_path(Path(state_dir), source_hash), timeout=timeout
        ):
            yield
    except TimeoutError as error:
        raise MessageOperationBusy("이 메시지는 다른 창에서 처리 중입니다.") from error
