"""검증을 마친 출결 사본으로 로컬 설치 연결만 안전하게 바꾼다."""

from __future__ import annotations

import copy
import json
import math
import os
import re
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from attendance_install_record import (  # noqa: E402
    AttendanceInstallRecordError,
    InstallRecordSnapshot,
    ensure_create_only_install_backup,
    load_attendance_install_record,
    read_attendance_install_snapshot,
    replace_attendance_install_record,
    validate_attendance_install_record,
)
from brity_bridge import paths  # noqa: E402
from verify_attendance_access import (  # noqa: E402
    AttendanceAccessCheckResult,
    _check_attendance_access_once,
)


ALLOWED_CENTRAL_OUTCOMES = {"not_registered", "moved"}
KNOWN_SWITCH_STATES = {
    "not_started",
    "central_checking",
    "central_check_required",
    "central_ready",
    "complete",
}
CHANGED_CONNECTION_FIELDS = {
    "spreadsheet_id",
    "spreadsheet_url",
    "script_id",
    "deployment_id",
}
RESERVED_SWITCH_PROOF_FIELDS = {
    "connection_switch_old_sha256",
    "connection_switch_script_id",
    "connection_switch_deployment_id",
    "central_outcome",
}
COPY_SCRIPT_ID_FIELD = "copy_script_id"
COPY_SCRIPT_VERSION_FIELD = "copy_script_version_number"
COPY_SCRIPT_DEPLOYMENT_FIELD = "copy_script_deployment_id"
COPY_SCRIPT_BUNDLE_FIELD = "copy_script_bundle_sha256"
COPY_SCRIPT_EVIDENCE_FIELDS = (
    COPY_SCRIPT_ID_FIELD,
    COPY_SCRIPT_VERSION_FIELD,
    COPY_SCRIPT_DEPLOYMENT_FIELD,
    COPY_SCRIPT_BUNDLE_FIELD,
)
PREPARED_COPY_SCRIPT_STATE = "ready_for_task5"


@dataclass(frozen=True)
class AttendanceScriptCheckResult:
    verified: bool
    parent_spreadsheet_id: str
    script_id: str
    deployment_id: str


@dataclass(frozen=True)
class CopyScriptRecordResult:
    state: str
    detail: str
    copy_spreadsheet_id: str
    script_id: str
    deployment_id: str
    version_number: int


@dataclass(frozen=True)
class CentralTransitionResult:
    outcome: str
    account: str
    source_spreadsheet_id: str
    copy_spreadsheet_id: str


@dataclass(frozen=True)
class ConnectionSwitchResult:
    state: str
    detail: str
    local_connection_switched: bool


class AttendanceConnectionSwitchHold(ValueError):
    """확인되지 않은 상태를 자동으로 고치지 않고 멈출 때 발생한다."""

    code = "ATTENDANCE_CONNECTION_SWITCH_HOLD"


AccessCheck = Callable[[Path], AttendanceAccessCheckResult]
ScriptCheck = Callable[..., AttendanceScriptCheckResult]
CentralTransition = Callable[..., CentralTransitionResult]
StateWriter = Callable[[Path, dict[str, Any]], Any]
RecordWriter = Callable[
    [Path, dict[str, Any], InstallRecordSnapshot],
    Any,
]
LockFactory = Callable[[Path], Any]


def _hold(message: str, *, cause: Exception | None = None):
    error = AttendanceConnectionSwitchHold(
        "ATTENDANCE_CONNECTION_SWITCH_HOLD: "
        + str(message).strip()
        + " 자동으로 고치거나 연결을 바꾸지 않았습니다."
    )
    if cause is not None:
        error.__cause__ = cause
    raise error


def _need(condition: Any, message: str) -> None:
    if not condition:
        _hold(message)


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _hold(f"사본 진행 기록의 {key} 항목이 두 번 적혀 있습니다.")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    _hold(f"사본 진행 기록에 JSON에서 쓸 수 없는 숫자가 있습니다: {value}")


def _check_json_value(value: Any, location: str = "사본 진행 기록") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            _need(isinstance(key, str), f"{location}의 항목 이름이 글자가 아닙니다.")
            _check_json_value(nested, f"{location}.{key}")
        return
    if isinstance(value, list):
        for index, nested in enumerate(value):
            _check_json_value(nested, f"{location}[{index}]")
        return
    if isinstance(value, float):
        _need(math.isfinite(value), f"{location}에 NaN이나 무한대가 있습니다.")
    _need(
        value is None or isinstance(value, (str, int, float, bool)),
        f"{location}에 JSON으로 저장할 수 없는 값이 있습니다.",
    )


