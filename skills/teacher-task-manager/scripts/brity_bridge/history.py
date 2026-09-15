from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from brity_bridge import atomic_io


HISTORY_UNAVAILABLE_DETAIL = (
    "중복 방지 기록을 안전하게 읽지 못해 Google에 추가 등록하지 않았습니다. "
    "기존 기록을 지우지 말고 Teacher Manager의 최근 기록과 Google 등록 결과를 확인해 주세요."
)
LEGACY_RESULT_UNCONFIRMED_DETAIL = (
    "이전 등록 결과를 확인할 수 없는 항목과 겹쳐 Google에 다시 등록하지 않았습니다. "
    "기존 기록을 지우지 말고 Teacher Manager의 최근 기록과 Google 등록 결과를 확인해 주세요."
)


class HistoryUnavailableError(RuntimeError):
    """Existing execution evidence cannot safely authorize more writes."""


def _unique_history_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ambiguous history key")
        result[key] = value
    return result


def _legacy_unconfirmed_keys(entry: dict) -> set[str]:
    # Older completed records did not require a Google result ID. Preserve that
    # evidence, but never use it as confirmation or permission to repeat a write.
    if entry.get("completed") is not True or "write_intents" in entry:
        return set()
    return {
        key for key, action in entry.get("actions", {}).items()
        if isinstance(action, dict)
        and action.get("kind") in ("calendar", "task")
        and action.get("google_id") == ""
    }


def _valid_history(raw: object) -> bool:
    if not isinstance(raw, dict) or not isinstance(raw.get("messages"), dict):
        return False
    kinds = ("calendar", "task", "notice")
    for source, entry in raw["messages"].items():
        if not isinstance(source, str) or not source or not isinstance(entry, dict):
            return False
        if "completed" in entry and type(entry["completed"]) is not bool:
            return False
        actions = entry.get("actions", {})
        intents = entry.get("write_intents", {})
        if not isinstance(actions, dict) or not isinstance(intents, dict):
            return False
        if entry.get("completed") is True and not actions:
            return False
        legacy_keys = _legacy_unconfirmed_keys(entry)
        for key, action in actions.items():
            if not isinstance(key, str) or not key or not isinstance(action, dict):
                return False
            kind, google_id = action.get("kind"), action.get("google_id")
            if kind not in kinds or not isinstance(google_id, str):
                return False
            if kind != "notice" and not google_id.strip() and key not in legacy_keys:
                return False
        for key, intent in intents.items():
            if not isinstance(key, str) or not key or not isinstance(intent, dict):
                return False
            if (intent.get("kind") not in kinds
                    or intent.get("state") not in ("write_started", "confirmed")
                    or not isinstance(intent.get("intent_hash"), str)
                    or not intent["intent_hash"]
                    or not isinstance(intent.get("pre_ids"), list)
                    or any(not isinstance(item, str) or not item for item in intent["pre_ids"])):
                return False
            if "google_id" in intent and not isinstance(intent["google_id"], str):
                return False
            if intent["kind"] == "notice" and (
                not isinstance(intent.get("account"), str) or not intent["account"]
                or not isinstance(intent.get("spreadsheet_id"), str) or not intent["spreadsheet_id"]
                or intent.get("target") not in ("personal", "class")
            ):
                return False
    return True


class HistoryStore:
    """메시지 해시별로 이미 만든 등록 항목을 기억해 중복 등록을 막는다."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict = {"messages": {}}
        self.load_state = "unloaded"

    def load(self) -> None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            # Missing primary evidence beside an old backup is not a first run.
            if self.load_state not in {"unloaded", "new"}:
                self.load_state = "unavailable"
                return
            for evidence in (self.path, self.path.with_name(self.path.name + ".bak")):
                try:
                    evidence.lstat()
                except FileNotFoundError:
                    continue
                except OSError:
                    self.load_state = "unavailable"
                    return
                else:
                    self.load_state = "unavailable"
                    return
            self.data = {"messages": {}}
            self.load_state = "new"
            return
        except (OSError, UnicodeError):
            self.load_state = "unavailable"
            return
        try:
            raw = json.loads(text, object_pairs_hook=_unique_history_object)
        except ValueError:
            self.load_state = "corrupt"
            return
        if _valid_history(raw):
            self.data = raw
            self.load_state = "valid"
        else:
            self.load_state = "unsupported"

    def require_usable(self, source_hash: str | None = None, action_keys=()) -> None:
        if self.load_state == "unloaded":
            self.load()
        if self.load_state not in {"new", "valid"} or not _valid_history(self.data):
            raise HistoryUnavailableError(HISTORY_UNAVAILABLE_DETAIL)
        requested_keys = set(action_keys)
        for source, entry in self.data["messages"].items():
            legacy_keys = _legacy_unconfirmed_keys(entry)
            if legacy_keys and (source == source_hash or legacy_keys & requested_keys):
                raise HistoryUnavailableError(LEGACY_RESULT_UNCONFIRMED_DETAIL)

    def save(self) -> None:
        self.require_usable()
        atomic_io.atomic_write_text(
            self.path, json.dumps(self.data, ensure_ascii=False, indent=2) + "\n"
        )
        self.load_state = "valid"

    def entry(self, source_hash: str) -> dict | None:
        self.require_usable(source_hash)
        return self.data["messages"].get(source_hash)

    def _ensure_entry(self, source_hash: str) -> dict:
        self.require_usable(source_hash)
        entry = self.data["messages"].setdefault(
            source_hash,
            {"when": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "completed": False, "actions": {}},
        )
        entry.setdefault("actions", {})
        return entry

    def is_completed(self, source_hash: str) -> bool:
        entry = self.entry(source_hash)
        return bool(entry and entry.get("completed"))

    def completed_keys(self, source_hash: str) -> set[str]:
        entry = self.entry(source_hash)
        if not entry:
            return set()
        return set(entry.get("actions", {}).keys())

    def record_action(self, source_hash: str, action_key: str, kind: str, google_id: str) -> None:
        self.require_usable(source_hash, (action_key,))
        if kind in ("calendar", "task") and (not isinstance(google_id, str) or not google_id.strip()):
            raise HistoryUnavailableError(HISTORY_UNAVAILABLE_DETAIL)
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
        *,
        account: str = "",
        spreadsheet_id: str = "",
        target: str = "",
    ) -> None:
        self.require_usable(source_hash, (action_key,))
        entry = self._ensure_entry(source_hash)
        intents = entry.setdefault("write_intents", {})
        intents[action_key] = {
            "kind": str(kind),
            "pre_ids": sorted({str(item) for item in pre_ids if str(item)}),
            "intent_hash": str(intent_hash),
            "state": "write_started",
        }
        if kind == "notice":
            intents[action_key].update(
                account=account, spreadsheet_id=spreadsheet_id, target=target,
            )

    def clear_write_intent(self, source_hash: str, action_key: str) -> None:
        entry = self.entry(source_hash)
        intents = entry.get("write_intents") if isinstance(entry, dict) else None
        if isinstance(intents, dict):
            intents.pop(action_key, None)

    def mark_completed(self, source_hash: str) -> None:
        self._ensure_entry(source_hash)["completed"] = True
