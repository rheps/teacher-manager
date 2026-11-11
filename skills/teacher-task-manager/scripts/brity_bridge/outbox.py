"""Account-bound, write-ahead Brity registration delivery (SIGNIN-03, C7)."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from brity_bridge import account_sessions, component_lock, paths
from dataclasses import dataclass

from brity_bridge.proposal_check import CheckedAction

# The outbox is the only route to Google (P6): these types moved here from the removed gws module.
PERMISSION_FAILURE_DETAIL = "이 항목을 등록할 Google 권한을 확인해 주세요."
GENERAL_FAILURE_DETAIL = "Google에서 이 항목을 처리하지 못했습니다. 잠시 뒤 다시 시도해 주세요."
RESULT_RECORD_FAILURE_DETAIL = (
    "Google에 이미 만들었을 수 있으니 같은 메시지를 다시 누르지 말고 기록에서 확인해 주세요."
)


@dataclass
class ActionResult:
    action: CheckedAction
    status: str  # created | duplicate | failed
    google_id: str = ""
    detail: str = ""
    retry_allowed: bool = True


@dataclass
class ExecutionReport:
    results: list

    def _by_status(self, status: str) -> list:
        return [result for result in self.results if result.status == status]

    @property
    def created(self) -> list:
        return self._by_status("created")

    @property
    def duplicates(self) -> list:
        return self._by_status("duplicate")

    @property
    def failures(self) -> list:
        return self._by_status("failed")

    @property
    def all_done(self) -> bool:
        return not self.failures

KST = timezone(timedelta(hours=9))
WAITING = "로그인하면 등록돼요"
UNKNOWN = "Google에 이미 만들었을 수 있으니 같은 메시지를 다시 누르지 말고 기록에서 확인해 주세요."
TERMINAL = {"created", "duplicate", "rejected", "refused", "unknown", "needs-decision", "not-registered"}


def make_entry(account, subject_key, source_hash, actions, captured_at=None):
    stamp = captured_at or datetime.now(KST)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=KST)
    stamp = stamp.astimezone(KST)
    return {"requestId": str(uuid.uuid4()), "email": account.casefold(), "subjectKey": subject_key,
            "schoolYear": stamp.year - (stamp.month < 3), "capture": {"sourceHash": source_hash,
            "capturedOn": stamp.date().isoformat(), "capturedAt": stamp.isoformat()},
            "actions": [{"actionKey": a.action_key, "kind": a.kind, "target": a.target,
                         "googleTargetId": a.google_id, "payload": a.payload} for a in actions],
            "results": [], "choice": None}


class Outbox:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.directory = component_lock.prepare_direct_directory(self.folder / "brity-bridge" / "outbox")

    def entries(self):
        if not self.directory.exists():
            return []
        rows = []
        for path in self.directory.glob("*.json"):
            component_lock.prepare_direct_file_path(path)
            value = json.loads(path.read_text(encoding="utf-8"))
            if path.stem != value.get("capture", {}).get("sourceHash"):
                raise ValueError("Outbox identity invalid")
            rows.append(value)
        return rows

    def save(self, entry):
        source = str(entry["capture"]["sourceHash"])
        if len(source) != 64 or any(ch not in "0123456789abcdef" for ch in source):
            raise ValueError("Outbox identity invalid")
        path = component_lock.prepare_direct_file_path(self.directory / (source + ".json"))
        component_lock.atomic_write_text_unique(path, json.dumps(entry, ensure_ascii=False, separators=(",", ":")))

    def enqueue(self, entry):
        old = next((row for row in self.entries() if row["capture"]["sourceHash"] == entry["capture"]["sourceHash"]), None)
        if old:
            if old["email"] != entry["email"]:
                raise ValueError("Outbox owner mismatch")
            return old  # Capture payloads, target IDs and original time stay fixed.
        self.save(entry)  # Write-ahead, including while signed out.
        return entry

    def drain(self, client, account, *, now=None, source_hash=None):
        clock = now or datetime.now(KST)
        answers = []
        if client is None or client.email.casefold() != account.casefold():
            return answers
        for entry in self.entries():
            if source_hash and entry["capture"]["sourceHash"] != source_hash:
                continue
            if entry["email"] != account.casefold() or (entry["subjectKey"] and entry["subjectKey"] != client.subject_key):
                continue
            if entry.get("choice") == "skip":
                continue
            if clock - datetime.fromisoformat(entry["capture"]["capturedAt"]) > timedelta(days=7) and entry.get("choice") != "send":
                continue
            if not entry["subjectKey"]:
                entry["subjectKey"] = client.subject_key
                self.save(entry)
            done = {r["actionKey"] for r in entry.get("results", []) if r["status"] in TERMINAL}
            pending = [a for a in entry["actions"] if a["actionKey"] not in done]
            if not pending:
                answers.append(entry)
                continue
            reply = client.data_call("/v1/data/registrations", {"requestId": entry["requestId"],
                "expected": {"subjectKey": entry["subjectKey"], "schoolYear": entry["schoolYear"]},
                "capture": entry["capture"], "actions": pending})
            # Lost replies remain pending. Registration claims allow recovery reads, never a second insert.
            if reply.status == 200 and isinstance(reply.body, dict) and isinstance(reply.body.get("results"), list):
                by_key = {r["actionKey"]: r for r in entry.get("results", [])}
                allowed = {a["actionKey"] for a in pending}
                for result in reply.body["results"]:
                    if result.get("actionKey") in allowed and isinstance(result.get("status"), str):
                        by_key[result["actionKey"]] = result
                entry["results"] = list(by_key.values())
                self.save(entry)
            answers.append(entry)
        return answers


def helper_for_folder(folder):
    from dashboard import server_session
    state = account_sessions.read_state(folder)
    if state.get("phase") != "active":
        return None
    parent = server_session.ServerSessionClient(server_session.server_base_url(os.environ), server_session.open_store(folder))
    if not parent.restore(state.get("account")):
        return None
    return parent.helper_client()


def execute(folder, actions, history, source_hash, captured_at=None, client=None):
    state = account_sessions.read_state(folder)
    history.require_usable(source_hash, (a.action_key for a in actions))
    known = history.completed_keys(source_hash)
    account = state.get("account") or state.get("settings_owner") or ""
    if not account:
        return ExecutionReport([ActionResult(a, "failed", "", WAITING, retry_allowed=False) for a in actions])
    owner = account_sessions._marker_subject_key(Path(folder), account)
    box = Outbox(folder)
    with component_lock.exclusive_file_lock(Path(folder) / ".brity-outbox.lock", timeout=10):
        entry = box.enqueue(make_entry(account, owner, source_hash, actions, captured_at))
        if known:
            recorded = (history.entry(source_hash) or {}).get("actions", {})
            done = {r["actionKey"] for r in entry["results"]}
            for a in actions:
                if a.action_key in known and a.action_key not in done:
                    entry["results"].append({"actionKey": a.action_key, "status": "duplicate", "googleId": recorded[a.action_key].get("google_id", "")})
            box.save(entry)
        try:
            client = client or helper_for_folder(folder)
            box.drain(client, account, source_hash=source_hash)
            entry = next(row for row in box.entries() if row["capture"]["sourceHash"] == source_hash)
        except (OSError, ValueError, account_sessions.AccountSessionError):
            pass  # Durable entry remains. Never fall back to gws or another account.
    by_key = {r["actionKey"]: r for r in entry["results"]}
    results = []
    for action in actions:
        result = by_key.get(action.action_key, {}); status = result.get("status", "deferred")
        if status in {"created", "duplicate"}:
            if result.get("googleId") or action.kind == "notice":
                history.record_action(source_hash, action.action_key, action.kind, result.get("googleId", "")); history.save()
            results.append(ActionResult(action, status, result.get("googleId", "")))
        else:
            detail = UNKNOWN if status in {"unknown", "needs-decision"} else PERMISSION_FAILURE_DETAIL if result.get("code") == "GOOGLE_PERMISSION_REQUIRED" else GENERAL_FAILURE_DETAIL if status in {"rejected", "refused"} else WAITING
            results.append(ActionResult(action, "failed", result.get("googleId", ""), detail, retry_allowed=False))
    return ExecutionReport(results)


def drain_active(root):
    with account_sessions.work(root) as state:
        folder = account_sessions.active_config_dir(root, state)
        with component_lock.exclusive_file_lock(folder / ".brity-outbox.lock", timeout=1):
            rows = Outbox(folder).drain(helper_for_folder(folder), state.get("account", ""))
            from brity_bridge.history import HistoryStore
            from brity_bridge import message_archive
            history = HistoryStore(paths.history_path(folder))
            for entry in rows:
                actions = {a["actionKey"]: a for a in entry["actions"]}
                for result in entry["results"]:
                    if result["status"] in {"created", "duplicate"} and (result.get("googleId") or actions[result["actionKey"]]["kind"] == "notice"):
                        history.record_action(entry["capture"]["sourceHash"], result["actionKey"], actions[result["actionKey"]]["kind"], result.get("googleId", ""))
                history.save()
                complete = len(entry["results"]) == len(actions) and all(r["status"] in {"created", "duplicate"} for r in entry["results"])
                message_archive.save_recovery_stage(paths.bridge_state_dir(folder), entry["capture"]["sourceHash"],
                    "google_complete" if complete else "google_partial", google_results=[{
                        "action_key": r["actionKey"], "kind": actions[r["actionKey"]]["kind"], "target": actions[r["actionKey"]]["target"],
                        "google_id": r.get("googleId", ""), "status": r["status"], "detail": "",
                    } for r in entry["results"]])
            return rows


def local_state(folder, source_hash):
    entry = next((e for e in Outbox(folder).entries() if e["capture"]["sourceHash"] == source_hash), None)
    if not entry:
        return None
    results = {r["actionKey"]: r for r in entry["results"]}
    pending = any(results.get(a["actionKey"], {}).get("status", "deferred") not in TERMINAL for a in entry["actions"])
    old = pending and datetime.now(KST) - datetime.fromisoformat(entry["capture"]["capturedAt"]) > timedelta(days=7) and entry.get("choice") is None
    return {"old": old, "skipped": entry.get("choice") == "skip", "actions": [{
        "key": a["actionKey"], "title": a["payload"].get("summary") or a["payload"].get("title") or a["payload"].get("content", ""),
        "status": results.get(a["actionKey"], {}).get("status", "deferred"),
        "code": results.get(a["actionKey"], {}).get("code", ""),
        "at": results.get(a["actionKey"], {}).get("at", ""), "device": results.get(a["actionKey"], {}).get("updatedBy", {}).get("deviceLabel", ""),
    } for a in entry["actions"]]}


def decide(folder, source_hash, action_key, choice):
    with component_lock.exclusive_file_lock(Path(folder) / ".brity-outbox.lock", timeout=10):
        box = Outbox(folder)
        entry = next(e for e in box.entries() if e["capture"]["sourceHash"] == source_hash)
        state = account_sessions.read_state(folder)
        if entry["email"] != state.get("account", "").casefold():
            raise account_sessions.AccountSessionError()
        if choice in {"send", "skip"}:
            entry["choice"] = choice
            box.save(entry)
            if choice == "send":
                box.drain(helper_for_folder(folder), state["account"], source_hash=source_hash)
            return {"status": "applied"}
        client = helper_for_folder(folder)
        if client is None or (entry["subjectKey"] and entry["subjectKey"] != client.subject_key):
            return {"status": "deferred"}
        result = next(r for r in entry["results"] if r["actionKey"] == action_key)
        claim_key = result["claimKey"]
        if choice == "retry" and result["status"] in {"not-registered", "rejected"}:
            action = next(a for a in entry["actions"] if a["actionKey"] == action_key)
            response = client.data_call("/v1/data/registrations", {"requestId": entry["requestId"], "retryClaim": claim_key,
                "expected": {"subjectKey": client.subject_key, "schoolYear": entry["schoolYear"]}, "capture": entry["capture"], "actions": [action]})
            if response.status == 200 and response.body.get("results"):
                result.update(response.body["results"][0])
            else:
                result["status"] = "unknown"  # Lost retry answer is held, never automatically inserted.
            box.save(entry)
            return {"status": result["status"]}
        if choice not in {"confirmed", "not-registered"} or result["status"] not in {"unknown", "needs-decision"}:
            raise ValueError("Outbox choice invalid")
        observed = client.data_call("/v1/data/registrations", {"status": [claim_key]})
        if observed.status != 200 or not observed.body.get("claims"):
            return {"status": "deferred"}
        claim = observed.body["claims"][0]
        if claim["phase"] in {"CONFIRMED_RESULT", "CONFIRMED_REJECTED"}:
            result["status"] = "duplicate" if claim["phase"] == "CONFIRMED_RESULT" else "not-registered" if (claim.get("resolution") or {}).get("choice") == "not-registered" else "rejected"
            result["googleId"] = claim.get("googleId", "")
            box.save(entry)
            return {"status": "applied"}
        response = client.data_call("/v1/data/registrations", {"decide": {"claimKey": claim_key, "updatedAt": claim["updatedAt"], "choice": choice}})
        if response.status == 200:
            result["status"] = "duplicate" if choice == "confirmed" else "not-registered"
            result["googleId"] = claim.get("googleId", "")
            box.save(entry)
        return {"status": "applied" if response.status == 200 else "deferred"}