def _load_copy_status(path: Path) -> dict[str, Any]:
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        _hold("사본 진행 기록을 끝까지 읽지 못했습니다.", cause=exc)
    try:
        parsed = json.loads(
            raw,
            object_pairs_hook=_strict_pairs,
            parse_constant=_reject_constant,
        )
    except AttendanceConnectionSwitchHold:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        _hold("사본 진행 기록 JSON이 잘렸거나 형식이 다릅니다.", cause=exc)
    _need(isinstance(parsed, dict), "사본 진행 기록의 바깥 모양이 객체가 아닙니다.")
    _check_json_value(parsed)
    return parsed


def _atomic_copy_status(path: Path, value: dict[str, Any]) -> dict[str, Any]:
    path = Path(path)
    _need(isinstance(value, dict), "새 사본 진행 기록의 바깥 모양이 다릅니다.")
    _check_json_value(value, "새 사본 진행 기록")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            prefix=f".{path.name}-",
            suffix=".tmp",
            dir=str(path.parent),
        )
    except OSError as exc:
        _hold("사본 진행 기록 옆 임시 파일을 만들지 못했습니다.", cause=exc)

    temporary = Path(name)
    try:
        try:
            with os.fdopen(
                descriptor,
                "w",
                encoding="utf-8",
                newline="\n",
            ) as file:
                file.write(
                    json.dumps(
                        value,
                        ensure_ascii=False,
                        indent=2,
                        allow_nan=False,
                    )
                    + "\n"
                )
                file.flush()
                os.fsync(file.fileno())
        except (OSError, TypeError, ValueError) as exc:
            _hold("사본 진행 기록 임시 파일을 끝까지 쓰지 못했습니다.", cause=exc)

        _need(
            _load_copy_status(temporary) == value,
            "사본 진행 기록 임시 파일을 다시 읽은 값이 다릅니다.",
        )
        try:
            os.replace(temporary, path)
        except OSError as exc:
            _hold("사본 진행 기록을 한 번에 교체하지 못했습니다.", cause=exc)
        return value
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _write_status(
    path: Path,
    value: dict[str, Any],
    writer: StateWriter,
) -> dict[str, Any]:
    wanted = copy.deepcopy(value)
    try:
        writer(Path(path), copy.deepcopy(wanted))
    except AttendanceConnectionSwitchHold:
        raise
    except Exception as exc:
        _hold("사본 진행 기록을 안전하게 저장하지 못했습니다.", cause=exc)
    checked = _load_copy_status(path)
    _need(
        checked == wanted,
        "저장 뒤 사본 진행 기록이 쓰려던 값과 다릅니다.",
    )
    return checked


def _strict_text(value: Any, label: str) -> str:
    _need(isinstance(value, str), f"{label}이 글자가 아닙니다.")
    clean = value.strip()
    _need(bool(clean) and clean == value, f"{label}이 비었거나 앞뒤가 흐립니다.")
    return clean


def _strict_email(value: Any, label: str) -> str:
    email = _strict_text(value, label)
    _need(
        email.count("@") == 1 and not email.startswith("@") and not email.endswith("@"),
        f"{label}의 이메일 모양이 다릅니다.",
    )
    return email.casefold()


def _sheet_id_from_url(value: Any, label: str) -> str:
    url = _strict_text(value, label)
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        _hold(f"{label}을 읽지 못했습니다.", cause=exc)
    _need(parsed.scheme == "https", f"{label}이 https 주소가 아닙니다.")
    _need(
        (parsed.hostname or "").casefold() == "docs.google.com",
        f"{label}이 Google Sheet 주소가 아닙니다.",
    )
    parts = [item for item in parsed.path.split("/") if item]
    _need(
        len(parts) >= 3 and parts[0] == "spreadsheets" and parts[1] == "d",
        f"{label}에서 Sheet ID를 찾지 못했습니다.",
    )
    return _strict_text(parts[2], f"{label}의 Sheet ID")


def _strict_positive_int(value: Any, label: str) -> int:
    _need(
        isinstance(value, int) and not isinstance(value, bool) and value > 0,
        f"{label}이 1 이상의 정수가 아닙니다.",
    )
    return int(value)


def _strict_sha256(value: Any, label: str) -> str:
    text = _strict_text(value, label)
    _need(
        re.fullmatch(r"[0-9a-f]{64}", text) is not None,
        f"{label}의 SHA-256 모양이 다릅니다.",
    )
    return text


def _default_assets_dir() -> Path:
    return SCRIPTS_DIR.parent / "assets"


