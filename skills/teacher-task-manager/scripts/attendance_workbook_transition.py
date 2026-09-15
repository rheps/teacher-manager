"""사용자가 확인한 새 학년도 전환만 수행한다.

과거의 여러 출석부 통합·기록 이관·Google Drive 휴지통 이동 기능은 제거했다.
이 모듈은 기존 출석부를 그대로 둔 채 새 학년도 출석부 후보를 완성하고, 모든
확인이 끝난 뒤 서버에서 현재 계정의 연결을 확정한다.
"""

from __future__ import annotations

import attendance_reference_storage as attendance_references

import json
import hashlib
import os
import re
import tempfile
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

import attendance_workbook_identity
import install_attendance_automation
from brity_bridge.google_account import is_goedu_email


def attendance_action_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def validate_attendance_action(value: object) -> dict:
    """Protected receipts describe an intent; they never grant cloud authority."""
    from attendance_binding import AttendanceBindingError
    def fail():
        raise AttendanceBindingError('ATTENDANCE_PROGRESS_INVALID')
    if not isinstance(value, dict): fail()
    item = json.loads(json.dumps(value))
    if (item.get('schemaVersion') != 1 or item.get('reason') not in ('first-setup', 'new-school-year', 'replace-trashed', 'replace-unavailable')
            or not isinstance(item.get('subjectKey'), str) or not item['subjectKey']
            or type(item.get('expectedSchoolYear')) is not int or not 2000 <= item['expectedSchoolYear'] <= 2099
            or type(item.get('expectedGeneration')) is not int or item['expectedGeneration'] < 0
            or item.get('phase') not in ('requested', 'admitted', 'running', 'reconciling', 'blocked', 'published')): fail()
    valid_id = lambda value: isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,200}', value))
    if item.get('operationId') is not None and not valid_id(item['operationId']): fail()
    if item['reason'] in ('replace-trashed', 'replace-unavailable'):
        if not valid_id(item.get('previousSpreadsheetId')): fail()
        if item.get('previousOperationId') is not None and not valid_id(item['previousOperationId']): fail()
        if item['reason'] == 'replace-unavailable':
            if (not valid_id(item.get('previousOperationId')) or item.get('explicitConfirmation') is not True
                    or not isinstance(item.get('failureCode'), str) or not item['failureCode']
                    or not isinstance(item.get('failureStage'), str) or not item['failureStage']): fail()
    elif item.get('previousOperationId') is not None: fail()
    elif item.get('previousSpreadsheetId') is not None: fail()
    if item.get('provenance') == 'saved-request':
        if (item.get('origin') not in ('initial-auto', 'explicit-create')
                or item.get('originalRequestOwnership') != 'saved-key'
                or not isinstance(item.get('idempotencyKey'), str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', item['idempotencyKey'])): fail()
        if item['origin'] == 'initial-auto' and item['reason'] != 'first-setup': fail()
    elif item.get('provenance') == 'legacy-existing-operation':
        if (item.get('idempotencyKey') is not None or item.get('originalRequestOwnership') != 'unproven'
                or item.get('explicitResume') is not True or not valid_id(item.get('operationId'))
                or not isinstance(item.get('evidence'), dict)): fail()
    else: fail()
    if item['phase'] == 'published':
        if (not valid_id(item.get('publishedSpreadsheetId')) or not valid_id(item.get('operationId'))
                or type(item.get('publishedGeneration')) is not int
                or item['publishedGeneration'] != item['expectedGeneration'] + 1): fail()
    return item


def new_attendance_action(scope, reason: str, *, origin='explicit-create', intent=None) -> dict:
    intent = intent or {}
    return validate_attendance_action(dict(schemaVersion=1, provenance='saved-request', origin=origin,
        reason=reason, subjectKey=scope.payload['subjectKey'], expectedSchoolYear=scope.year,
        expectedGeneration=scope.payload['generation'], previousSpreadsheetId=intent.get('previousSpreadsheetId'),
        previousOperationId=intent.get('previousOperationId'),
        **({key: intent.get(key) for key in ('explicitConfirmation', 'failureCode', 'failureStage')} if reason == 'replace-unavailable' else {}),
        idempotencyKey=intent.get('idempotencyKey') or str(uuid.uuid4()), originalRequestOwnership='saved-key',
        operationId=None, phase='requested', publishedSpreadsheetId=None, publishedGeneration=None))


def require_action_context(action: dict, scope, *, published=False) -> None:
    from attendance_binding import AttendanceBindingError
    if (action['subjectKey'] != scope.payload['subjectKey'] or action['expectedSchoolYear'] != scope.year
            or scope.payload['generation'] != action['expectedGeneration'] + (1 if published else 0)):
        raise AttendanceBindingError('ATTENDANCE_SCOPE_CHANGED')
    if published and (scope.payload.get('operationId') != action.get('operationId')
            or scope.payload.get('spreadsheetId') != action.get('publishedSpreadsheetId')):
        raise AttendanceBindingError('ATTENDANCE_SCOPE_CHANGED')


def correlate_attendance_operation(action: dict, operation: dict) -> dict:
    from attendance_binding import AttendanceBindingError
    if (not isinstance(operation, dict) or not operation.get('operationId')
            or action['subjectKey'] != operation.get('subjectKey')
            or action['expectedSchoolYear'] != operation.get('schoolYear')
            or action['expectedGeneration'] != operation.get('generation')
            or action['reason'] != operation.get('reason')
            or action.get('operationId') not in (None, operation['operationId'])
            or action.get('previousSpreadsheetId') != operation.get('previousSpreadsheetId')
            or action.get('previousOperationId') != operation.get('previousOperationId')
            or operation.get('state') == 'SUPERSEDED'):
        raise AttendanceBindingError('ATTENDANCE_SCOPE_CHANGED')
    if action['reason'] == 'replace-unavailable' and any(
            action.get(key) != operation.get(key) for key in ('explicitConfirmation', 'failureCode', 'failureStage')):
        raise AttendanceBindingError('ATTENDANCE_SCOPE_CHANGED')
    if action['provenance'] == 'saved-request' and hashlib.sha256(action['idempotencyKey'].encode()).hexdigest() != operation.get('idempotencyHash'):
        raise AttendanceBindingError('ATTENDANCE_OPERATION_IN_PROGRESS')
    return {**action, 'operationId': operation['operationId']}


def archive_attendance_setup(config_dir: Path) -> str:
    """Keep exact pre-migration bytes; never replace an existing history item."""
    source = Path(config_dir) / 'attendance-setup-status.generated.json'
    raw = attendance_references.read_bytes(source)
    digest = hashlib.sha256(raw).hexdigest()
    history = Path(config_dir) / 'attendance-preparation-history'
    history.mkdir(exist_ok=True)
    target = history / (digest + '.json')
    try:
        with target.open('xb') as stream: stream.write(attendance_references.protect(target, raw))
    except FileExistsError:
        if attendance_references.read_bytes(target) != raw:
            raise ValueError('출석부 준비 기록의 보관 결과를 확인하지 못했어요.')
    return digest


@dataclass
class TransitionDeps:
    installer: Callable
    write_record: Callable
    gws_executable: str
    runner: Callable | None = None
    account: str = ""
    chat_read_config: Callable | None = None
    chat_status: Callable | None = None
    chat_prepare_candidate: Callable | None = None
    chat_move: Callable | None = None
    binding_client: object = None


@dataclass(frozen=True)
class TransitionResult:
    state: str
    spreadsheet_url: str = ""
    detail: str = ""


class TransitionUserError(RuntimeError):
    """사용자에게 내부값을 보이지 않고 안전하게 멈추는 전환 오류."""


NEW_SCHOOL_YEAR_FAILURE = (
    "새 학년도 출석부를 시작하지 못했어요. 기존 출결 자료와 현재 연결은 "
    "바꾸지 않았습니다. Google 로그인과 인터넷 연결을 확인한 뒤 다시 시도해 주세요."
)


_INSTALL_PROGRESS_KEYS = frozenset(
    {
        "workbook_layout_ready",
        "template_doc_id",
        "template_doc_url",
        "spreadsheet_id",
        "spreadsheet_url",
        "folder_id",
        "task_list_id",
        "script_id",
        "deployment_id",
        install_attendance_automation._PENDING_TEMPLATE_INTENT,
        install_attendance_automation._PENDING_SHEET_INTENT,
        install_attendance_automation._PENDING_FOLDER_INTENT,
        install_attendance_automation._PENDING_TASK_TITLE,
        install_attendance_automation._PENDING_SCRIPT_TITLE,
        install_attendance_automation._PENDING_SCRIPT_UPLOAD_SHA256,
        install_attendance_automation._PENDING_SCRIPT_VERSION_DESCRIPTION,
        install_attendance_automation._PENDING_DEPLOYMENT_DESCRIPTION,
        install_attendance_automation._PENDING_DEPLOYMENT_VERSION,
    }
)
_INSTALL_PROGRESS_ID_KEYS = frozenset(
    {
        "template_doc_id",
        "spreadsheet_id",
        "folder_id",
        "task_list_id",
        "script_id",
        "deployment_id",
    }
)
_GOOGLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,200}$")


def make_transition_deps(*, runner, gws_executable: str, account: str) -> TransitionDeps:
    from dashboard import central_chat

    def run(args):
        return runner(args, None)

    return TransitionDeps(
        installer=install_attendance_automation.install_attendance_automation,
        write_record=install_attendance_automation.write_install_record,
        gws_executable=gws_executable,
        runner=runner,
        account=account,
        chat_read_config=lambda spreadsheet_id: central_chat.read_config_for_spreadsheet(
            spreadsheet_id, run, gws_executable=gws_executable
        ),
        chat_status=central_chat.status_for_config,
        chat_prepare_candidate=lambda candidate_id, original, inherited: (
            central_chat.prepare_handover_candidate(
                candidate_id, original, inherited, run, gws_executable
            )
        ),
        chat_move=central_chat.move_chat_connection,
    )


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"{key} 항목이 두 번 적혀 있습니다.")
        value[key] = item
    return value


