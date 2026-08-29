from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from brity_bridge import atomic_io


class HistoryStore:
    """메시지 해시별로 이미 만든 등록 항목을 기억해 중복 등록을 막는다."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict = {"messages": {}}

    def load(self) -> None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return
        try:
            raw = json.loads(text)
        except ValueError:
            # 파손본을 옆으로 치워야 다음 save가 남은 기억(중복 방지 증거)을
            # 덮어써 영구 소실시키지 않는다. 개인톡 안내는 이 기억이 유일한
            # 중복 방어선이다.
            try:
                os.replace(self.path, self.path.with_name(self.path.name + ".bak"))
            except OSError:
                pass
            return
        if isinstance(raw, dict) and isinstance(raw.get("messages"), dict):
            self.data = raw

    def save(self) -> None:
        atomic_io.atomic_write_text(
            self.path, json.dumps(self.data, ensure_ascii=False, indent=2) + "\n"
        )

    def entry(self, source_hash: str) -> dict | None:
        return self.data["messages"].get(source_hash)

    def _ensure_entry(self, source_hash: str) -> dict:
        return self.data["messages"].setdefault(
            source_hash,
            {"when": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "completed": False, "actions": {}},
        )

    def is_completed(self, source_hash: str) -> bool:
        entry = self.entry(source_hash)
        return bool(entry and entry.get("completed"))

    def completed_keys(self, source_hash: str) -> set[str]:
        entry = self.entry(source_hash)
        if not entry:
            return set()
        return set(entry.get("actions", {}).keys())

    def record_action(self, source_hash: str, action_key: str, kind: str, google_id: str) -> None:
        entry = self._ensure_entry(source_hash)
        entry["actions"][action_key] = {"kind": kind, "google_id": google_id}
        intents = entry.get("write_intents")
        if isinstance(intents, dict) and isinstance(intents.get(action_key), dict):
            intents[action_key]["state"] = "confirmed"
            intents[action_key]["google_id"] = google_id

    def write_intent(self, source_hash: str, action_key: str) -> dict | None:
        entry = self.entry(source_hash)
        intents = entry.get("write_intents") if isinstance(entry, dict) else None
        intent = intents.get(action_key) if isinstance(intents, dict) else None
        return dict(intent) if isinstance(intent, dict) else None

    def record_write_intent(
        self,
        source_hash: str,
        action_key: str,
        kind: str,
        pre_ids,
        intent_hash: str,
    ) -> None:
        entry = self._ensure_entry(source_hash)
        intents = entry.setdefault("write_intents", {})
        intents[action_key] = {
            "kind": str(kind),
            "pre_ids": sorted({str(item) for item in pre_ids if str(item)}),
            "intent_hash": str(intent_hash),
            "state": "write_started",
        }

    def clear_write_intent(self, source_hash: str, action_key: str) -> None:
        entry = self.entry(source_hash)
        intents = entry.get("write_intents") if isinstance(entry, dict) else None
        if isinstance(intents, dict):
            intents.pop(action_key, None)

    def mark_completed(self, source_hash: str) -> None:
        self._ensure_entry(source_hash)["completed"] = True