def _copy_script_evidence(
    status: dict[str, Any],
    *,
    copy_spreadsheet_id: str,
) -> dict[str, Any]:
    """사본 진행 기록에 남은 새 Sheet·Script·버전·배포·코드 지문을 엄격하게 읽는다."""

    missing = sorted(
        field for field in COPY_SCRIPT_EVIDENCE_FIELDS if field not in status
    )
    _need(
        not missing,
        "사본 진행 기록에 새 Apps Script 확인값이 없습니다: " + ", ".join(missing),
    )
    recorded_copy_id = _strict_text(
        status.get("copy_spreadsheet_id"),
        "사본 진행 기록의 사본 Sheet ID",
    )
    _need(
        recorded_copy_id == _strict_text(copy_spreadsheet_id, "이번 사본 Sheet ID"),
        "사본 진행 기록의 사본 Sheet ID가 이번 대상과 다릅니다.",
    )
    return {
        "copy_spreadsheet_id": recorded_copy_id,
        "script_id": _strict_text(
            status.get(COPY_SCRIPT_ID_FIELD),
            "사본 진행 기록의 새 Apps Script ID",
        ),
        "version_number": _strict_positive_int(
            status.get(COPY_SCRIPT_VERSION_FIELD),
            "사본 진행 기록의 새 Apps Script 버전 번호",
        ),
        "deployment_id": _strict_text(
            status.get(COPY_SCRIPT_DEPLOYMENT_FIELD),
            "사본 진행 기록의 새 배포 ID",
        ),
        "bundle_sha256": _strict_sha256(
            status.get(COPY_SCRIPT_BUNDLE_FIELD),
            "사본 진행 기록의 새 Code.gs 묶음 지문",
        ),
    }


def _default_script_verifier(
    *,
    copy_spreadsheet_id: str,
    script_id: str,
    version_number: int,
    deployment_id: str,
    bundle_sha256: str,
    assets_dir: Path,
):
    from prepare_attendance_copy_script import verify_prepared_copied_script

    return verify_prepared_copied_script(
        copy_spreadsheet_id,
        script_id,
        version_number=version_number,
        deployment_id=deployment_id,
        bundle_sha256=bundle_sha256,
        assets_dir=Path(assets_dir),
    )


def _verified_copy_script(
    evidence: dict[str, Any],
    *,
    verifier: Callable[..., Any],
    assets_dir: Path,
) -> AttendanceScriptCheckResult:
    """읽기 전용 재확인이 정확히 맞을 때만 확인 결과를 돌려준다."""

    try:
        checked = verifier(
            copy_spreadsheet_id=evidence["copy_spreadsheet_id"],
            script_id=evidence["script_id"],
            version_number=evidence["version_number"],
            deployment_id=evidence["deployment_id"],
            bundle_sha256=evidence["bundle_sha256"],
            assets_dir=Path(assets_dir),
        )
    except AttendanceConnectionSwitchHold:
        raise
    except Exception as exc:
        _hold("사본 Apps Script 재확인을 끝내지 못했습니다.", cause=exc)
    _need(
        getattr(checked, "state", None) == PREPARED_COPY_SCRIPT_STATE
        and getattr(checked, "verified", None) is True,
        "사본 Apps Script 재확인 결과가 명시적인 통과가 아닙니다.",
    )
    _need(
        getattr(checked, "copy_spreadsheet_id", None)
        == evidence["copy_spreadsheet_id"]
        and getattr(checked, "copy_script_id", None) == evidence["script_id"]
        and getattr(checked, "version_number", None) == evidence["version_number"]
        and getattr(checked, "deployment_id", None) == evidence["deployment_id"]
        and getattr(checked, "bundle_sha256", None) == evidence["bundle_sha256"],
        "사본 Apps Script 재확인 결과가 기록된 값과 정확히 같지 않습니다.",
    )
    return AttendanceScriptCheckResult(
        verified=True,
        parent_spreadsheet_id=evidence["copy_spreadsheet_id"],
        script_id=evidence["script_id"],
        deployment_id=evidence["deployment_id"],
    )


def _default_access_check(config_dir: Path) -> AttendanceAccessCheckResult:
    return _check_attendance_access_once(config_dir)


def _default_script_check(
    *,
    config_dir: Path,
    copy_spreadsheet_id: str,
    script_id: str,
    deployment_id: str,
    script_verifier: Callable[..., Any] | None = None,
    assets_dir: Path | None = None,
) -> AttendanceScriptCheckResult:
    """사본 진행 기록의 확인값으로 parent·HEAD·버전·배포를 다시 읽어 대조한다."""

    status_path = paths.attendance_copy_status_path(Path(config_dir))
    evidence = _copy_script_evidence(
        _load_copy_status(status_path),
        copy_spreadsheet_id=copy_spreadsheet_id,
    )
    _need(
        evidence["script_id"] == _strict_text(script_id, "이번 새 Apps Script ID"),
        "사본 진행 기록의 새 Apps Script ID가 이번 값과 다릅니다.",
    )
    _need(
        evidence["deployment_id"] == _strict_text(deployment_id, "이번 새 배포 ID"),
        "사본 진행 기록의 새 배포 ID가 이번 값과 다릅니다.",
    )
    return _verified_copy_script(
        evidence,
        verifier=script_verifier or _default_script_verifier,
        assets_dir=Path(assets_dir) if assets_dir else _default_assets_dir(),
    )