def _read_dict(path: Path) -> dict:
    try:
        text = attendance_references.read_bytes(path).decode("utf-8")
    except UnicodeError as error:
        raise ValueError(f"{path.name} 내용을 UTF-8 글자로 읽지 못했습니다.") from error
    value = json.loads(text, object_pairs_hook=_strict_json_object)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} 내용이 자료 묶음이 아닙니다.")
    return value


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            file.write(attendance_references.protect(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")).decode("utf-8"))
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}-restore-", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(attendance_references.protect(path, value))
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _archive_record(record_path: Path) -> Path:
    archive_dir = record_path.parent / "attendance-archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = archive_dir / f"{record_path.stem}-{stamp}{record_path.suffix}"
    counter = 0
    while target.exists():
        counter += 1
        target = archive_dir / (
            f"{record_path.stem}-{stamp}-{counter}{record_path.suffix}"
        )
    _atomic_bytes(target, attendance_references.read_bytes(record_path))
    if attendance_references.read_bytes(target) != attendance_references.read_bytes(record_path):
        raise OSError("기존 출결 연결 기록 보관본을 다시 읽은 값이 다릅니다.")
    return target


def preserve_replacement_record(config_dir: Path) -> None:
    """Preserve connection and protection evidence before server retirement.

    Google roster and queues remain with the old full file ID. This operation
    never reads or copies their contents into the replacement's inputs.
    """
    for name in ("attendance-install.generated.json", "attendance-setup-status.generated.json"):
        source = Path(config_dir) / name
        if not source.exists():
            continue
        raw = attendance_references.read_bytes(source)
        target = Path(config_dir) / "attendance-replacement-history" / (source.stem + "-" + hashlib.sha256(raw).hexdigest() + ".json")
        if not target.exists():
            _atomic_bytes(target, raw)
        if attendance_references.read_bytes(target) != raw:
            raise OSError("기존 출석부 연결 기록의 보관본을 확인하지 못했습니다.")


def _valid_install_progress(value: object, previous_id: str) -> bool:
    if not isinstance(value, dict) or not set(value).issubset(_INSTALL_PROGRESS_KEYS):
        return False
    if any(
        not isinstance(item, str) or not item or len(item) > 500
        for item in value.values()
    ):
        return False
    for key in _INSTALL_PROGRESS_ID_KEYS:
        item = value.get(key)
        if item is not None and (
            _GOOGLE_ID_PATTERN.fullmatch(item) is None or item.startswith("AIza")
        ):
            return False
    spreadsheet_id = value.get("spreadsheet_id", "")
    if spreadsheet_id and spreadsheet_id == previous_id:
        return False
    spreadsheet_url = value.get("spreadsheet_url")
    expected_sheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
    if spreadsheet_url is not None and (
        not spreadsheet_id
        or not (
            spreadsheet_url == expected_sheet_url
            or spreadsheet_url.startswith(expected_sheet_url + "?")
            or spreadsheet_url.startswith(expected_sheet_url + "#")
        )
    ):
        return False
    template_id = value.get("template_doc_id", "")
    template_url = value.get("template_doc_url")
    expected_template_url = f"https://docs.google.com/document/d/{template_id}/edit"
    if template_url is not None and (
        not template_id
        or not (
            template_url == expected_template_url
            or template_url.startswith(expected_template_url + "?")
            or template_url.startswith(expected_template_url + "#")
        )
    ):
        return False
    intent_patterns = {
        install_attendance_automation._PENDING_TEMPLATE_INTENT: "template",
        install_attendance_automation._PENDING_SHEET_INTENT: "sheet",
        install_attendance_automation._PENDING_FOLDER_INTENT: "folder",
    }
    for key, kind in intent_patterns.items():
        item = value.get(key)
        if item is not None and re.fullmatch(f"{kind}:[0-9a-f]{{32}}", item) is None:
            return False
    pending_script_title = value.get(install_attendance_automation._PENDING_SCRIPT_TITLE)
    if pending_script_title is not None:
        suffix = install_attendance_automation._pending_script_title_suffix(
            pending_script_title
        )
        if re.fullmatch(r"[0-9a-f]{32}", suffix) is None:
            return False
    pending_version = value.get(
        install_attendance_automation._PENDING_SCRIPT_VERSION_DESCRIPTION
    )
    pending_upload = value.get(
        install_attendance_automation._PENDING_SCRIPT_UPLOAD_SHA256
    )
    if pending_upload is not None and re.fullmatch(r"[0-9a-f]{64}", pending_upload) is None:
        return False
    if pending_version is not None and re.fullmatch(
        r"teacher-manager-attendance-version-[0-9a-f]{32}", pending_version
    ) is None:
        return False
    pending_deployment = value.get(
        install_attendance_automation._PENDING_DEPLOYMENT_DESCRIPTION
    )
    pending_deployment_version = value.get(
        install_attendance_automation._PENDING_DEPLOYMENT_VERSION
    )
    if (pending_deployment is None) != (pending_deployment_version is None):
        return False
    if pending_deployment is not None and (
        re.fullmatch(
            r"teacher-manager-attendance-install-[0-9a-f]{32}",
            pending_deployment,
        )
        is None
        or re.fullmatch(r"[1-9][0-9]*", pending_deployment_version) is None
    ):
        return False
    return True


def _valid_new_school_year_state(value: Mapping[str, Any]) -> bool:
    if not isinstance(value, Mapping) or value.get("reason") != (
        install_attendance_automation.ATTENDANCE_CREATION_NEW_SCHOOL_YEAR
    ):
        return False
    state = value.get("state")
    required = {
        "state",
        "reason",
        "previous_spreadsheet_id",
        "school_year",
        "progress",
    }
    if state in {"candidate-verified", "complete"}:
        required |= {"spreadsheet_id", "spreadsheet_url"}
    elif state != "building":
        return False
    previous_id = value.get("previous_spreadsheet_id")
    progress = value.get("progress")
    if not (
        set(value).issubset(required | {"chat_handover"})
        and required.issubset(set(value))
        and isinstance(previous_id, str)
        and _GOOGLE_ID_PATTERN.fullmatch(previous_id) is not None
        and isinstance(value.get("school_year"), str)
        and re.fullmatch(r"20[0-9]{2}", value["school_year"]) is not None
        and _valid_install_progress(progress, previous_id)
    ):
        return False
    if state == "candidate-verified":
        candidate_id = value.get("spreadsheet_id")
        candidate_ok = bool(
            isinstance(candidate_id, str)
            and _GOOGLE_ID_PATTERN.fullmatch(candidate_id) is not None
            and candidate_id != previous_id
            and value.get("spreadsheet_url")
            == f"https://docs.google.com/spreadsheets/d/{candidate_id}/edit"
        )
        return candidate_ok and _valid_chat_handover(value.get("chat_handover"))
    if state == "complete":
        candidate_id = value.get("spreadsheet_id")
        return bool(
            isinstance(candidate_id, str)
            and _GOOGLE_ID_PATTERN.fullmatch(candidate_id) is not None
            and candidate_id != previous_id
            and value.get("spreadsheet_url")
            == f"https://docs.google.com/spreadsheets/d/{candidate_id}/edit"
            and _valid_chat_handover(value.get("chat_handover"))
        )
    return True


def _valid_chat_handover(value: object) -> bool:
    if value is None:
        return True
    required = {
        "state", "move_attempts", "source_settings_sha256",
        "target_settings_sha256", "source_route_sha256", "target_route_sha256",
    }
    optional = {"candidate_original_settings_sha256"}
    if not isinstance(value, Mapping) or not (
        required.issubset(set(value)) and set(value).issubset(required | optional)
    ):
        return False
    if value.get("state") not in {
        "not-needed", "prepared", "move-requested", "moved-verified", "complete"
    }:
        return False
    attempts = value.get("move_attempts")
    if not isinstance(attempts, int) or not 0 <= attempts <= 3:
        return False
    fingerprint_keys = (
        "source_settings_sha256", "target_settings_sha256",
        "source_route_sha256", "target_route_sha256",
    )
    if "candidate_original_settings_sha256" in value:
        fingerprint_keys += ("candidate_original_settings_sha256",)
    return all(
        isinstance(value.get(key), str)
        and re.fullmatch(r"[0-9a-f]{64}", value[key]) is not None
        for key in fingerprint_keys
    )


def _sha256_value(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _chat_config_shape(config: Mapping[str, Any], spreadsheet_id: str) -> bool:
    sheet_id = str(config.get("sheet_id", "") or "")
    return bool(
        str(config.get("spreadsheet_id", "") or "") == spreadsheet_id
        and str(config.get("url", "") or "").startswith("https://")
        and sheet_id.startswith(spreadsheet_id + ":")
        and len(sheet_id.split(":", 1)[1]) >= 3
        and str(config.get("sheet_secret", "") or "")
    )


def _inherited_chat_config(
    source: Mapping[str, Any], candidate: Mapping[str, Any], candidate_id: str
) -> dict:
    suffix = str(source["sheet_id"]).split(":", 1)[1]
    return {
        **dict(candidate),
        "url": str(source["url"]),
        "sheet_id": f"{candidate_id}:{suffix}",
        "sheet_secret": str(source["sheet_secret"]),
        "class_space_id": str(source.get("class_space_id", "") or ""),
        "class_space_name": str(source.get("class_space_name", "") or ""),
    }


def _chat_ledger(
    state: str, attempts: int, source: Mapping, target: Mapping,
    candidate_original: Mapping | None = None,
) -> dict:
    ledger = {
        "state": state,
        "move_attempts": attempts,
        "source_settings_sha256": _sha256_value({
            key: source.get(key, "")
            for key in ("url", "sheet_id", "sheet_secret", "class_space_id", "class_space_name")
        }),
        "target_settings_sha256": _sha256_value({
            key: target.get(key, "")
            for key in ("url", "sheet_id", "sheet_secret", "class_space_id", "class_space_name")
        }),
        "source_route_sha256": _sha256_value(source.get("sheet_id", "")),
        "target_route_sha256": _sha256_value(target.get("sheet_id", "")),
    }
    if candidate_original is not None:
        ledger["candidate_original_settings_sha256"] = _chat_settings_sha256(
            candidate_original
        )
    return ledger


def _chat_settings_sha256(config: Mapping) -> str:
    return _sha256_value({
        key: config.get(key, "")
        for key in ("url", "sheet_id", "sheet_secret", "class_space_id", "class_space_name")
    })


def _chat_move_verified(source_status: Mapping, target_status: Mapping,
                        source: Mapping, target: Mapping) -> bool:
    return bool(
        str(source_status.get("movedTo", "") or "") == target["sheet_id"]
        and str(target_status.get("movedFrom", "") or "") == source["sheet_id"]
        and str(target_status.get("account", "") or "").lower()
        == str(source_status.get("account", "") or "").lower()
        and is_goedu_email(target_status.get("account", ""))
        and str(source_status.get("classSpaceResource", "") or "")
        == str(source.get("class_space_id", "") or "")
        and str(target_status.get("classSpaceResource", "") or "")
        == str(source.get("class_space_id", "") or "")
    )


def _handover_chat_for_candidate(
    *, deps: TransitionDeps, source_id: str, candidate_id: str,
    transition_state: dict, state_path: Path,
) -> None:
    if not all((deps.chat_read_config, deps.chat_status,
                deps.chat_prepare_candidate, deps.chat_move)):
        return
    source = deps.chat_read_config(source_id)
    if not _chat_config_shape(source, source_id):
        raise TransitionUserError("기존 Chat 연결 설정을 안전하게 확인하지 못했어요.")
    existing = dict(transition_state.get("chat_handover") or {})
    candidate = deps.chat_read_config(candidate_id)
    if not _chat_config_shape(candidate, candidate_id):
        raise TransitionUserError("새 출석부의 Chat 연결 설정을 안전하게 확인하지 못했어요.")
    target = _inherited_chat_config(source, candidate, candidate_id)

    resume_candidate_prepare = False
    if existing.get("state") in {"prepared", "move-requested", "moved-verified"}:
        if (
            existing.get("source_settings_sha256") != _chat_settings_sha256(source)
            or existing.get("target_settings_sha256") != _chat_settings_sha256(target)
        ):
            raise TransitionUserError("저장된 Chat 이동 준비와 현재 설정이 달라 멈췄어요.")
        candidate_sha256 = _chat_settings_sha256(candidate)
        if candidate_sha256 != _chat_settings_sha256(target):
            if (
                existing.get("state") == "prepared"
                and existing.get("candidate_original_settings_sha256")
                == candidate_sha256
            ):
                resume_candidate_prepare = True
            else:
                raise TransitionUserError("저장된 Chat 이동 준비와 현재 설정이 달라 멈췄어요.")

    source_status = deps.chat_status(source)
    if existing.get("state") == "moved-verified":
        target_after = deps.chat_status(target)
        if not _chat_move_verified(source_status, target_after, source, target):
            raise TransitionUserError("옮긴 Chat 연결을 다시 확인하지 못했어요.")
        return
    if existing.get("state") == "move-requested":
        target_after = deps.chat_status(target)
        if _chat_move_verified(source_status, target_after, source, target):
            transition_state["chat_handover"] = _chat_ledger(
                "moved-verified", int(existing.get("move_attempts", 0) or 0),
                source, target,
            )
            _atomic_json(state_path, transition_state)
            return
    connected = bool(source_status.get("connected"))
    source_account = str(source_status.get("account", "") or "").lower()
    if not connected or not is_goedu_email(source_account):
        if existing.get("state") in {"prepared", "move-requested"}:
            raise TransitionUserError("Chat 연결 이동 상태가 서로 달라 자동으로 바꾸지 않았어요.")
        transition_state["chat_handover"] = _chat_ledger(
            "not-needed", int(existing.get("move_attempts", 0) or 0), source, target
        )
        _atomic_json(state_path, transition_state)
        return

    if resume_candidate_prepare:
        original_status = deps.chat_status(candidate)
        target_status = deps.chat_status(target)
        if any(
            status.get(key)
            for status in (original_status, target_status)
            for key in ("registered", "connected", "movedFrom", "movedTo")
        ):
            raise TransitionUserError("새 출석부의 Chat 연결 대상이 이미 사용 중이에요.")
        candidate = deps.chat_prepare_candidate(candidate_id, candidate, target)
        if _chat_settings_sha256(candidate) != _chat_settings_sha256(target):
            raise TransitionUserError("새 출석부의 Chat 설정 저장을 다시 확인하지 못했어요.")

    if existing.get("state") not in {"prepared", "move-requested"}:
        if str(candidate.get("class_space_id", "") or "") or str(
            candidate.get("class_space_name", "") or ""
        ):
            raise TransitionUserError("새 출석부에 다른 Chat 방 선택이 있어 바꾸지 않았어요.")
        original_status = deps.chat_status(candidate)
        target_status = deps.chat_status(target)
        if any(
            status.get(key)
            for status in (original_status, target_status)
            for key in ("registered", "connected", "movedFrom", "movedTo")
        ):
            raise TransitionUserError("새 출석부의 Chat 연결 대상이 이미 사용 중이에요.")
        transition_state["chat_handover"] = _chat_ledger(
            "prepared", 0, source, target, candidate_original=candidate
        )
        _atomic_json(state_path, transition_state)
        candidate = deps.chat_prepare_candidate(candidate_id, candidate, target)
        if any(str(candidate.get(key, "") or "") != str(target.get(key, "") or "")
               for key in ("url", "sheet_id", "sheet_secret", "class_space_id", "class_space_name")):
            raise TransitionUserError("새 출석부의 Chat 설정 저장을 다시 확인하지 못했어요.")

    attempts = int(existing.get("move_attempts", 0) or 0)
    source_before = deps.chat_status(source)
    target_before = deps.chat_status(target)
    if _chat_move_verified(source_before, target_before, source, target):
        transition_state["chat_handover"] = _chat_ledger(
            "moved-verified", attempts, source, target
        )
        _atomic_json(state_path, transition_state)
        return
    if attempts >= 3:
        raise TransitionUserError("Chat 연결을 세 번 확인했지만 옮기지 못했어요.")
    if not (
        source_before.get("connected")
        and str(source_before.get("account", "") or "").lower() == source_account
        and str(source_before.get("classSpaceResource", "") or "")
        == str(source.get("class_space_id", "") or "")
        and not target_before.get("registered")
        and not target_before.get("movedFrom")
        and not target_before.get("movedTo")
    ):
        raise TransitionUserError("Chat 연결 양쪽 상태가 달라 자동으로 옮기지 않았어요.")
    attempts += 1
    transition_state["chat_handover"] = _chat_ledger(
        "move-requested", attempts, source, target
    )
    _atomic_json(state_path, transition_state)
    deps.chat_move(source, target["sheet_id"])
    source_after = deps.chat_status(source)
    target_after = deps.chat_status(target)
    if not _chat_move_verified(source_after, target_after, source, target):
        raise TransitionUserError("Chat 연결을 옮긴 결과를 확인하지 못했어요.")
    transition_state["chat_handover"] = _chat_ledger(
        "moved-verified", attempts, source, target
    )
    _atomic_json(state_path, transition_state)


def _candidate_ok(result, profile: dict, current_id: str) -> bool:
    candidate_id = str(getattr(result, "spreadsheet_id", "") or "").strip()
    bundle_sha256 = str(
        getattr(result, "script_bundle_sha256", "") or ""
    ).strip().lower()
    return bool(
        candidate_id
        and candidate_id != current_id
        and str(getattr(result, "spreadsheet_url", "") or "").startswith(
            f"https://docs.google.com/spreadsheets/d/{candidate_id}/edit"
        )
        and str(getattr(result, "script_id", "") or "").strip()
        and str(getattr(result, "deployment_id", "") or "").strip()
        and str(getattr(result, "workbook_name", "") or "")
        == attendance_workbook_identity.attendance_workbook_name(profile)
        and len(bundle_sha256) == 64
        and all(character in "0123456789abcdef" for character in bundle_sha256)
    )


def _switch_record_last(
    record_path: Path,
    profile_path: Path,
    result,
    *,
    write_record: Callable,
) -> None:
    previous = attendance_references.read_bytes(record_path) if record_path.exists() else None
    candidate_id = str(getattr(result, "spreadsheet_id", "") or "").strip()
    if previous is not None:
        _archive_record(record_path)
    try:
        write_record(profile_path, result)
        switched = _read_dict(record_path)
        if str(switched.get("spreadsheet_id", "") or "") != candidate_id:
            raise OSError("새 출결 연결 기록을 다시 읽은 번호가 후보와 다릅니다.")
    except Exception:
        if previous is None:
            record_path.unlink(missing_ok=True)
        else:
            _atomic_bytes(record_path, previous)
        raise


def start_new_school_year_workbook(config_dir: Path, *, deps: TransitionDeps) -> TransitionResult:
    """Compatibility entry point routed through the same registry admission."""
    if deps.binding_client is None:
        return TransitionResult(state="verification-unavailable", detail="현재 계정과 학년도의 출석부 연결을 먼저 확인해야 해요. 기존 자료는 그대로입니다.")
    from dashboard import engine
    result = engine.start_new_attendance(Path(config_dir), deps=engine.AttendanceDeps(
        attendance_installer=deps.installer, write_record=deps.write_record,
        attendance_runner=deps.runner, gws_resolver=lambda: deps.gws_executable,
        binding_client=deps.binding_client,
    ))
    return TransitionResult(state="complete" if result.created else result.state,
                            spreadsheet_url=result.spreadsheet_url, detail=result.detail)


__all__ = [
    "NEW_SCHOOL_YEAR_FAILURE",
    "TransitionDeps",
    "TransitionResult",
    "make_transition_deps",
    "start_new_school_year_workbook",
]