def _default_central_transition(
    *,
    config_dir: Path,
    account: str,
    source_spreadsheet_id: str,
    copy_spreadsheet_id: str,
    mover: Callable[..., Any] | None = None,
) -> CentralTransitionResult:
    """등록·연결이 실제로 있는 사용자만 기존 발송 연결을 사본으로 한 번 옮긴다."""

    if mover is None:
        from attendance_central_move import move_central_chat_connection

        mover = move_central_chat_connection

    moved = mover(
        Path(config_dir),
        account=account,
        source_spreadsheet_id=source_spreadsheet_id,
        copy_spreadsheet_id=copy_spreadsheet_id,
    )
    _need(
        getattr(moved, "outcome", None) in ALLOWED_CENTRAL_OUTCOMES,
        "중앙 연결 이동 결과가 not_registered 또는 moved가 아닙니다.",
    )
    return CentralTransitionResult(
        outcome=moved.outcome,
        account=_strict_email(moved.account, "중앙 연결 이동 계정"),
        source_spreadsheet_id=_strict_text(
            moved.source_spreadsheet_id,
            "중앙 연결 이동의 원본 Sheet ID",
        ),
        copy_spreadsheet_id=_strict_text(
            moved.copy_spreadsheet_id,
            "중앙 연결 이동의 사본 Sheet ID",
        ),
    )


def _default_lock_factory(config_dir: Path):
    from dashboard.engine import attendance_setup_lock

    return attendance_setup_lock(config_dir)


def _default_record_writer(
    path: Path,
    record: dict[str, Any],
    expected: InstallRecordSnapshot,
) -> InstallRecordSnapshot:
    return replace_attendance_install_record(path, record, expected)


def _switch_state(status: dict[str, Any]) -> str:
    raw = status.get("connection_switch_state", "not_started")
    state = _strict_text(raw, "연결 변경 상태")
    _need(state in KNOWN_SWITCH_STATES, "알 수 없는 연결 변경 상태입니다.")
    return state


def _validate_task3_status(
    status: dict[str, Any],
    install: dict[str, Any],
    *,
    new_script_id: str,
    new_deployment_id: str,
) -> tuple[str, str, str, str]:
    _need(status.get("state") == "complete", "사본 준비가 완료 상태가 아닙니다.")
    account = _strict_email(status.get("account"), "사본을 만든 계정")
    _need(
        status.get("code_gs_hold") is False,
        "사본의 새 Code.gs 확인 차단이 아직 풀리지 않았습니다.",
    )
    _need(
        type(status.get("local_connection_switched")) is bool,
        "로컬 연결 변경 여부가 실제 참/거짓이 아닙니다.",
    )

    source_id = _strict_text(
        status.get("source_spreadsheet_id"),
        "사본 상태의 원본 Sheet ID",
    )
    installed_source_id = _strict_text(
        install.get("spreadsheet_id"),
        "설치 기록의 원본 Sheet ID",
    )
    _need(
        source_id == installed_source_id,
        "사본 상태와 설치 기록의 원본 Sheet ID가 다릅니다.",
    )
    _need(
        _sheet_id_from_url(
            install.get("spreadsheet_url"),
            "설치 기록의 원본 Sheet 주소",
        )
        == source_id,
        "원본 Sheet 주소와 ID가 다릅니다.",
    )

    copy_id = _strict_text(
        status.get("copy_spreadsheet_id"),
        "사본 Sheet ID",
    )
    _need(copy_id != source_id, "원본과 사본 Sheet ID가 같습니다.")
    copy_url = _strict_text(
        status.get("copy_spreadsheet_url"),
        "사본 Sheet 주소",
    )
    _need(
        _sheet_id_from_url(copy_url, "사본 Sheet 주소") == copy_id,
        "사본 Sheet 주소와 ID가 다릅니다.",
    )

    script_id = _strict_text(new_script_id, "새 Apps Script ID")
    deployment_id = _strict_text(new_deployment_id, "새 배포 ID")
    _need(
        script_id != _strict_text(install.get("script_id"), "기존 Apps Script ID"),
        "새 Apps Script ID가 원본 ID와 같습니다.",
    )
    _need(
        deployment_id
        != _strict_text(install.get("deployment_id"), "기존 배포 ID"),
        "새 배포 ID가 원본 ID와 같습니다.",
    )
    return account, source_id, copy_id, copy_url


def _new_record(
    old_record: dict[str, Any],
    *,
    copy_id: str,
    copy_url: str,
    new_script_id: str,
    new_deployment_id: str,
) -> dict[str, Any]:
    updated = copy.deepcopy(old_record)
    updated.update(
        {
            "spreadsheet_id": copy_id,
            "spreadsheet_url": copy_url,
            "script_id": new_script_id,
            "deployment_id": new_deployment_id,
        }
    )
    return validate_attendance_install_record(updated)


def _only_four_fields_changed(
    old_record: dict[str, Any],
    new_record: dict[str, Any],
) -> bool:
    if set(old_record) != set(new_record):
        return False
    for key in old_record:
        if key in CHANGED_CONNECTION_FIELDS:
            continue
        if old_record[key] != new_record[key]:
            return False
    return True


def _validate_saved_switch_values(
    status: dict[str, Any],
    *,
    backup: InstallRecordSnapshot,
    new_script_id: str,
    new_deployment_id: str,
) -> None:
    _need(
        status.get("connection_switch_old_sha256") == backup.sha256,
        "저장된 전환 전 설치 기록 지문이 현재 백업과 다릅니다.",
    )
    _need(
        status.get("connection_switch_script_id") == new_script_id,
        "저장된 새 Apps Script ID가 이번 값과 다릅니다.",
    )
    _need(
        status.get("connection_switch_deployment_id") == new_deployment_id,
        "저장된 새 배포 ID가 이번 값과 다릅니다.",
    )


def _state_with(
    status: dict[str, Any],
    state: str,
    *,
    backup: InstallRecordSnapshot,
    new_script_id: str,
    new_deployment_id: str,
    central_outcome: str | None = None,
    complete: bool = False,
) -> dict[str, Any]:
    updated = copy.deepcopy(status)
    updated.update(
        {
            "connection_switch_state": state,
            "connection_switch_old_sha256": backup.sha256,
            "connection_switch_script_id": new_script_id,
            "connection_switch_deployment_id": new_deployment_id,
            "local_connection_switched": bool(complete),
        }
    )
    if central_outcome is not None:
        updated["central_outcome"] = central_outcome
    elif state in {"central_checking", "central_check_required"}:
        updated.pop("central_outcome", None)
    return updated


def _result_complete() -> ConnectionSwitchResult:
    return ConnectionSwitchResult(
        state="complete",
        detail="새 출결 사본 연결을 로컬 설치 기록에 안전하게 반영했습니다.",
        local_connection_switched=True,
    )


def _validate_complete_or_ready(
    status: dict[str, Any],
    *,
    state: str,
    backup: InstallRecordSnapshot,
    active: InstallRecordSnapshot,
    new_record: dict[str, Any],
    new_script_id: str,
    new_deployment_id: str,
) -> str:
    _validate_saved_switch_values(
        status,
        backup=backup,
        new_script_id=new_script_id,
        new_deployment_id=new_deployment_id,
    )
    outcome = _strict_text(status.get("central_outcome"), "중앙 확인 결과")
    _need(
        outcome in ALLOWED_CENTRAL_OUTCOMES,
        "중앙 확인 결과가 확실한 두 상태 중 하나가 아닙니다.",
    )
    if state == "complete":
        _need(
            status.get("local_connection_switched") is True,
            "완료 상태와 로컬 연결 변경 표시가 다릅니다.",
        )
        _need(
            active.record == new_record,
            "완료된 활성 설치 기록이 정확한 새 연결값과 다릅니다.",
        )
        return "complete"

    _need(
        status.get("local_connection_switched") is False,
        "중앙 확인 완료 상태에서 로컬 연결 표시가 먼저 바뀌었습니다.",
    )
    if active.record == new_record:
        return "new"
    _need(
        active.raw == backup.raw,
        "활성 설치 기록이 전환 전 원본과 정확한 새 기록 중 어느 쪽도 아닙니다.",
    )
    return "old"


def _verify_access_and_script(
    config_dir: Path,
    *,
    account: str,
    source_id: str,
    copy_id: str,
    new_script_id: str,
    new_deployment_id: str,
    access_check: AccessCheck,
    script_check: ScriptCheck,
) -> None:
    try:
        access = access_check(config_dir)
    except Exception as exc:
        _hold("Task 6 계정·권한 확인을 통과하지 못했습니다.", cause=exc)
    _need(
        isinstance(access, AttendanceAccessCheckResult),
        "Task 6 확인 결과의 모양이 다릅니다.",
    )
    _need(
        access.state == "ready" and access.verified is True,
        "Task 6 확인 결과가 명시적인 통과가 아닙니다.",
    )
    _need(
        _strict_email(access.account, "Task 6 확인 계정") == account,
        "Task 6 계정이 사본을 만든 계정과 다릅니다.",
    )
    _need(
        access.source_spreadsheet_id == source_id,
        "Task 6 원본 Sheet ID가 이번 원본과 다릅니다.",
    )
    _need(
        access.copy_spreadsheet_id == copy_id,
        "Task 6 사본 Sheet ID가 이번 사본과 다릅니다.",
    )

    try:
        script = script_check(
            config_dir=config_dir,
            copy_spreadsheet_id=copy_id,
            script_id=new_script_id,
            deployment_id=new_deployment_id,
        )
    except AttendanceConnectionSwitchHold:
        raise
    except Exception as exc:
        _hold("사본 Apps Script와 배포를 확인하지 못했습니다.", cause=exc)
    _need(
        isinstance(script, AttendanceScriptCheckResult),
        "사본 Apps Script 확인 결과의 모양이 다릅니다.",
    )
    _need(
        script.verified is True
        and script.parent_spreadsheet_id == copy_id
        and script.script_id == new_script_id
        and script.deployment_id == new_deployment_id,
        "사본 parent와 새 script/deployment를 정확히 확인하지 못했습니다.",
    )


def _switch_attendance_connection_once(
    config_dir: Path,
    *,
    new_script_id: str,
    new_deployment_id: str,
    access_check: AccessCheck,
    script_check: ScriptCheck,
    central_transition: CentralTransition,
    state_writer: StateWriter,
    record_writer: RecordWriter,
) -> ConnectionSwitchResult:
    config_dir = Path(config_dir)
    install_path = paths.attendance_install_record_path(config_dir)
    backup_path = paths.attendance_install_backup_path(config_dir)
    status_path = paths.attendance_copy_status_path(config_dir)

    try:
        active = read_attendance_install_snapshot(install_path)
    except AttendanceInstallRecordError as exc:
        _hold("활성 출결 설치 기록을 엄격하게 읽지 못했습니다.", cause=exc)
    status = _load_copy_status(status_path)
    state = _switch_state(status)

    if state == "not_started":
        leftover_proof = sorted(
            key for key in RESERVED_SWITCH_PROOF_FIELDS if key in status
        )
        _need(
            not leftover_proof,
            "시작 전 상태에 앞선 연결 변경의 예약 증거가 남아 있습니다.",
        )
        account, source_id, copy_id, copy_url = _validate_task3_status(
            status,
            active.record,
            new_script_id=new_script_id,
            new_deployment_id=new_deployment_id,
        )
        _need(
            status.get("local_connection_switched") is False,
            "첫 전환 전에 로컬 연결 표시가 이미 바뀌었습니다.",
        )
        try:
            backup = ensure_create_only_install_backup(backup_path, active)
        except AttendanceInstallRecordError as exc:
            _hold("전환 전 설치 기록의 고정 백업을 확인하지 못했습니다.", cause=exc)
    else:
        try:
            backup = read_attendance_install_snapshot(backup_path)
        except AttendanceInstallRecordError as exc:
            _hold("기존 고정 백업을 엄격하게 읽지 못했습니다.", cause=exc)
        account, source_id, copy_id, copy_url = _validate_task3_status(
            status,
            backup.record,
            new_script_id=new_script_id,
            new_deployment_id=new_deployment_id,
        )

    old_record = backup.record
    _need(
        _strict_text(old_record.get("spreadsheet_id"), "백업의 원본 Sheet ID")
        == source_id,
        "고정 백업이 이번 원본 Sheet를 가리키지 않습니다.",
    )
    new_record = _new_record(
        old_record,
        copy_id=copy_id,
        copy_url=copy_url,
        new_script_id=new_script_id,
        new_deployment_id=new_deployment_id,
    )
    _need(
        _only_four_fields_changed(old_record, new_record),
        "허용한 네 연결값 밖의 기존 설정이 달라졌습니다.",
    )

    if state == "complete":
        _validate_complete_or_ready(
            status,
            state=state,
            backup=backup,
            active=active,
            new_record=new_record,
            new_script_id=new_script_id,
            new_deployment_id=new_deployment_id,
        )
        return _result_complete()

    if state in {"central_checking", "central_check_required"}:
        _validate_saved_switch_values(
            status,
            backup=backup,
            new_script_id=new_script_id,
            new_deployment_id=new_deployment_id,
        )
        _need(
            status.get("local_connection_switched") is False,
            "중앙 수동 확인 상태에서 로컬 연결 표시가 바뀌었습니다.",
        )
        _need(
            active.raw == backup.raw,
            "중앙 확인 대기 중 활성 설치 기록이 전환 전 백업과 달라졌습니다.",
        )
        _hold("앞선 중앙 확인 결과가 불명확해 사람이 먼저 확인해야 합니다.")

    if state == "central_ready":
        active_kind = _validate_complete_or_ready(
            status,
            state=state,
            backup=backup,
            active=active,
            new_record=new_record,
            new_script_id=new_script_id,
            new_deployment_id=new_deployment_id,
        )
        outcome = _strict_text(status.get("central_outcome"), "중앙 확인 결과")
        if active_kind == "new":
            completed = _state_with(
                status,
                "complete",
                backup=backup,
                new_script_id=new_script_id,
                new_deployment_id=new_deployment_id,
                central_outcome=outcome,
                complete=True,
            )
            _write_status(status_path, completed, state_writer)
            return _result_complete()
        _verify_access_and_script(
            config_dir,
            account=account,
            source_id=source_id,
            copy_id=copy_id,
            new_script_id=new_script_id,
            new_deployment_id=new_deployment_id,
            access_check=access_check,
            script_check=script_check,
        )
    else:
        _verify_access_and_script(
            config_dir,
            account=account,
            source_id=source_id,
            copy_id=copy_id,
            new_script_id=new_script_id,
            new_deployment_id=new_deployment_id,
            access_check=access_check,
            script_check=script_check,
        )

        checking = _state_with(
            status,
            "central_checking",
            backup=backup,
            new_script_id=new_script_id,
            new_deployment_id=new_deployment_id,
        )
        status = _write_status(status_path, checking, state_writer)
        try:
            central = central_transition(
                config_dir=config_dir,
                account=account,
                source_spreadsheet_id=source_id,
                copy_spreadsheet_id=copy_id,
            )
            _need(
                isinstance(central, CentralTransitionResult),
                "중앙 확인 결과의 모양이 다릅니다.",
            )
            _need(
                central.outcome in ALLOWED_CENTRAL_OUTCOMES,
                "중앙 결과가 not_registered 또는 moved가 아닙니다.",
            )
            _need(
                _strict_email(central.account, "중앙 확인 계정") == account,
                "중앙 확인 계정이 이번 계정과 다릅니다.",
            )
            _need(
                central.source_spreadsheet_id == source_id
                and central.copy_spreadsheet_id == copy_id,
                "중앙 확인의 원본·사본 Sheet ID가 이번 대상과 다릅니다.",
            )
        except Exception as exc:
            required = _state_with(
                status,
                "central_check_required",
                backup=backup,
                new_script_id=new_script_id,
                new_deployment_id=new_deployment_id,
            )
            _write_status(status_path, required, state_writer)
            _hold(
                "중앙 연결 확인 결과가 불명확해 자동 재시도를 막았습니다.",
                cause=exc,
            )

        ready = _state_with(
            status,
            "central_ready",
            backup=backup,
            new_script_id=new_script_id,
            new_deployment_id=new_deployment_id,
            central_outcome=central.outcome,
        )
        status = _write_status(status_path, ready, state_writer)

    try:
        current = read_attendance_install_snapshot(install_path)
    except AttendanceInstallRecordError as exc:
        _hold("로컬 교체 직전 활성 설치 기록을 다시 읽지 못했습니다.", cause=exc)
    _need(
        current.raw == backup.raw,
        "로컬 교체 직전 활성 설치 기록이 처음 읽은 원본과 달라졌습니다.",
    )
    try:
        record_writer(
            install_path,
            copy.deepcopy(new_record),
            copy.deepcopy(current),
        )
    except Exception as exc:
        _hold(
            "중앙 확인은 끝났지만 로컬 설치 기록 교체를 완료하지 못했습니다.",
            cause=exc,
        )

    try:
        final = read_attendance_install_snapshot(install_path)
    except AttendanceInstallRecordError as exc:
        _hold("교체 뒤 활성 설치 기록을 다시 읽지 못했습니다.", cause=exc)
    _need(
        final.record == new_record
        and _only_four_fields_changed(backup.record, final.record),
        "교체 뒤 허용한 네 연결값 밖의 값이 달라졌습니다.",
    )

    outcome = _strict_text(status.get("central_outcome"), "중앙 확인 결과")
    completed = _state_with(
        status,
        "complete",
        backup=backup,
        new_script_id=new_script_id,
        new_deployment_id=new_deployment_id,
        central_outcome=outcome,
        complete=True,
    )
    _write_status(status_path, completed, state_writer)
    return _result_complete()


def _record_prepared_copy_script_once(
    config_dir: Path,
    *,
    copy_spreadsheet_id: str,
    copy_script_id: str,
    version_number: Any,
    deployment_id: str,
    bundle_sha256: str,
    script_verifier: Callable[..., Any],
    assets_dir: Path,
    state_writer: StateWriter,
) -> CopyScriptRecordResult:
    status_path = paths.attendance_copy_status_path(Path(config_dir))
    status = _load_copy_status(status_path)

    _need(status.get("state") == "complete", "사본 준비가 완료 상태가 아닙니다.")
    _need(
        _switch_state(status) == "not_started",
        "연결 변경이 이미 시작된 뒤에는 새 Code.gs 확인을 기록하지 않습니다.",
    )
    leftover_proof = sorted(
        key for key in RESERVED_SWITCH_PROOF_FIELDS if key in status
    )
    _need(
        not leftover_proof,
        "시작 전 상태에 앞선 연결 변경의 예약 증거가 남아 있습니다.",
    )
    _need(
        status.get("local_connection_switched") is False,
        "로컬 연결이 이미 바뀐 기록에는 새 Code.gs 확인을 남기지 않습니다.",
    )

    copy_id = _strict_text(
        status.get("copy_spreadsheet_id"),
        "사본 진행 기록의 사본 Sheet ID",
    )
    _need(
        copy_id == _strict_text(copy_spreadsheet_id, "이번 사본 Sheet ID"),
        "사본 진행 기록의 사본 Sheet ID가 이번 대상과 다릅니다.",
    )
    _need(
        copy_id
        != _strict_text(
            status.get("source_spreadsheet_id"),
            "사본 진행 기록의 원본 Sheet ID",
        ),
        "원본과 사본 Sheet ID가 같습니다.",
    )

    wanted = {
        "copy_spreadsheet_id": copy_id,
        "script_id": _strict_text(copy_script_id, "새 Apps Script ID"),
        "version_number": _strict_positive_int(
            version_number,
            "새 Apps Script 버전 번호",
        ),
        "deployment_id": _strict_text(deployment_id, "새 배포 ID"),
        "bundle_sha256": _strict_sha256(bundle_sha256, "새 Code.gs 묶음 지문"),
    }
    recorded = [field for field in COPY_SCRIPT_EVIDENCE_FIELDS if field in status]
    if recorded:
        _need(
            len(recorded) == len(COPY_SCRIPT_EVIDENCE_FIELDS),
            "사본 진행 기록의 새 Apps Script 확인값이 일부만 남아 있습니다.",
        )
        _need(
            _copy_script_evidence(status, copy_spreadsheet_id=copy_id) == wanted,
            "이미 기록된 새 Apps Script 확인값이 이번 값과 다릅니다.",
        )

    _verified_copy_script(
        wanted,
        verifier=script_verifier,
        assets_dir=Path(assets_dir),
    )

    updated = copy.deepcopy(status)
    updated.update(
        {
            COPY_SCRIPT_ID_FIELD: wanted["script_id"],
            COPY_SCRIPT_VERSION_FIELD: wanted["version_number"],
            COPY_SCRIPT_DEPLOYMENT_FIELD: wanted["deployment_id"],
            COPY_SCRIPT_BUNDLE_FIELD: wanted["bundle_sha256"],
            "code_gs_hold": False,
        }
    )
    if updated != status:
        _write_status(status_path, updated, state_writer)
    return CopyScriptRecordResult(
        state="recorded",
        detail=(
            "사본에 올린 새 Code.gs와 배포를 다시 읽어 확인하고 "
            "사본 진행 기록에 남겼습니다."
        ),
        copy_spreadsheet_id=copy_id,
        script_id=wanted["script_id"],
        deployment_id=wanted["deployment_id"],
        version_number=wanted["version_number"],
    )


def record_prepared_copy_script(
    config_dir: Path,
    *,
    copy_spreadsheet_id: str,
    copy_script_id: str,
    version_number: Any,
    deployment_id: str,
    bundle_sha256: str,
    script_verifier: Callable[..., Any] | None = None,
    assets_dir: Path | None = None,
    state_writer: StateWriter = _atomic_copy_status,
    lock_factory: LockFactory = _default_lock_factory,
) -> CopyScriptRecordResult:
    """사본에 올린 새 Code.gs를 다시 읽어 확인한 뒤에만 옛 Code.gs 보류를 푼다."""

    config_dir = Path(config_dir)
    with lock_factory(config_dir):
        return _record_prepared_copy_script_once(
            config_dir,
            copy_spreadsheet_id=copy_spreadsheet_id,
            copy_script_id=copy_script_id,
            version_number=version_number,
            deployment_id=deployment_id,
            bundle_sha256=bundle_sha256,
            script_verifier=script_verifier or _default_script_verifier,
            assets_dir=Path(assets_dir) if assets_dir else _default_assets_dir(),
            state_writer=state_writer,
        )


def switch_attendance_connection(
    config_dir: Path,
    *,
    new_script_id: str,
    new_deployment_id: str,
    access_check: AccessCheck = _default_access_check,
    script_check: ScriptCheck = _default_script_check,
    central_transition: CentralTransition = _default_central_transition,
    state_writer: StateWriter = _atomic_copy_status,
    record_writer: RecordWriter = _default_record_writer,
    lock_factory: LockFactory = _default_lock_factory,
) -> ConnectionSwitchResult:
    """같은 출결 잠금 안에서 중앙 확인 뒤 로컬 연결을 한 번만 바꾼다."""

    config_dir = Path(config_dir)
    with lock_factory(config_dir):
        return _switch_attendance_connection_once(
            config_dir,
            new_script_id=new_script_id,
            new_deployment_id=new_deployment_id,
            access_check=access_check,
            script_check=script_check,
            central_transition=central_transition,
            state_writer=state_writer,
            record_writer=record_writer,
        )


__all__ = [
    "AttendanceAccessCheckResult",
    "AttendanceConnectionSwitchHold",
    "AttendanceScriptCheckResult",
    "CentralTransitionResult",
    "ConnectionSwitchResult",
    "CopyScriptRecordResult",
    "_switch_attendance_connection_once",
    "load_attendance_install_record",
    "record_prepared_copy_script",
    "switch_attendance_connection",
]
