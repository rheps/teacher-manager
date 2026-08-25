"""출결 후보를 끝까지 확인한 뒤 현재 연결을 마지막에 바꾼다."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

import install_attendance_automation
import attendance_ai_setup
import attendance_canonical_rebuild
import attendance_central_move
import attendance_script_update
import attendance_workbook_identity
from attendance_install_record import CONNECTION_FIELDS, load_attendance_install_record


@dataclass
class TransitionDeps:
    source_finder: Callable
    installer: Callable
    write_record: Callable
    central_mover: Callable
    gws_executable: str
    runner: Callable | None = None
    account: str = ""
    consent_checker: Callable | None = None
    preview_builder: Callable | None = None
    record_migrator: Callable | None = None
    resource_verifier: Callable | None = None
    candidate_finalizer: Callable | None = None
    candidate_verifier: Callable | None = None
    ai_inspector: Callable | None = None
    central_route_reader: Callable | None = None
    central_rollback: Callable | None = None
    trash_workbook: Callable | None = None


@dataclass(frozen=True)
class TransitionResult:
    state: str
    spreadsheet_url: str = ""
    moved_row_counts: tuple[int, ...] = ()
    trigger_count: int = 0
    trashed_count: int = 0
    remaining_cleanup_count: int = 0


@dataclass(frozen=True)
class ConsolidationCheckpoint:
    """Task 5가 검증한 내부 재개 상태. 화면 응답으로 내보내지 않는다."""

    state: str
    fingerprint: str
    candidate_spreadsheet_id: str
    spreadsheet_url: str
    approved_cleanup_ids: tuple[str, ...]
    remaining_cleanup_ids: tuple[str, ...]
    moved_row_counts: tuple[int, ...]
    trigger_count: int
    total_cleanup_count: int


@dataclass(frozen=True)
class ResumableTransitionStatus:
    state: str
    fingerprint: str = ""
    spreadsheet_url: str = ""


class TransitionUserError(RuntimeError):
    """Only fixed, user-readable transition guidance may use this exception."""


CONSOLIDATION_FAILURE = (
    "출결 파일을 하나로 정리하지 못했어요. 기존 출결 자료와 현재 연결은 바꾸지 않았습니다. "
    "Google 로그인과 인터넷 연결을 확인한 뒤 다시 시도해 주세요."
)
NEW_SCHOOL_YEAR_FAILURE = (
    "새 학년도 출석부를 시작하지 못했어요. 기존 출결 자료와 현재 연결은 바꾸지 않았습니다. "
    "Google 로그인과 인터넷 연결을 확인한 뒤 다시 시도해 주세요."
)


def _candidate_names(candidates) -> list[str]:
    names = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        name = " ".join(str(candidate.get("name", "") or "").split())[:120]
        if name and name not in names:
            names.append(name)
    return names


def find_split_repair_sources(profile, runner, workdir: Path, gws_executable: str):
    """AI가 쓰던 고정 이름을 우선하고, 없을 때만 잘못 갈린 이름을 본다."""

    legacy = install_attendance_automation.find_existing_attendance_sheets(
        runner,
        Path(workdir),
        False,
        install_attendance_automation.ATTENDANCE_LEGACY_SHEET_NAME,
        gws_executable,
    )
    if legacy:
        return legacy
    return install_attendance_automation.find_legacy_attendance_sheets(
        runner,
        Path(workdir),
        False,
        (
            attendance_workbook_identity.legacy_year_workbook_name(profile),
            attendance_workbook_identity.previous_attendance_workbook_name(profile),
        ),
        gws_executable,
    )


def _settings_map(rows: list[list[str]]) -> dict[str, str]:
    values: dict[str, str] = {}
    for row in rows:
        if len(row) < 2:
            continue
        key = str(row[0]).strip()
        if key:
            if key in values:
                raise ValueError(f"출결 설정에 같은 키가 두 번 있습니다: {key}")
            values[key] = str(row[1]).strip()
    return values


def move_central_for_transition(
    *,
    config_dir: Path,
    source_spreadsheet_id: str,
    destination_spreadsheet_id: str,
    runner,
    gws_executable: str,
    account: str,
):
    """원본에 실제 중앙 Chat 등록값이 있을 때만 후보로 안전하게 옮긴다."""

    source_rows = install_attendance_automation._read_existing_setting_rows(
        runner,
        Path(config_dir),
        source_spreadsheet_id,
        gws_executable,
    )
    source = _settings_map(source_rows)
    central_keys = (
        "CENTRAL_CHAT_SENDER_URL",
        "CENTRAL_CHAT_SHEET_ID",
        "CENTRAL_CHAT_SHEET_SECRET",
    )
    present = [bool(source.get(key)) for key in central_keys]
    if not any(present):
        return True
    if not all(present):
        raise ValueError("원본의 Google Chat 연결값이 일부만 있어 자동으로 옮기지 않았어요.")

    def read_rows(spreadsheet_id: str):
        return install_attendance_automation._read_existing_setting_rows(
            runner,
            Path(config_dir),
            spreadsheet_id,
            gws_executable,
        )

    def read_config(_config_dir: Path):
        return {
            "spreadsheet_id": source_spreadsheet_id,
            "url": source["CENTRAL_CHAT_SENDER_URL"],
            "sheet_id": source["CENTRAL_CHAT_SHEET_ID"],
            "sheet_secret": source["CENTRAL_CHAT_SHEET_SECRET"],
        }

    def update_setting(spreadsheet_id: str, rows: list, key: str, value: str):
        from dashboard import central_chat

        return central_chat._update_settings_value(
            spreadsheet_id,
            rows,
            key,
            value,
            lambda args: runner(args, Path(config_dir)),
            gws_executable=gws_executable,
        )

    return attendance_central_move.move_central_chat_connection(
        Path(config_dir),
        account=account,
        source_spreadsheet_id=source_spreadsheet_id,
        candidate_spreadsheet_id=destination_spreadsheet_id,
        read_config=read_config,
        read_rows=read_rows,
        update_setting=update_setting,
    )


def rollback_central_for_transition(
    *,
    config_dir: Path,
    source_spreadsheet_id: str,
    candidate_spreadsheet_id: str,
    runner,
    gws_executable: str,
    account: str,
) -> bool:
    """원본 설정의 확인값을 다시 읽어 중앙 발송 경로만 원본으로 되돌린다."""

    source_rows = install_attendance_automation._read_existing_setting_rows(
        runner,
        Path(config_dir),
        source_spreadsheet_id,
        gws_executable,
    )
    source = _settings_map(source_rows)
    keys = (
        "CENTRAL_CHAT_SENDER_URL",
        "CENTRAL_CHAT_SHEET_ID",
        "CENTRAL_CHAT_SHEET_SECRET",
    )
    present = [bool(source.get(key)) for key in keys]
    if not any(present):
        return True
    if not all(present):
        return False

    def read_config(_config_dir: Path):
        return {
            "spreadsheet_id": source_spreadsheet_id,
            "url": source["CENTRAL_CHAT_SENDER_URL"],
            "sheet_id": source["CENTRAL_CHAT_SHEET_ID"],
            "sheet_secret": source["CENTRAL_CHAT_SHEET_SECRET"],
        }

    return attendance_central_move.rollback_central_chat_connection(
        Path(config_dir),
        account=account,
        source_spreadsheet_id=source_spreadsheet_id,
        candidate_spreadsheet_id=candidate_spreadsheet_id,
        read_config=read_config,
    )


def inspect_central_for_transition(
    *,
    config_dir: Path,
    source_spreadsheet_id: str,
    candidate_spreadsheet_id: str,
    runner,
    gws_executable: str,
    account: str,
):
    """중앙 경로를 바꾸지 않고 원본·후보·미등록 상태만 다시 읽는다."""

    source_rows = install_attendance_automation._read_existing_setting_rows(
        runner,
        Path(config_dir),
        source_spreadsheet_id,
        gws_executable,
    )
    source = _settings_map(source_rows)
    keys = (
        "CENTRAL_CHAT_SENDER_URL",
        "CENTRAL_CHAT_SHEET_ID",
        "CENTRAL_CHAT_SHEET_SECRET",
    )
    present = [bool(source.get(key)) for key in keys]
    if not any(present):
        return attendance_central_move.CentralRouteResult("not_registered")
    if not all(present):
        raise attendance_central_move.AttendanceCentralMoveHold(
            "중앙 발송 설정의 일부만 있어 경로를 확인하지 못했습니다."
        )

    def read_config(_config_dir: Path):
        return {
            "spreadsheet_id": source_spreadsheet_id,
            "url": source["CENTRAL_CHAT_SENDER_URL"],
            "sheet_id": source["CENTRAL_CHAT_SHEET_ID"],
            "sheet_secret": source["CENTRAL_CHAT_SHEET_SECRET"],
        }

    return attendance_central_move.inspect_central_chat_route(
        Path(config_dir),
        account=account,
        source_spreadsheet_id=source_spreadsheet_id,
        candidate_spreadsheet_id=candidate_spreadsheet_id,
        read_config=read_config,
    )


def _has_scope_consent(*, config_dir: Path, account: str) -> bool:
    # dashboard.engine가 이 모듈을 불러오므로 실행 시점에만 역방향으로 읽는다.
    from dashboard import engine

    return engine.has_current_gws_scope_grant(Path(config_dir), account)


def make_transition_deps(*, runner, gws_executable: str, account: str) -> TransitionDeps:
    return TransitionDeps(
        source_finder=find_split_repair_sources,
        installer=install_attendance_automation.install_attendance_automation,
        write_record=install_attendance_automation.write_install_record,
        central_mover=lambda **kwargs: move_central_for_transition(
            **kwargs, account=account
        ),
        gws_executable=gws_executable,
        runner=runner,
        account=account,
        consent_checker=_has_scope_consent,
        preview_builder=attendance_canonical_rebuild.build_consolidation_preview,
        record_migrator=migrate_attendance_records,
        resource_verifier=verify_attendance_resources,
        candidate_finalizer=(
            attendance_canonical_rebuild.finalize_rebuilt_workbook_name
        ),
        candidate_verifier=verify_final_candidate_name,
        ai_inspector=attendance_ai_setup.inspect_attendance_ai_setup,
        central_route_reader=lambda **kwargs: inspect_central_for_transition(
            **kwargs, account=account
        ),
        central_rollback=lambda **kwargs: rollback_central_for_transition(
            **kwargs, account=account
        ),
        trash_workbook=trash_attendance_workbook,
    )


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"{key} 항목이 두 번 적혀 있습니다.")
        value[key] = item
    return value


def _read_dict(path: Path) -> dict:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_strict_json_object,
    )
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
            file.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


_CONSOLIDATION_PROGRESS_KEYS = frozenset({"stage", "fingerprint", "sources"})
_CONSOLIDATION_SOURCE_KEYS = frozenset(
    {
        "source_spreadsheet_id",
        "sheet_name",
        "row_count",
        "appended_range",
        "sha256",
        "trashed",
        "write_state",
    }
)
_CONSOLIDATION_SOURCE_KEYS_LEGACY = _CONSOLIDATION_SOURCE_KEYS - {"write_state"}
_CONSOLIDATION_STAGES = frozenset(
    {
        "preview",
        "sources-read",
        "building",
        "candidate-created",
        "authoritative-copied",
        "records",
        "records-complete",
        "ai-setup",
        "candidate-verified",
        "record-switched",
        "trash",
        "cleanup-required",
        "complete",
    }
)
_BUILDING_RECORD_STAGES = frozenset({"preview", "records", "records-complete"})
_INSTALL_PROGRESS_KEYS = frozenset(
    {
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
_APPENDED_RANGE_PATTERN = re.compile(
    r"^(?:'?(?:[1-9]|1[0-2])월'?|'?메신저 개인톡 내용'?|"
    r"'?메신저 단체톡 내용'?|'?발송기록'?)!A[1-9][0-9]*:[A-M][1-9][0-9]*$"
)


def _is_sha256(value) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_install_progress(value: object, approved_ids: tuple[str, ...]) -> bool:
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
    template_id = value.get("template_doc_id", "")
    if spreadsheet_id and spreadsheet_id in approved_ids:
        return False
    spreadsheet_url = value.get("spreadsheet_url")
    expected_spreadsheet_url = (
        f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
    )
    if spreadsheet_url is not None and (
        not spreadsheet_id
        or not (
            spreadsheet_url == expected_spreadsheet_url
            or spreadsheet_url.startswith(expected_spreadsheet_url + "?")
            or spreadsheet_url.startswith(expected_spreadsheet_url + "#")
        )
    ):
        return False
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
    pending_script_title = value.get(
        install_attendance_automation._PENDING_SCRIPT_TITLE
    )
    if pending_script_title is not None:
        suffix = install_attendance_automation._pending_script_title_suffix(
            pending_script_title
        )
        if re.fullmatch(r"[0-9a-f]{32}", suffix) is None:
            return False
    pending_version = value.get(
        install_attendance_automation._PENDING_SCRIPT_VERSION_DESCRIPTION
    )
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


def _validate_consolidation_progress(value) -> None:
    """진행 재개에 필요한 고정 구조 외에는 로컬 기록에 넣지 않는다."""

    if not isinstance(value, dict) or set(value) != _CONSOLIDATION_PROGRESS_KEYS:
        raise ValueError("출결 통합 진행 기록에 허용되지 않은 항목이 있습니다.")
    if value["stage"] not in _CONSOLIDATION_STAGES:
        raise ValueError("출결 통합 진행 단계를 확인할 수 없습니다.")
    if not _is_sha256(value["fingerprint"]):
        raise ValueError("출결 통합 진행 기록 지문을 확인할 수 없습니다.")
    sources = value["sources"]
    if not isinstance(sources, list):
        raise ValueError("출결 통합 원본 진행 기록을 확인할 수 없습니다.")
    allowed_sheet_names = {
        *(f"{month}월" for month in range(1, 13)),
        "개인톡",
        "단체톡",
        "발송기록",
    }
    for source in sources:
        if not isinstance(source, dict) or set(source) not in {
            _CONSOLIDATION_SOURCE_KEYS,
            _CONSOLIDATION_SOURCE_KEYS_LEGACY,
        }:
            raise ValueError("출결 통합 원본 진행 기록을 확인할 수 없습니다.")
        source_id = source["source_spreadsheet_id"]
        if not (
            isinstance(source_id, str)
            and _GOOGLE_ID_PATTERN.fullmatch(source_id) is not None
            and not source_id.startswith("AIza")
        ):
            raise ValueError("출결 통합 원본 번호를 확인할 수 없습니다.")
        if source["sheet_name"] not in allowed_sheet_names:
            raise ValueError("출결 통합 탭 이름을 확인할 수 없습니다.")
        row_count = source["row_count"]
        if not (
            isinstance(row_count, int)
            and not isinstance(row_count, bool)
            and row_count >= 0
        ):
            raise ValueError("출결 통합 줄 수를 확인할 수 없습니다.")
        appended_range = source["appended_range"]
        if not (
            isinstance(appended_range, str)
            and _APPENDED_RANGE_PATTERN.fullmatch(appended_range) is not None
        ):
            raise ValueError("출결 통합 붙인 범위를 확인할 수 없습니다.")
        if not _is_sha256(source["sha256"]):
            raise ValueError("출결 통합 원본 지문을 확인할 수 없습니다.")
        if not isinstance(source["trashed"], bool):
            raise ValueError("출결 통합 휴지통 상태를 확인할 수 없습니다.")
        if source.get("write_state", "planned") not in {"planned", "confirmed"}:
            raise ValueError("출결 통합 쓰기 상태를 확인할 수 없습니다.")


def write_consolidation_progress(config_dir: Path, progress: dict) -> Path:
    """안전한 재개 메타데이터만 진행 파일에 원자적으로 기록한다."""

    if not isinstance(progress, dict):
        raise ValueError("출결 통합 진행 기록이 자료 묶음이 아닙니다.")
    _validate_consolidation_progress(progress)
    path = Path(config_dir) / "attendance-workbook-transition.generated.json"
    _atomic_json(path, progress)
    return path


def trash_attendance_workbook(
    *,
    runner,
    workdir: Path,
    gws_executable: str,
    spreadsheet_id: str,
    approved_spreadsheet_ids,
    candidate_spreadsheet_id: str,
) -> bool:
    """미리보기로 고정한 옛 Sheet만 휴지통으로 옮기고 즉시 다시 읽는다."""

    target = str(spreadsheet_id or "").strip()
    candidate = str(candidate_spreadsheet_id or "").strip()
    approved = tuple(str(value or "").strip() for value in approved_spreadsheet_ids)
    if not (
        callable(runner)
        and target
        and candidate
        and target != candidate
        and target in approved
        and len(set(approved)) == len(approved)
        and all(_GOOGLE_ID_PATTERN.fullmatch(value) is not None for value in approved)
        and _GOOGLE_ID_PATTERN.fullmatch(candidate) is not None
    ):
        raise ValueError("휴지통으로 옮길 이전 출석부 번호를 확인하지 못했습니다.")
    updated = attendance_canonical_rebuild._run_json(
        runner,
        [
            str(gws_executable),
            "drive",
            "files",
            "update",
            "--params",
            json.dumps(
                {"fileId": target, "fields": "id,trashed"},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "--json",
            json.dumps({"trashed": True}, separators=(",", ":")),
            "--format",
            "json",
        ],
        Path(workdir),
    )
    if not (
        isinstance(updated, dict)
        and updated.get("id") == target
        and updated.get("trashed") is True
    ):
        raise RuntimeError("이전 출석부의 휴지통 이동 결과를 확인하지 못했습니다.")
    checked = attendance_canonical_rebuild._run_json(
        runner,
        [
            str(gws_executable),
            "drive",
            "files",
            "get",
            "--params",
            json.dumps(
                {"fileId": target, "fields": "id,trashed"},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "--format",
            "json",
        ],
        Path(workdir),
    )
    if not (
        isinstance(checked, dict)
        and checked.get("id") == target
        and checked.get("trashed") is True
    ):
        raise RuntimeError("휴지통으로 옮긴 이전 출석부를 다시 확인하지 못했습니다.")
    return True


def _atomic_bytes(path: Path, value: bytes) -> None:
    """기존 연결 기록을 같은 폴더의 임시 파일로 완성한 뒤 되돌린다."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}-restore-", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(value)
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
    shutil.copy2(record_path, target)
    if target.read_bytes() != record_path.read_bytes():
        raise OSError("기존 출결 연결 기록 보관본을 다시 읽은 값이 다릅니다.")
    return target


def _candidate_ok(result, profile: dict, source_id: str, current_id: str) -> bool:
    candidate_id = str(getattr(result, "spreadsheet_id", "") or "").strip()
    bundle_sha256 = str(
        getattr(result, "script_bundle_sha256", "") or ""
    ).strip().lower()
    expected_name = attendance_workbook_identity.attendance_workbook_name(profile)
    return bool(
        candidate_id
        and candidate_id not in {source_id, current_id}
        and str(getattr(result, "spreadsheet_url", "") or "").startswith(
            "https://docs.google.com/spreadsheets/d/"
        )
        and str(getattr(result, "script_id", "") or "").strip()
        and str(getattr(result, "deployment_id", "") or "").strip()
        and str(getattr(result, "workbook_name", "") or "") == expected_name
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
    """후보가 끝난 뒤 기록만 교체하며, 교체 오류면 이전 바이트로 되돌린다."""

    previous = record_path.read_bytes() if record_path.exists() else None
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
            # 이 호출 전에 없던 첫 기록만 치운다. Google 원본과 후보는 건드리지 않는다.
            record_path.unlink(missing_ok=True)
        else:
            _atomic_bytes(record_path, previous)
        raise


def _preview_source_ids(preview) -> tuple[str, ...]:
    sources = getattr(preview, "sources", ())
    if not isinstance(sources, tuple):
        raise TransitionUserError("정리할 출석부 목록을 다시 확인하지 못했어요.")
    values = tuple(
        str(getattr(source, "spreadsheet_id", "") or "").strip()
        for source in sources
    )
    if (
        not values
        or len(set(values)) != len(values)
        or any(_GOOGLE_ID_PATTERN.fullmatch(value) is None for value in values)
    ):
        raise TransitionUserError("정리할 출석부 목록을 다시 확인하지 못했어요.")
    return values


def migrate_attendance_records(
    *,
    config_dir: Path,
    runner,
    gws_executable: str,
    account: str,
    profile: Mapping[str, Any],
    current_record: Mapping[str, Any],
    source_spreadsheet_id: str,
    destination_spreadsheet_id: str,
    preview,
    install_result,
    fingerprint: str,
    progress: dict[str, object],
    remember: Callable[[dict[str, object]], None],
    verify_only: bool = False,
) -> dict[str, object]:
    """재확인한 불변 기록 묶음만 후보에 붙이고 기준 자료와 전체 줄을 대조한다."""

    try:
        config_dir = Path(config_dir)
        source_ids = _preview_source_ids(preview)
        if source_spreadsheet_id not in source_ids:
            raise attendance_canonical_rebuild.CanonicalRebuildHold(
                "현재 연결이 정리 원본 목록에 없습니다."
            )
        sources = tuple(getattr(preview, "sources"))
        blocks = tuple(
            block
            for source in sources
            for block in attendance_canonical_rebuild._read_record_blocks(
                source=source,
                runner=runner,
                workdir=config_dir,
                gws_executable=gws_executable,
            )
        )

        title = attendance_workbook_identity.attendance_workbook_name(dict(profile))
        rediscovered = attendance_canonical_rebuild._discover_sources(
            runner=runner,
            workdir=config_dir,
            gws_executable=gws_executable,
            account=account,
            title=title,
            current_spreadsheet_id=source_spreadsheet_id,
        )
        if rediscovered != sources:
            raise attendance_canonical_rebuild.CanonicalRebuildHold(
                "정리할 출석부 목록이 확인 뒤 달라졌습니다."
            )
        inspected = attendance_script_update.inspect_attendance_script_update(
            source_spreadsheet_id,
            str(current_record.get("script_id", "") or ""),
            str(current_record.get("deployment_id", "") or ""),
            assets_dir=Path(__file__).resolve().parent.parent / "assets",
            runner=runner,
            gws_executable=gws_executable,
        )
        script_sha256 = str(getattr(inspected, "current_bundle_sha256", "") or "")
        if not (
            getattr(inspected, "state", "") == "current"
            and getattr(inspected, "verified", False) is True
            and _is_sha256(script_sha256)
        ):
            raise attendance_canonical_rebuild.CanonicalRebuildHold(
                "현재 자동 기능을 다시 확인하지 못했습니다."
            )
        recalculated = attendance_canonical_rebuild._fingerprint(
            account=account,
            current_spreadsheet_id=source_spreadsheet_id,
            title=title,
            sources=sources,
            blocks=blocks,
            current_script_sha256=script_sha256,
        )
        if recalculated != fingerprint:
            raise attendance_canonical_rebuild.CanonicalRebuildHold(
                "확인한 출결 기록 지문이 실행 직전에 달라졌습니다."
            )

        authority_ranges = {
            range_name: attendance_canonical_rebuild._read_rows(
                runner=runner,
                workdir=config_dir,
                gws_executable=gws_executable,
                spreadsheet_id=source_spreadsheet_id,
                range_name=range_name,
                column_count=width,
            )
            for range_name, width in attendance_canonical_rebuild.AUTHORITATIVE_RANGES.items()
        }
        current_source = next(
            source for source in sources if source.spreadsheet_id == source_spreadsheet_id
        )
        source_settings = attendance_canonical_rebuild._read_settings(
            source=current_source,
            runner=runner,
            workdir=config_dir,
            gws_executable=gws_executable,
        )
        candidate_source = attendance_canonical_rebuild.SourceWorkbook(
            spreadsheet_id=destination_spreadsheet_id,
            name=str(getattr(install_result, "workbook_name", "") or ""),
            created_time="1970-01-01T00:00:00Z",
            modified_time="1970-01-01T00:00:00Z",
        )
        candidate_settings = attendance_canonical_rebuild._read_settings(
            source=candidate_source,
            runner=runner,
            workdir=config_dir,
            gws_executable=gws_executable,
        )
        candidate_central_id = str(
            candidate_settings.get("CENTRAL_CHAT_SHEET_ID", "") or ""
        ).strip()
        if not candidate_central_id.startswith(destination_spreadsheet_id + ":"):
            raise attendance_canonical_rebuild.CanonicalRebuildHold(
                "후보의 중앙 발송 연결번호를 확인하지 못했습니다."
            )
        expected_settings = dict(source_settings)
        expected_settings.update(
            {
                "TEMPLATE_DOC_ID": str(install_result.template_doc_id),
                "DEST_FOLDER_ID": str(install_result.folder_id),
                "TASK_LIST_ID": str(install_result.task_list_id),
                "SCRIPT_ID": str(install_result.script_id),
                "DEPLOYMENT_ID": str(install_result.deployment_id),
                "CENTRAL_CHAT_SHEET_ID": candidate_central_id,
                "GEMINI_API_KEY": str(
                    source_settings.get("GEMINI_API_KEY", "")
                    or install_attendance_automation.local_gemini_api_key(config_dir)
                ).strip(),
                "ATTENDANCE_AI_ALLOWED": "예",
            }
        )
        authoritative_snapshot = {
            "ranges": authority_ranges,
            "settings": expected_settings,
        }
        if verify_only is True:
            migrated = dict(progress)
        else:
            migrated = attendance_canonical_rebuild.append_record_blocks(
                runner=runner,
                workdir=config_dir,
                gws_executable=gws_executable,
                destination_spreadsheet_id=destination_spreadsheet_id,
                blocks=blocks,
                progress=progress,
                remember=remember,
            )
        verified = attendance_canonical_rebuild.verify_rebuilt_workbook(
            runner=runner,
            workdir=config_dir,
            gws_executable=gws_executable,
            destination_spreadsheet_id=destination_spreadsheet_id,
            authoritative_snapshot=authoritative_snapshot,
            blocks=blocks,
            progress=migrated,
        )
        counts = tuple(
            item.get("total_rows")
            for item in getattr(preview, "counts_by_source", ())
            if isinstance(item, Mapping)
        )
        if not (
            verified is True
            and len(counts) == len(source_ids)
            and all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in counts)
            and sum(counts) == sum(len(block.rows) for block in blocks)
        ):
            raise attendance_canonical_rebuild.CanonicalRebuildHold(
                "옮긴 출결 기록 전체를 확인하지 못했습니다."
            )
        return {"verified": True, "moved_row_counts": counts}
    except attendance_canonical_rebuild.CanonicalRebuildHold:
        raise
    except Exception as error:
        raise attendance_canonical_rebuild.CanonicalRebuildHold(
            "출결 기록과 기준 자료를 안전하게 옮기고 확인하지 못했어요."
        ) from error


def verify_attendance_resources(
    *,
    config_dir: Path,
    runner,
    gws_executable: str,
    source_spreadsheet_id: str,
    destination_spreadsheet_id: str,
    install_result,
    **_unused,
) -> bool:
    """문서·폴더·Tasks와 보존한 학급 대화방 설정을 실제 ID로 다시 확인한다."""

    try:
        source_rows = install_attendance_automation._read_existing_setting_rows(
            runner, Path(config_dir), source_spreadsheet_id, gws_executable
        )
        candidate_rows = install_attendance_automation._read_existing_setting_rows(
            runner, Path(config_dir), destination_spreadsheet_id, gws_executable
        )
        source = _settings_map(source_rows)
        candidate = _settings_map(candidate_rows)
        required = {
            "TEMPLATE_DOC_ID": str(install_result.template_doc_id),
            "DEST_FOLDER_ID": str(install_result.folder_id),
            "TASK_LIST_ID": str(install_result.task_list_id),
            "SCRIPT_ID": str(install_result.script_id),
            "DEPLOYMENT_ID": str(install_result.deployment_id),
        }
        if any(candidate.get(key) != value for key, value in required.items()):
            return False
        for key in (
            "CLASS_CHAT_SPACE_ID",
            "CLASS_CHAT_SPACE_NAME",
            "CENTRAL_CHAT_SENDER_URL",
            "CENTRAL_CHAT_SHEET_SECRET",
        ):
            if candidate.get(key, "") != source.get(key, ""):
                return False
        central_id = candidate.get("CENTRAL_CHAT_SHEET_ID", "")
        if not central_id.startswith(destination_spreadsheet_id + ":"):
            return False

        def drive_file(file_id: str, mime_type: str) -> bool:
            value = attendance_canonical_rebuild._run_json(
                runner,
                [
                    gws_executable,
                    "drive",
                    "files",
                    "get",
                    "--params",
                    json.dumps(
                        {"fileId": file_id, "fields": "id,mimeType,trashed"},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    "--format",
                    "json",
                ],
                Path(config_dir),
            )
            return bool(
                isinstance(value, dict)
                and value.get("id") == file_id
                and value.get("mimeType") == mime_type
                and value.get("trashed") is False
            )

        if not drive_file(
            str(install_result.template_doc_id),
            "application/vnd.google-apps.document",
        ):
            return False
        if not drive_file(
            str(install_result.folder_id),
            "application/vnd.google-apps.folder",
        ):
            return False
        task = attendance_canonical_rebuild._run_json(
            runner,
            [
                gws_executable,
                "tasks",
                "tasklists",
                "get",
                "--params",
                json.dumps(
                    {"tasklist": str(install_result.task_list_id)},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "--format",
                "json",
            ],
            Path(config_dir),
        )
        if not (
            isinstance(task, dict)
            and task.get("id") == str(install_result.task_list_id)
        ):
            return False
        class_space_id = candidate.get("CLASS_CHAT_SPACE_ID", "")
        class_space_name = candidate.get("CLASS_CHAT_SPACE_NAME", "")
        if bool(class_space_id) != bool(class_space_name):
            return False
        if class_space_id:
            from dashboard import central_chat

            spaces = central_chat.list_spaces(
                Path(config_dir),
                lambda args: runner(args, Path(config_dir)),
                gws_executable=gws_executable,
                attendance_record={"spreadsheet_id": source_spreadsheet_id},
            )
            if not isinstance(spaces, list) or not any(
                isinstance(space, dict)
                and space.get("name") == class_space_id
                and space.get("displayName") == class_space_name
                for space in spaces
            ):
                return False
        return True
    except Exception:
        return False


def verify_final_candidate_name(
    *,
    runner,
    workdir: Path,
    gws_executable: str,
    destination_spreadsheet_id: str,
    final_name: str,
    **_unused,
) -> bool:
    """후보를 바꾸지 않고 정식 이름과 파일 종류·휴지통 상태를 다시 읽는다."""

    try:
        destination_id = str(destination_spreadsheet_id or "").strip()
        expected_name = str(final_name or "").strip()
        value = attendance_canonical_rebuild._run_json(
            runner,
            [
                gws_executable,
                "drive",
                "files",
                "get",
                "--params",
                json.dumps(
                    {
                        "fileId": destination_id,
                        "fields": "id,name,mimeType,trashed",
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "--format",
                "json",
            ],
            Path(workdir),
        )
        return bool(
            destination_id
            and expected_name
            and isinstance(value, dict)
            and value.get("id") == destination_id
            and value.get("name") == expected_name
            and value.get("mimeType")
            == attendance_canonical_rebuild.SPREADSHEET_MIME_TYPE
            and value.get("trashed") is False
        )
    except Exception:
        return False


_CANDIDATE_STATE_FIELDS = (
    "spreadsheet_id",
    "spreadsheet_url",
    "template_doc_id",
    "template_doc_url",
    "script_id",
    "deployment_id",
    "folder_id",
    "task_list_id",
    "script_bundle_sha256",
    "workbook_role",
)


def _candidate_state(result) -> dict[str, str]:
    value = {
        key: str(getattr(result, key, "") or "").strip()
        for key in _CANDIDATE_STATE_FIELDS
    }
    if not all(value[key] for key in _CANDIDATE_STATE_FIELDS[:-2]):
        raise TransitionUserError("새 정식 출석부의 연결 자료를 확인하지 못했어요.")
    return value


def _candidate_from_state(value: object, final_name: str):
    if not isinstance(value, dict) or set(value) != set(_CANDIDATE_STATE_FIELDS):
        raise TransitionUserError("새 정식 출석부의 재시도 기록을 확인하지 못했어요.")
    checked = {key: str(value.get(key, "") or "").strip() for key in value}
    if not all(checked[key] for key in _CANDIDATE_STATE_FIELDS[:-2]):
        raise TransitionUserError("새 정식 출석부의 재시도 기록을 확인하지 못했어요.")
    return install_attendance_automation.AttendanceInstallResult(
        spreadsheet_id=checked["spreadsheet_id"],
        spreadsheet_url=checked["spreadsheet_url"],
        template_doc_id=checked["template_doc_id"],
        template_doc_url=checked["template_doc_url"],
        script_id=checked["script_id"],
        deployment_id=checked["deployment_id"],
        folder_id=checked["folder_id"],
        task_list_id=checked["task_list_id"],
        workbook_name=final_name,
        script_bundle_sha256=checked["script_bundle_sha256"],
        workbook_role=(
            checked["workbook_role"]
            or attendance_workbook_identity.ATTENDANCE_ROLE_VALUE
        ),
    )


def _consolidation_candidate_ok(
    result,
    *,
    forbidden_ids: tuple[str, ...],
    expected_name: str,
) -> bool:
    candidate_id = str(getattr(result, "spreadsheet_id", "") or "").strip()
    bundle_sha256 = str(
        getattr(result, "script_bundle_sha256", "") or ""
    ).strip().lower()
    return bool(
        candidate_id
        and candidate_id not in forbidden_ids
        and str(getattr(result, "spreadsheet_url", "") or "").startswith(
            f"https://docs.google.com/spreadsheets/d/{candidate_id}/"
        )
        and str(getattr(result, "script_id", "") or "").strip()
        and str(getattr(result, "deployment_id", "") or "").strip()
        and str(getattr(result, "workbook_name", "") or "") == expected_name
        and _is_sha256(bundle_sha256)
    )


def _result_from_cleanup_state(state: Mapping[str, Any]) -> TransitionResult:
    counts = state.get("moved_row_counts", [])
    if not (
        isinstance(counts, list)
        and all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in counts)
    ):
        counts = []
    remaining = state.get("remaining_cleanup_ids", [])
    if not isinstance(remaining, list):
        remaining = []
    total = state.get("total_cleanup_count", len(remaining))
    if not isinstance(total, int) or isinstance(total, bool) or total < len(remaining):
        total = len(remaining)
    trigger_count = state.get("trigger_count", 0)
    if not isinstance(trigger_count, int) or isinstance(trigger_count, bool):
        trigger_count = 0
    return TransitionResult(
        state=str(state.get("state", "failed") or "failed"),
        spreadsheet_url=str(state.get("spreadsheet_url", "") or ""),
        moved_row_counts=tuple(counts),
        trigger_count=trigger_count,
        trashed_count=total - len(remaining),
        remaining_cleanup_count=len(remaining),
    )


def _cleanup_approval_payload(
    *,
    fingerprint: str,
    candidate_spreadsheet_id: str,
    approved_cleanup_ids: tuple[str, ...],
) -> dict[str, object]:
    body = {
        "fingerprint": fingerprint,
        "candidate_spreadsheet_id": candidate_spreadsheet_id,
        "approved_cleanup_ids": list(approved_cleanup_ids),
    }
    digest = hashlib.sha256(
        json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {**body, "sha256": digest}


def _validated_cleanup_approval(
    state: Mapping[str, Any],
    *,
    candidate_spreadsheet_id: str,
    fingerprint: str,
) -> tuple[str, ...] | None:
    approval = state.get("cleanup_approval")
    if not isinstance(approval, dict) or set(approval) != {
        "fingerprint",
        "candidate_spreadsheet_id",
        "approved_cleanup_ids",
        "sha256",
    }:
        return None
    approval_fingerprint = str(approval.get("fingerprint", "") or "").strip().lower()
    approval_candidate = str(
        approval.get("candidate_spreadsheet_id", "") or ""
    ).strip()
    approval_ids = approval.get("approved_cleanup_ids")
    approval_hash = str(approval.get("sha256", "") or "").strip().lower()
    if not (
        _is_sha256(approval_fingerprint)
        and approval_fingerprint == fingerprint
        and approval_candidate == candidate_spreadsheet_id
        and isinstance(approval_ids, list)
        and bool(approval_ids)
        and all(
            isinstance(value, str)
            and value
            and value != approval_candidate
            and _GOOGLE_ID_PATTERN.fullmatch(value) is not None
            for value in approval_ids
        )
        and len(set(approval_ids)) == len(approval_ids)
        and _is_sha256(approval_hash)
    ):
        return None
    expected = _cleanup_approval_payload(
        fingerprint=approval_fingerprint,
        candidate_spreadsheet_id=approval_candidate,
        approved_cleanup_ids=tuple(approval_ids),
    )
    if approval_hash != expected["sha256"]:
        return None
    return tuple(approval_ids)


def _cleanup_state_payload(
    *,
    state: str,
    fingerprint: str,
    candidate_spreadsheet_id: str,
    approved_cleanup_ids: tuple[str, ...],
    spreadsheet_url: str,
    moved_row_counts: tuple[int, ...],
    trigger_count: int,
    total_cleanup_count: int,
    remaining_cleanup_ids: list[str],
) -> dict[str, object]:
    return {
        "schema_version": 3,
        "state": state,
        "candidate_spreadsheet_id": candidate_spreadsheet_id,
        "spreadsheet_url": spreadsheet_url,
        "moved_row_counts": list(moved_row_counts),
        "trigger_count": trigger_count,
        "total_cleanup_count": total_cleanup_count,
        "remaining_cleanup_ids": list(remaining_cleanup_ids),
        "cleanup_approval": _cleanup_approval_payload(
            fingerprint=fingerprint,
            candidate_spreadsheet_id=candidate_spreadsheet_id,
            approved_cleanup_ids=approved_cleanup_ids,
        ),
    }


_CLEANUP_CHECKPOINT_KEYS = frozenset({
    "schema_version",
    "state",
    "candidate_spreadsheet_id",
    "spreadsheet_url",
    "moved_row_counts",
    "trigger_count",
    "total_cleanup_count",
    "remaining_cleanup_ids",
    "cleanup_approval",
})
_CLEANUP_CHECKPOINT_STATES = frozenset({
    "record-switched", "cleanup-required", "complete",
})


def _canonical_spreadsheet_url(spreadsheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"


def _validated_consolidation_checkpoint(
    state: Mapping[str, Any],
    record: Mapping[str, Any],
    *,
    expected_fingerprint: str,
) -> ConsolidationCheckpoint | None:
    if not isinstance(state, Mapping) or set(state) != _CLEANUP_CHECKPOINT_KEYS:
        return None
    checkpoint_state = str(state.get("state", "") or "")
    candidate_id = str(state.get("candidate_spreadsheet_id", "") or "").strip()
    expected_url = _canonical_spreadsheet_url(candidate_id)
    approved = _validated_cleanup_approval(
        state,
        candidate_spreadsheet_id=candidate_id,
        fingerprint=expected_fingerprint,
    )
    remaining_value = state.get("remaining_cleanup_ids")
    counts_value = state.get("moved_row_counts")
    total = state.get("total_cleanup_count")
    trigger_count = state.get("trigger_count")
    if not (
        state.get("schema_version") == 3
        and checkpoint_state in _CLEANUP_CHECKPOINT_STATES
        and _GOOGLE_ID_PATTERN.fullmatch(candidate_id) is not None
        and not candidate_id.startswith("AIza")
        and approved is not None
        and isinstance(remaining_value, list)
        and all(isinstance(value, str) and value for value in remaining_value)
        and len(set(remaining_value)) == len(remaining_value)
        and isinstance(counts_value, list)
        and len(counts_value) == len(approved)
        and all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in counts_value
        )
        and isinstance(total, int)
        and not isinstance(total, bool)
        and total == len(approved)
        and trigger_count == 1
        and state.get("spreadsheet_url") == expected_url
        and record.get("spreadsheet_id") == candidate_id
        and record.get("spreadsheet_url") == expected_url
        and record.get("workbook_role")
        == attendance_workbook_identity.ATTENDANCE_ROLE_VALUE
    ):
        return None
    remaining = tuple(remaining_value)
    expected_remaining = approved[len(approved) - len(remaining):] if remaining else ()
    if remaining != expected_remaining:
        return None
    if checkpoint_state == "record-switched" and remaining != approved:
        return None
    if checkpoint_state == "cleanup-required" and not remaining:
        return None
    if checkpoint_state == "complete" and remaining:
        return None
    return ConsolidationCheckpoint(
        state=checkpoint_state,
        fingerprint=expected_fingerprint,
        candidate_spreadsheet_id=candidate_id,
        spreadsheet_url=expected_url,
        approved_cleanup_ids=approved,
        remaining_cleanup_ids=remaining,
        moved_row_counts=tuple(counts_value),
        trigger_count=trigger_count,
        total_cleanup_count=total,
    )


def read_validated_consolidation_checkpoint(
    config_dir: Path,
    *,
    expected_fingerprint: str,
    expected_account: str = "",
) -> ConsolidationCheckpoint | None:
    """실제 로컬 기록과 Task 5 진행 상태를 읽기만 하며 한 묶음으로 검증한다."""

    config_dir = Path(config_dir)
    fingerprint = str(expected_fingerprint or "").strip().lower()
    if not _is_sha256(fingerprint):
        return None
    try:
        state = _read_dict(
            config_dir / "attendance-workbook-transition.generated.json"
        )
        record = load_attendance_install_record(
            config_dir / "attendance-install.generated.json"
        )
        setup_path = config_dir / "attendance-setup-status.generated.json"
        if expected_account and setup_path.exists():
            setup = _read_dict(setup_path)
            saved_account = str(setup.get("account", "") or "").strip().lower()
            if saved_account and saved_account != str(expected_account).strip().lower():
                return None
    except Exception:
        return None
    return _validated_consolidation_checkpoint(
        state,
        record,
        expected_fingerprint=fingerprint,
    )


_BUILDING_TRANSITION_STATES = frozenset({"building"})
_AI_ACTION_TRANSITION_STATES = frozenset({"ai-action-required"})
_SWITCH_TRANSITION_STATES = frozenset({
    "candidate-finalized",
    "central-move-in-flight",
    "central-move-required",
    "central-moved",
    "central-not-registered",
    "central-rollback-in-flight",
    "central-rollback-required",
    "record-switch-in-flight",
    "switch-required",
})
_KNOWN_CONSOLIDATION_STATES = (
    _BUILDING_TRANSITION_STATES
    | _AI_ACTION_TRANSITION_STATES
    | _SWITCH_TRANSITION_STATES
    | _CLEANUP_CHECKPOINT_STATES
)

_SWITCH_TRANSITION_KEYS = frozenset({
    "schema_version",
    "state",
    "fingerprint",
    "source_spreadsheet_id",
    "approved_cleanup_ids",
    "candidate_spreadsheet_id",
    "candidate",
    "spreadsheet_url",
    "moved_row_counts",
    "trigger_count",
    "total_cleanup_count",
    "remaining_cleanup_ids",
    "record_progress",
    "cleanup_approval",
})

_COMPLETED_LEGACY_SPLIT_REPAIR_KEYS = frozenset({
    "state",
    "reason",
    "source_spreadsheet_id",
    "school_year",
    "progress",
    "central_complete",
    "spreadsheet_id",
    "spreadsheet_url",
})


def _valid_new_school_year_transition_state(value: Mapping[str, Any]) -> bool:
    """같은 파일을 쓰는 알려진 옛 새 학년도 진행 모양만 명시적으로 인정한다."""

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
    if state == "candidate-verified":
        required |= {"spreadsheet_id", "spreadsheet_url"}
    elif state != "building":
        return False
    progress = value.get("progress")
    previous_id = value.get("previous_spreadsheet_id")
    if not (
        set(value) == required
        and isinstance(previous_id, str)
        and _GOOGLE_ID_PATTERN.fullmatch(previous_id) is not None
        and isinstance(value.get("school_year"), str)
        and re.fullmatch(r"20[0-9]{2}", value["school_year"]) is not None
        and isinstance(progress, dict)
        and _valid_install_progress(progress, (previous_id,))
    ):
        return False
    if state == "candidate-verified":
        candidate_id = value.get("spreadsheet_id")
        return bool(
            isinstance(candidate_id, str)
            and _GOOGLE_ID_PATTERN.fullmatch(candidate_id) is not None
            and candidate_id != previous_id
            and value.get("spreadsheet_url")
            == _canonical_spreadsheet_url(candidate_id)
        )
    return True


def _valid_completed_legacy_split_repair_state(value: Mapping[str, Any]) -> bool:
    """연결 교체 직전까지 끝낸 옛 정리 기록의 정확한 모양만 인정한다."""

    if not isinstance(value, Mapping) or set(value) != (
        _COMPLETED_LEGACY_SPLIT_REPAIR_KEYS
    ):
        return False
    source_id = value.get("source_spreadsheet_id")
    candidate_id = value.get("spreadsheet_id")
    school_year = value.get("school_year")
    progress = value.get("progress")
    canonical_url = (
        _canonical_spreadsheet_url(candidate_id)
        if isinstance(candidate_id, str)
        else ""
    )
    return bool(
        value.get("state") == "candidate-verified"
        and value.get("reason")
        == install_attendance_automation.ATTENDANCE_CREATION_SPLIT_REPAIR
        and value.get("central_complete") is True
        and isinstance(source_id, str)
        and _GOOGLE_ID_PATTERN.fullmatch(source_id) is not None
        and not source_id.startswith("AIza")
        and isinstance(candidate_id, str)
        and _GOOGLE_ID_PATTERN.fullmatch(candidate_id) is not None
        and not candidate_id.startswith("AIza")
        and source_id != candidate_id
        and isinstance(school_year, str)
        and (not school_year or re.fullmatch(r"20[0-9]{2}", school_year) is not None)
        and isinstance(progress, dict)
        and set(progress) == CONNECTION_FIELDS
        and _valid_install_progress(progress, (source_id,))
        and progress.get("spreadsheet_id") == candidate_id
        and progress.get("spreadsheet_url") == canonical_url
        and value.get("spreadsheet_url") == canonical_url
    )


def _valid_switch_transition_state(value: Mapping[str, Any]) -> bool:
    """연결 교체 전후의 재개 파일을 화면에 내보내기 전에 완전히 검사한다."""

    if not isinstance(value, Mapping) or set(value) != _SWITCH_TRANSITION_KEYS:
        return False
    state = value.get("state")
    fingerprint = str(value.get("fingerprint", "") or "").strip().lower()
    source_id = str(value.get("source_spreadsheet_id", "") or "").strip()
    candidate_id = str(value.get("candidate_spreadsheet_id", "") or "").strip()
    approved = value.get("approved_cleanup_ids")
    remaining = value.get("remaining_cleanup_ids")
    counts = value.get("moved_row_counts")
    progress = value.get("record_progress")
    if not (
        value.get("schema_version") == 3
        and state in _SWITCH_TRANSITION_STATES
        and _is_sha256(fingerprint)
        and _GOOGLE_ID_PATTERN.fullmatch(source_id) is not None
        and _GOOGLE_ID_PATTERN.fullmatch(candidate_id) is not None
        and source_id != candidate_id
        and isinstance(approved, list)
        and approved.count(source_id) == 1
        and candidate_id not in approved
        and len(set(approved)) == len(approved)
        and all(
            isinstance(item, str)
            and _GOOGLE_ID_PATTERN.fullmatch(item) is not None
            for item in approved
        )
        and remaining == approved
        and isinstance(counts, list)
        and len(counts) == len(approved)
        and all(
            isinstance(count, int) and not isinstance(count, bool) and count >= 0
            for count in counts
        )
        and value.get("trigger_count") == 1
        and value.get("total_cleanup_count") == len(approved)
        and value.get("spreadsheet_url") == _canonical_spreadsheet_url(candidate_id)
        and isinstance(progress, dict)
    ):
        return False
    try:
        candidate = _candidate_from_state(value.get("candidate"), "")
        _validate_consolidation_progress(progress)
    except (TransitionUserError, ValueError):
        return False
    return bool(
        candidate.spreadsheet_id == candidate_id
        and candidate.spreadsheet_url == value.get("spreadsheet_url")
        and progress.get("stage") == "records-complete"
        and _validated_cleanup_approval(
            value,
            candidate_spreadsheet_id=candidate_id,
            fingerprint=fingerprint,
        )
        == tuple(approved)
    )


def _load_consolidation_transition_state(
    state_path: Path,
) -> tuple[dict[str, Any], bool]:
    """(상태, 손상 여부). 파일이 없거나 다른 정식 흐름이면 빈 정상 상태다."""

    state_path = Path(state_path)
    if not state_path.exists():
        return {}, False
    try:
        value = _read_dict(state_path)
    except Exception:
        return {}, True
    # 새 학년도 전환도 같은 파일을 쓰므로, 알려진 옛 모양만 통합 재개 밖으로 인정한다.
    if value.get("reason") == install_attendance_automation.ATTENDANCE_CREATION_NEW_SCHOOL_YEAR:
        return ({}, False) if _valid_new_school_year_transition_state(value) else ({}, True)
    state = str(value.get("state", "") or "")
    schema = value.get("schema_version")
    if state not in _KNOWN_CONSOLIDATION_STATES:
        return {}, True
    expected_schema = (
        2 if state in _BUILDING_TRANSITION_STATES
        else 4 if state in _AI_ACTION_TRANSITION_STATES
        else 3
    )
    if schema != expected_schema:
        return {}, True
    if state == "building":
        if set(value) != {
            "schema_version",
            "state",
            "fingerprint",
            "source_spreadsheet_id",
            "approved_cleanup_ids",
            "install_progress",
            "record_progress",
        }:
            return {}, True
        try:
            _validate_consolidation_progress(value.get("record_progress"))
        except ValueError:
            return {}, True
        source_id = value.get("source_spreadsheet_id")
        approved_value = value.get("approved_cleanup_ids")
        approved_ids = (
            tuple(approved_value) if isinstance(approved_value, list) else ()
        )
        progress_value = value["record_progress"]
        if not (
            _is_sha256(value.get("fingerprint"))
            and isinstance(source_id, str)
            and _GOOGLE_ID_PATTERN.fullmatch(source_id) is not None
            and not source_id.startswith("AIza")
            and approved_ids.count(source_id) == 1
            and len(approved_ids) == len(set(approved_ids))
            and all(
                isinstance(item, str)
                and _GOOGLE_ID_PATTERN.fullmatch(item) is not None
                and not item.startswith("AIza")
                for item in approved_ids
            )
            and progress_value.get("fingerprint") == value.get("fingerprint")
            and progress_value.get("stage") in _BUILDING_RECORD_STAGES
            and _valid_install_progress(value.get("install_progress"), approved_ids)
        ):
            return {}, True
    if state in _SWITCH_TRANSITION_STATES and not _valid_switch_transition_state(value):
        return {}, True
    return value, False


def _validated_ai_action_state(
    state: Mapping[str, Any],
    *,
    expected_fingerprint: str = "",
) -> ResumableTransitionStatus | None:
    required = {
        "schema_version",
        "state",
        "fingerprint",
        "source_spreadsheet_id",
        "approved_cleanup_ids",
        "candidate_spreadsheet_id",
        "candidate",
        "spreadsheet_url",
        "moved_row_counts",
        "record_progress",
    }
    if not isinstance(state, Mapping) or set(state) != required:
        return None
    fingerprint = str(state.get("fingerprint", "") or "").strip().lower()
    source_id = str(state.get("source_spreadsheet_id", "") or "").strip()
    candidate_id = str(state.get("candidate_spreadsheet_id", "") or "").strip()
    approved = state.get("approved_cleanup_ids")
    counts = state.get("moved_row_counts")
    url = str(state.get("spreadsheet_url", "") or "")
    if not (
        state.get("schema_version") == 4
        and state.get("state") == "ai-action-required"
        and _is_sha256(fingerprint)
        and (not expected_fingerprint or fingerprint == expected_fingerprint)
        and _GOOGLE_ID_PATTERN.fullmatch(source_id) is not None
        and _GOOGLE_ID_PATTERN.fullmatch(candidate_id) is not None
        and source_id != candidate_id
        and isinstance(approved, list)
        and approved.count(source_id) == 1
        and candidate_id not in approved
        and len(set(approved)) == len(approved)
        and all(
            isinstance(value, str)
            and _GOOGLE_ID_PATTERN.fullmatch(value) is not None
            for value in approved
        )
        and isinstance(counts, list)
        and len(counts) == len(approved)
        and all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in counts
        )
        and isinstance(state.get("record_progress"), dict)
        and url == _canonical_spreadsheet_url(candidate_id)
    ):
        return None
    try:
        _validate_consolidation_progress(state["record_progress"])
    except ValueError:
        return None
    if state["record_progress"].get("stage") != "records-complete":
        return None
    try:
        candidate = _candidate_from_state(state.get("candidate"), "정리 중")
    except TransitionUserError:
        return None
    if (
        candidate.spreadsheet_id != candidate_id
        or candidate.spreadsheet_url != url
    ):
        return None
    return ResumableTransitionStatus(
        state="ai-action-required",
        fingerprint=fingerprint,
        spreadsheet_url=url,
    )


def _validated_local_transition_record(
    config_dir: Path,
    *,
    allowed_spreadsheet_ids: tuple[str, ...],
    expected_account: str,
) -> dict[str, Any] | None:
    try:
        record = load_attendance_install_record(
            Path(config_dir) / "attendance-install.generated.json"
        )
        if record.get("spreadsheet_id") not in allowed_spreadsheet_ids:
            return None
        setup_path = Path(config_dir) / "attendance-setup-status.generated.json"
        if expected_account and setup_path.exists():
            setup = _read_dict(setup_path)
            saved_account = str(setup.get("account", "") or "").strip().lower()
            if saved_account and saved_account != str(expected_account).strip().lower():
                return None
        return record
    except Exception:
        return None


def _completed_legacy_split_repair_points_to_current(
    config_dir: Path,
    *,
    expected_account: str,
) -> bool:
    """옛 완료 기록과 현재 정본 연결이 서로 정확히 맞을 때만 정상으로 본다."""

    try:
        state = _read_dict(
            Path(config_dir) / "attendance-workbook-transition.generated.json"
        )
    except Exception:
        return False
    if not _valid_completed_legacy_split_repair_state(state):
        return False
    candidate_id = str(state.get("spreadsheet_id", "") or "")
    record = _validated_local_transition_record(
        config_dir,
        allowed_spreadsheet_ids=(candidate_id,),
        expected_account=expected_account,
    )
    transition_year = str(state.get("school_year", "") or "").strip()
    record_year = (
        str(record.get("school_year", "") or "").strip()
        if record is not None
        else ""
    )
    return bool(
        record is not None
        and all(record.get(key) == state["progress"].get(key) for key in CONNECTION_FIELDS)
        and record.get("spreadsheet_id") == candidate_id
        and record.get("spreadsheet_url") == _canonical_spreadsheet_url(candidate_id)
        and record.get("workbook_role")
        == attendance_workbook_identity.ATTENDANCE_ROLE_VALUE
        and (
            not transition_year
            or not record_year
            or transition_year == record_year
        )
    )


def read_resumable_transition_status(
    config_dir: Path,
    *,
    expected_account: str = "",
) -> ResumableTransitionStatus | None:
    """화면에 ID 대신 재개용 지문과 안전한 Sheet 주소만 돌려준다."""

    config_dir = Path(config_dir)
    state, invalid = _load_consolidation_transition_state(
        config_dir / "attendance-workbook-transition.generated.json"
    )
    if invalid:
        if _completed_legacy_split_repair_points_to_current(
            config_dir, expected_account=expected_account
        ):
            return None
        return ResumableTransitionStatus(state="recovery-required")
    if not state:
        return None
    state_name = str(state.get("state", "") or "")
    if state_name == "ai-action-required":
        checked = _validated_ai_action_state(state)
        source_id = str(state.get("source_spreadsheet_id", "") or "").strip()
        record = _validated_local_transition_record(
            config_dir,
            allowed_spreadsheet_ids=(source_id,),
            expected_account=expected_account,
        )
        return (
            checked
            if checked is not None and record is not None
            else ResumableTransitionStatus(state="recovery-required")
        )
    if state_name in _CLEANUP_CHECKPOINT_STATES:
        approval = state.get("cleanup_approval")
        fingerprint = str(
            approval.get("fingerprint", "") if isinstance(approval, dict) else ""
        ).strip().lower()
        checkpoint = read_validated_consolidation_checkpoint(
            config_dir,
            expected_fingerprint=fingerprint,
            expected_account=expected_account,
        )
        if checkpoint is None:
            return ResumableTransitionStatus(state="recovery-required")
        if checkpoint.state == "complete":
            return None
        return ResumableTransitionStatus(
            state=checkpoint.state,
            fingerprint=checkpoint.fingerprint,
            spreadsheet_url=checkpoint.spreadsheet_url,
        )
    fingerprint = str(state.get("fingerprint", "") or "").strip().lower()
    source_id = str(state.get("source_spreadsheet_id", "") or "").strip()
    candidate_id = str(state.get("candidate_spreadsheet_id", "") or "").strip()
    url = str(state.get("spreadsheet_url", "") or "")
    record = _validated_local_transition_record(
        config_dir,
        allowed_spreadsheet_ids=(source_id, candidate_id),
        expected_account=expected_account,
    )
    if (
        not _valid_switch_transition_state(state)
        or record is None
        or not _is_sha256(fingerprint)
    ):
        return ResumableTransitionStatus(state="recovery-required")
    if candidate_id and url != _canonical_spreadsheet_url(candidate_id):
        return ResumableTransitionStatus(state="recovery-required")
    return ResumableTransitionStatus(
        state=(
            "record-switch-in-flight"
            if state_name == "record-switch-in-flight"
            else "recovery-required"
        ),
        fingerprint=fingerprint,
        spreadsheet_url=url if url.startswith("https://docs.google.com/spreadsheets/d/") else "",
    )


def _cleanup_old_workbooks(
    *,
    config_dir: Path,
    state_path: Path,
    deps: TransitionDeps,
    checkpoint: ConsolidationCheckpoint,
) -> TransitionResult:
    if not isinstance(checkpoint, ConsolidationCheckpoint) or not callable(
        deps.trash_workbook
    ):
        return TransitionResult(state="failed")
    candidate_id = checkpoint.candidate_spreadsheet_id
    spreadsheet_url = checkpoint.spreadsheet_url
    fingerprint = checkpoint.fingerprint
    approved = checkpoint.approved_cleanup_ids
    counts = checkpoint.moved_row_counts
    remaining = list(checkpoint.remaining_cleanup_ids)
    total = checkpoint.total_cleanup_count
    trigger_count = checkpoint.trigger_count
    if not remaining:
        complete = _cleanup_state_payload(
            state="complete",
            fingerprint=fingerprint,
            candidate_spreadsheet_id=candidate_id,
            approved_cleanup_ids=approved,
            spreadsheet_url=spreadsheet_url,
            moved_row_counts=counts,
            trigger_count=trigger_count,
            total_cleanup_count=total,
            remaining_cleanup_ids=[],
        )
        _atomic_json(state_path, complete)
        return _result_from_cleanup_state(complete)
    while remaining:
        try:
            latest_record = load_attendance_install_record(
                Path(config_dir) / "attendance-install.generated.json"
            )
            expected_name = str(latest_record.get("workbook_name", "") or "").strip()
            metadata = attendance_canonical_rebuild._run_json(
                deps.runner,
                [
                    deps.gws_executable,
                    "drive",
                    "files",
                    "get",
                    "--params",
                    json.dumps(
                        {
                            "fileId": candidate_id,
                            "fields": "id,name,mimeType,trashed,ownedByMe,owners",
                            "supportsAllDrives": True,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    "--format",
                    "json",
                ],
                Path(config_dir),
            )
            owners = metadata.get("owners") if isinstance(metadata, dict) else None
            candidate_live = (
                latest_record.get("spreadsheet_id") == candidate_id
                and latest_record.get("spreadsheet_url") == spreadsheet_url
                and latest_record.get("workbook_role")
                == attendance_workbook_identity.ATTENDANCE_ROLE_VALUE
                and isinstance(metadata, dict)
                and metadata.get("id") == candidate_id
                and metadata.get("mimeType")
                == attendance_canonical_rebuild.SPREADSHEET_MIME_TYPE
                and metadata.get("trashed") is False
                and metadata.get("ownedByMe") is True
                and isinstance(owners, list)
                and any(
                    isinstance(owner, dict)
                    and str(owner.get("emailAddress", "") or "").strip().lower()
                    == str(deps.account or "").strip().lower()
                    for owner in owners
                )
                and (not expected_name or metadata.get("name") == expected_name)
            )
        except Exception:
            candidate_live = False
        if not candidate_live:
            return TransitionResult(
                state="recovery-required",
                spreadsheet_url=spreadsheet_url,
                moved_row_counts=counts,
                trigger_count=trigger_count,
                trashed_count=total - len(remaining),
                remaining_cleanup_count=len(remaining),
            )
        target = remaining[0]
        try:
            moved = deps.trash_workbook(
                runner=deps.runner,
                workdir=config_dir,
                gws_executable=deps.gws_executable,
                spreadsheet_id=target,
                approved_spreadsheet_ids=approved,
                candidate_spreadsheet_id=candidate_id,
            )
            if moved is not True:
                raise RuntimeError("휴지통 이동을 다시 확인하지 못했습니다.")
        except Exception:
            failed = _cleanup_state_payload(
                state="cleanup-required",
                fingerprint=fingerprint,
                candidate_spreadsheet_id=candidate_id,
                approved_cleanup_ids=approved,
                spreadsheet_url=spreadsheet_url,
                moved_row_counts=counts,
                trigger_count=trigger_count,
                total_cleanup_count=total,
                remaining_cleanup_ids=remaining,
            )
            _atomic_json(state_path, failed)
            return _result_from_cleanup_state(failed)
        remaining.pop(0)
        checkpoint = _cleanup_state_payload(
            state="cleanup-required" if remaining else "complete",
            fingerprint=fingerprint,
            candidate_spreadsheet_id=candidate_id,
            approved_cleanup_ids=approved,
            spreadsheet_url=spreadsheet_url,
            moved_row_counts=counts,
            trigger_count=trigger_count,
            total_cleanup_count=total,
            remaining_cleanup_ids=remaining,
        )
        _atomic_json(state_path, checkpoint)
        if not remaining:
            return _result_from_cleanup_state(checkpoint)
    return TransitionResult(state="failed")


def consolidate_attendance_workbooks(
    config_dir: Path,
    *,
    expected_fingerprint: str = "",
    deps: TransitionDeps,
) -> TransitionResult:
    """승인된 미리보기를 끝까지 검증한 뒤 연결과 휴지통 상태를 순서대로 바꾼다.

    호출자는 dashboard의 공용 출결 원격 작업 잠금을 잡은 뒤 이 함수를 부른다. 잠금
    안에서 첫 동작으로 로컬 연결과 진행 파일을 다시 읽어 두 창의 두 번째 실행이 첫
    실행의 완료 또는 정리 재시도 상태를 그대로 사용하게 한다.
    """

    config_dir = Path(config_dir)
    profile_path = config_dir / "profile.generated.json"
    record_path = config_dir / "attendance-install.generated.json"
    state_path = config_dir / "attendance-workbook-transition.generated.json"
    expected_fingerprint = str(expected_fingerprint or "").strip().lower()
    try:
        profile = _read_dict(profile_path)
        record = _read_dict(record_path)
        current_id = str(record.get("spreadsheet_id", "") or "").strip()
        if not current_id:
            raise TransitionUserError("현재 출석부 연결을 확인하지 못했어요.")
        saved, invalid_saved = _load_consolidation_transition_state(state_path)
        if invalid_saved:
            return TransitionResult(state="recovery-required")

        saved_state = str(saved.get("state", "") or "")
        if (
            saved_state in {"record-switched", "cleanup-required", "complete"}
        ):
            checkpoint = read_validated_consolidation_checkpoint(
                config_dir,
                expected_fingerprint=expected_fingerprint,
                expected_account=deps.account,
            )
            if checkpoint is None:
                return TransitionResult(state="recovery-required")
            if checkpoint.state == "complete":
                return _result_from_cleanup_state(saved)
            return _cleanup_old_workbooks(
                config_dir=config_dir,
                state_path=state_path,
                deps=deps,
                checkpoint=checkpoint,
            )

        final_name = attendance_workbook_identity.attendance_workbook_name(profile)
        pending_name = install_attendance_automation._consolidation_candidate_name(
            final_name, expected_fingerprint
        )

        def saved_switch_values(
            state: Mapping[str, Any], *, require_approval: bool = False
        ):
            source_id = str(state.get("source_spreadsheet_id", "") or "").strip()
            approved_value = state.get("approved_cleanup_ids", [])
            counts_value = state.get("moved_row_counts", [])
            trigger_count = state.get("trigger_count", 0)
            record_progress_value = state.get("record_progress")
            result = _candidate_from_state(state.get("candidate"), final_name)
            candidate_id = str(result.spreadsheet_id)
            if not (
                state.get("fingerprint") == expected_fingerprint
                and source_id
                and isinstance(approved_value, list)
                and all(isinstance(value, str) and value for value in approved_value)
                and len(set(approved_value)) == len(approved_value)
                and candidate_id not in approved_value
                and isinstance(counts_value, list)
                and len(counts_value) == len(approved_value)
                and all(
                    isinstance(value, int)
                    and not isinstance(value, bool)
                    and value >= 0
                    for value in counts_value
                )
                and trigger_count == 1
                and isinstance(record_progress_value, dict)
            ):
                raise TransitionUserError("새 정식 출석부의 재시도 상태를 확인하지 못했어요.")
            approved_ids = tuple(approved_value)
            if require_approval:
                anchored_ids = _validated_cleanup_approval(
                    state,
                    candidate_spreadsheet_id=candidate_id,
                    fingerprint=expected_fingerprint,
                )
                if not (
                    anchored_ids == approved_ids
                    and state.get("remaining_cleanup_ids") == list(anchored_ids)
                    and state.get("total_cleanup_count") == len(anchored_ids)
                    and state.get("candidate_spreadsheet_id") == candidate_id
                    and state.get("source_spreadsheet_id") in anchored_ids
                    and anchored_ids.count(state.get("source_spreadsheet_id")) == 1
                ):
                    raise TransitionUserError(
                        "새 정식 출석부의 정리 승인 상태를 확인하지 못했어요."
                    )
            return (
                result,
                source_id,
                approved_ids,
                tuple(counts_value),
                trigger_count,
                dict(record_progress_value),
            )

        def anchored_switch_state(
            state: Mapping[str, Any],
            *,
            source_id: str,
            candidate_id: str,
            approved_ids: tuple[str, ...],
            moved_row_counts: tuple[int, ...],
        ) -> dict[str, Any]:
            if approved_ids.count(source_id) != 1 or candidate_id in approved_ids:
                raise TransitionUserError("새 정식 출석부의 정리 승인 상태를 만들지 못했어요.")
            anchored = dict(state)
            anchored.update(
                {
                    "fingerprint": expected_fingerprint,
                    "source_spreadsheet_id": source_id,
                    "approved_cleanup_ids": list(approved_ids),
                    "candidate_spreadsheet_id": candidate_id,
                    "moved_row_counts": list(moved_row_counts),
                    "total_cleanup_count": len(approved_ids),
                    "remaining_cleanup_ids": list(approved_ids),
                    "cleanup_approval": _cleanup_approval_payload(
                        fingerprint=expected_fingerprint,
                        candidate_spreadsheet_id=candidate_id,
                        approved_cleanup_ids=approved_ids,
                    ),
                }
            )
            return anchored

        def read_central_route(source_id: str, candidate_id: str) -> str:
            value = deps.central_route_reader(
                config_dir=config_dir,
                source_spreadsheet_id=source_id,
                candidate_spreadsheet_id=candidate_id,
                runner=deps.runner,
                gws_executable=deps.gws_executable,
            )
            route = str(getattr(value, "route", value) or "").strip()
            if route not in {"source", "candidate", "not_registered"}:
                raise TransitionUserError("학급 단톡방 연결 위치를 확인하지 못했어요.")
            return route

        def fresh_resume_validation(state: Mapping[str, Any]):
            (
                result,
                source_id,
                approved_ids,
                moved_row_counts,
                trigger_count,
                record_progress_value,
            ) = saved_switch_values(state)
            if deps.consent_checker(config_dir=config_dir, account=deps.account) is not True:
                raise TransitionUserError("Google 권한 허용 기록을 확인하지 못했어요.")
            preview = deps.preview_builder(
                config_dir=config_dir,
                runner=deps.runner,
                gws_executable=deps.gws_executable,
                account=deps.account,
            )
            if not (
                getattr(preview, "state", "") == "ready"
                and str(getattr(preview, "fingerprint", "") or "").lower()
                == expected_fingerprint
                and _preview_source_ids(preview) == approved_ids
                and approved_ids.count(source_id) == 1
            ):
                raise TransitionUserError("확인한 출결 파일 내용이 실행 직전에 달라졌어요.")
            candidate_id = str(result.spreadsheet_id)
            if not (
                candidate_id not in approved_ids
                and deps.candidate_verifier(
                    runner=deps.runner,
                    workdir=config_dir,
                    gws_executable=deps.gws_executable,
                    destination_spreadsheet_id=candidate_id,
                    final_name=final_name,
                )
                is True
            ):
                raise TransitionUserError("새 출석부의 정식 이름을 다시 확인하지 못했어요.")
            migration = deps.record_migrator(
                config_dir=config_dir,
                runner=deps.runner,
                gws_executable=deps.gws_executable,
                account=deps.account,
                profile=profile,
                current_record=record,
                source_spreadsheet_id=source_id,
                destination_spreadsheet_id=candidate_id,
                preview=preview,
                install_result=result,
                fingerprint=expected_fingerprint,
                progress=record_progress_value,
                remember=lambda _progress: None,
                verify_only=True,
            )
            if not (
                isinstance(migration, dict)
                and migration.get("verified") is True
                and migration.get("moved_row_counts") == moved_row_counts
            ):
                raise TransitionUserError("새 출석부 후보의 모든 기록을 다시 확인하지 못했어요.")
            ai_status = deps.ai_inspector(
                runner=deps.runner,
                workdir=config_dir,
                gws_executable=deps.gws_executable,
                spreadsheet_id=candidate_id,
            )
            if not (
                getattr(ai_status, "ok", False) is True
                and getattr(ai_status, "trigger_count", 0) == trigger_count == 1
            ):
                raise TransitionUserError("AI 출결 입력 연결을 다시 확인하지 못했어요.")
            if deps.resource_verifier(
                config_dir=config_dir,
                runner=deps.runner,
                gws_executable=deps.gws_executable,
                account=deps.account,
                source_spreadsheet_id=source_id,
                destination_spreadsheet_id=candidate_id,
                install_result=result,
            ) is not True:
                raise TransitionUserError("새 출석부가 쓰는 연결 자료를 다시 확인하지 못했어요.")
            latest_record = _read_dict(record_path)
            latest_id = str(latest_record.get("spreadsheet_id", "") or "").strip()
            route = read_central_route(source_id, candidate_id)
            return (
                result,
                source_id,
                approved_ids,
                moved_row_counts,
                trigger_count,
                latest_id,
                route,
            )

        def finish_switch(
            *,
            result,
            source_id: str,
            approved_ids: tuple[str, ...],
            moved_row_counts: tuple[int, ...],
            trigger_count: int,
            central_route: str,
            switch_state: Mapping[str, Any],
        ) -> TransitionResult:
            candidate_id = str(result.spreadsheet_id)
            (
                anchored_result,
                anchored_source,
                anchored_ids,
                anchored_counts,
                anchored_trigger_count,
                _anchored_progress,
            ) = saved_switch_values(switch_state, require_approval=True)
            if not (
                str(anchored_result.spreadsheet_id) == candidate_id
                and anchored_source == source_id
                and anchored_ids == approved_ids
                and anchored_counts == moved_row_counts
                and anchored_trigger_count == trigger_count
            ):
                raise TransitionUserError("새 정식 출석부의 정리 승인 상태가 달라졌어요.")
            central_moved = central_route == "candidate"
            if central_route == "source":
                in_flight = dict(switch_state)
                in_flight["state"] = "central-move-in-flight"
                _atomic_json(state_path, in_flight)
                try:
                    moved = deps.central_mover(
                        config_dir=config_dir,
                        source_spreadsheet_id=source_id,
                        destination_spreadsheet_id=candidate_id,
                        runner=deps.runner,
                        gws_executable=deps.gws_executable,
                    )
                except Exception:
                    held = dict(in_flight)
                    held["state"] = "central-move-required"
                    _atomic_json(state_path, held)
                    return TransitionResult(state="failed")
                outcome = "not_registered" if moved is True else str(
                    getattr(moved, "outcome", "") or ""
                )
                if outcome not in {"moved", "not_registered"}:
                    raise TransitionUserError(
                        "학급 단톡방 연결을 옮긴 결과를 확인하지 못했어요."
                    )
                central_moved = outcome == "moved"
                central_state = dict(switch_state)
                central_state["state"] = (
                    "central-moved" if central_moved else "central-not-registered"
                )
                _atomic_json(state_path, central_state)
            local_in_flight = dict(switch_state)
            local_in_flight["state"] = "record-switch-in-flight"
            _atomic_json(state_path, local_in_flight)
            try:
                _switch_record_last(
                    record_path,
                    profile_path,
                    result,
                    write_record=deps.write_record,
                )
            except Exception:
                rollback_ok = not central_moved
                if central_moved:
                    rollback_state = dict(switch_state)
                    rollback_state["state"] = "central-rollback-in-flight"
                    _atomic_json(state_path, rollback_state)
                    try:
                        rollback_ok = deps.central_rollback(
                            config_dir=config_dir,
                            source_spreadsheet_id=source_id,
                            candidate_spreadsheet_id=candidate_id,
                            runner=deps.runner,
                            gws_executable=deps.gws_executable,
                        ) is True
                    except Exception:
                        rollback_ok = False
                failed_state = dict(switch_state)
                failed_state["state"] = (
                    "switch-required" if rollback_ok else "central-rollback-required"
                )
                _atomic_json(state_path, failed_state)
                return TransitionResult(state=failed_state["state"] if not rollback_ok else "failed")

            switched_state = _cleanup_state_payload(
                state="record-switched",
                fingerprint=expected_fingerprint,
                candidate_spreadsheet_id=candidate_id,
                approved_cleanup_ids=approved_ids,
                spreadsheet_url=str(result.spreadsheet_url),
                moved_row_counts=moved_row_counts,
                trigger_count=trigger_count,
                total_cleanup_count=len(approved_ids),
                remaining_cleanup_ids=list(approved_ids),
            )
            _atomic_json(state_path, switched_state)
            checkpoint = read_validated_consolidation_checkpoint(
                config_dir,
                expected_fingerprint=expected_fingerprint,
                expected_account=deps.account,
            )
            if checkpoint is None:
                return TransitionResult(state="recovery-required")
            return _cleanup_old_workbooks(
                config_dir=config_dir,
                state_path=state_path,
                deps=deps,
                checkpoint=checkpoint,
            )

        if not (
            _is_sha256(expected_fingerprint)
            and deps.account
            and callable(deps.consent_checker)
            and callable(deps.preview_builder)
            and callable(deps.record_migrator)
            and callable(deps.ai_inspector)
            and callable(deps.resource_verifier)
            and callable(deps.candidate_finalizer)
            and callable(deps.candidate_verifier)
            and callable(deps.central_route_reader)
            and callable(deps.central_rollback)
            and callable(deps.trash_workbook)
        ):
            raise TransitionUserError("출결 파일 정리를 시작할 확인값이 없습니다.")

        if saved_state == "ai-action-required":
            checked_action = _validated_ai_action_state(
                saved, expected_fingerprint=expected_fingerprint
            )
            if checked_action is None:
                return TransitionResult(state="recovery-required")
            source_id = str(saved["source_spreadsheet_id"])
            approved_ids = tuple(saved["approved_cleanup_ids"])
            moved_row_counts = tuple(saved["moved_row_counts"])
            result = _candidate_from_state(saved.get("candidate"), pending_name)
            candidate_id = str(result.spreadsheet_id)
            if deps.consent_checker(config_dir=config_dir, account=deps.account) is not True:
                raise TransitionUserError("Google 권한 허용 기록을 확인하지 못했어요.")
            preview = deps.preview_builder(
                config_dir=config_dir,
                runner=deps.runner,
                gws_executable=deps.gws_executable,
                account=deps.account,
            )
            if not (
                getattr(preview, "state", "") == "ready"
                and str(getattr(preview, "fingerprint", "") or "").lower()
                == expected_fingerprint
                and _preview_source_ids(preview) == approved_ids
                and approved_ids.count(source_id) == 1
                and current_id == source_id
                and _consolidation_candidate_ok(
                    result,
                    forbidden_ids=approved_ids,
                    expected_name=pending_name,
                )
                and deps.candidate_verifier(
                    runner=deps.runner,
                    workdir=config_dir,
                    gws_executable=deps.gws_executable,
                    destination_spreadsheet_id=candidate_id,
                    final_name=pending_name,
                ) is True
            ):
                raise TransitionUserError("새 출석부 후보와 원본을 다시 확인하지 못했어요.")
            migration = deps.record_migrator(
                config_dir=config_dir,
                runner=deps.runner,
                gws_executable=deps.gws_executable,
                account=deps.account,
                profile=profile,
                current_record=record,
                source_spreadsheet_id=source_id,
                destination_spreadsheet_id=candidate_id,
                preview=preview,
                install_result=result,
                fingerprint=expected_fingerprint,
                progress=dict(saved["record_progress"]),
                remember=lambda _progress: None,
                verify_only=True,
            )
            if not (
                isinstance(migration, dict)
                and migration.get("verified") is True
                and migration.get("moved_row_counts") == moved_row_counts
            ):
                raise TransitionUserError("새 출석부 후보의 모든 기록을 다시 확인하지 못했어요.")
            if deps.resource_verifier(
                config_dir=config_dir,
                runner=deps.runner,
                gws_executable=deps.gws_executable,
                account=deps.account,
                source_spreadsheet_id=source_id,
                destination_spreadsheet_id=candidate_id,
                install_result=result,
            ) is not True:
                raise TransitionUserError("새 출석부가 쓰는 연결 자료를 다시 확인하지 못했어요.")
            ai_status = deps.ai_inspector(
                runner=deps.runner,
                workdir=config_dir,
                gws_executable=deps.gws_executable,
                spreadsheet_id=candidate_id,
            )
            if not (
                getattr(ai_status, "ok", False) is True
                and getattr(ai_status, "state", "") == "verified"
                and getattr(ai_status, "trigger_count", 0) == 1
            ):
                return TransitionResult(
                    state="ai-action-required",
                    spreadsheet_url=checked_action.spreadsheet_url,
                    moved_row_counts=moved_row_counts,
                )
            named = deps.candidate_finalizer(
                runner=deps.runner,
                workdir=config_dir,
                gws_executable=deps.gws_executable,
                destination_spreadsheet_id=candidate_id,
                pending_name=pending_name,
                final_name=final_name,
                data_verified=True,
                services_verified=True,
                ai_verified=True,
            )
            if named is not True:
                raise TransitionUserError("새 출석부의 정식 이름을 확인하지 못했어요.")
            write_result = replace(result, workbook_name=final_name)
            saved_switch_state = {
                "schema_version": 3,
                "state": "candidate-finalized",
                "fingerprint": expected_fingerprint,
                "source_spreadsheet_id": source_id,
                "approved_cleanup_ids": list(approved_ids),
                "candidate_spreadsheet_id": candidate_id,
                "candidate": _candidate_state(write_result),
                "spreadsheet_url": str(write_result.spreadsheet_url),
                "moved_row_counts": list(moved_row_counts),
                "trigger_count": 1,
                "total_cleanup_count": len(approved_ids),
                "remaining_cleanup_ids": list(approved_ids),
                "record_progress": dict(saved["record_progress"]),
            }
            saved_switch_state = anchored_switch_state(
                saved_switch_state,
                source_id=source_id,
                candidate_id=candidate_id,
                approved_ids=approved_ids,
                moved_row_counts=moved_row_counts,
            )
            _atomic_json(state_path, saved_switch_state)
            return finish_switch(
                result=write_result,
                source_id=source_id,
                approved_ids=approved_ids,
                moved_row_counts=moved_row_counts,
                trigger_count=1,
                central_route="source",
                switch_state=saved_switch_state,
            )

        if saved_state == "record-switch-in-flight":
            (
                resumed_result,
                source_id,
                approved_ids,
                moved_row_counts,
                trigger_count,
                _record_progress,
            ) = saved_switch_values(saved, require_approval=True)
            candidate_id = str(resumed_result.spreadsheet_id)
            route = read_central_route(source_id, candidate_id)
            if current_id == candidate_id and route in {"candidate", "not_registered"}:
                switched_state = _cleanup_state_payload(
                    state="record-switched",
                    fingerprint=expected_fingerprint,
                    candidate_spreadsheet_id=candidate_id,
                    approved_cleanup_ids=approved_ids,
                    spreadsheet_url=str(resumed_result.spreadsheet_url),
                    moved_row_counts=moved_row_counts,
                    trigger_count=trigger_count,
                    total_cleanup_count=len(approved_ids),
                    remaining_cleanup_ids=list(approved_ids),
                )
                _atomic_json(state_path, switched_state)
                checkpoint = read_validated_consolidation_checkpoint(
                    config_dir,
                    expected_fingerprint=expected_fingerprint,
                    expected_account=deps.account,
                )
                if checkpoint is None:
                    return TransitionResult(state="recovery-required")
                return _cleanup_old_workbooks(
                    config_dir=config_dir,
                    state_path=state_path,
                    deps=deps,
                    checkpoint=checkpoint,
                )
            if current_id != source_id or route == "source":
                return TransitionResult(state="failed")

        resumable_states = {
            "candidate-finalized",
            "central-move-in-flight",
            "central-move-required",
            "central-moved",
            "central-not-registered",
            "central-rollback-in-flight",
            "central-rollback-required",
            "record-switch-in-flight",
            "switch-required",
        }
        if (
            saved_state in resumable_states
            and saved.get("fingerprint") == expected_fingerprint
        ):
            (
                resumed_result,
                source_id,
                approved_ids,
                moved_row_counts,
                trigger_count,
                latest_id,
                route,
            ) = fresh_resume_validation(saved)
            if latest_id != source_id:
                return TransitionResult(state="failed")
            if saved_state == "central-move-required" and route == "source":
                return TransitionResult(state="failed")
            resume_state = anchored_switch_state(
                saved,
                source_id=source_id,
                candidate_id=str(resumed_result.spreadsheet_id),
                approved_ids=approved_ids,
                moved_row_counts=moved_row_counts,
            )
            _atomic_json(state_path, resume_state)
            if saved_state in {"central-rollback-in-flight", "central-rollback-required"}:
                if route == "candidate":
                    rollback_state = dict(resume_state)
                    rollback_state["state"] = "central-rollback-in-flight"
                    _atomic_json(state_path, rollback_state)
                    try:
                        rollback_ok = deps.central_rollback(
                            config_dir=config_dir,
                            source_spreadsheet_id=source_id,
                            candidate_spreadsheet_id=str(resumed_result.spreadsheet_id),
                            runner=deps.runner,
                            gws_executable=deps.gws_executable,
                        ) is True
                    except Exception:
                        rollback_ok = False
                    held = dict(resume_state)
                    held["state"] = (
                        "switch-required" if rollback_ok else "central-rollback-required"
                    )
                    _atomic_json(state_path, held)
                    return TransitionResult(
                        state="failed" if rollback_ok else "central-rollback-required"
                    )
                if route == "source":
                    reconciled = dict(resume_state)
                    reconciled["state"] = "switch-required"
                    _atomic_json(state_path, reconciled)
                elif route != "not_registered":
                    return TransitionResult(state="central-rollback-required")
            if saved_state == "central-moved" and route == "source":
                return TransitionResult(state="failed")
            return finish_switch(
                result=resumed_result,
                source_id=source_id,
                approved_ids=approved_ids,
                moved_row_counts=moved_row_counts,
                trigger_count=trigger_count,
                central_route=route,
                switch_state=resume_state,
            )
        if deps.consent_checker(config_dir=config_dir, account=deps.account) is not True:
            raise TransitionUserError("Google 권한 허용 기록을 확인하지 못했어요.")
        preview = deps.preview_builder(
            config_dir=config_dir,
            runner=deps.runner,
            gws_executable=deps.gws_executable,
            account=deps.account,
        )
        if not (
            getattr(preview, "state", "") == "ready"
            and str(getattr(preview, "fingerprint", "") or "").lower()
            == expected_fingerprint
        ):
            raise TransitionUserError("확인한 출결 파일 내용이 실행 직전에 달라졌어요.")
        approved_ids = _preview_source_ids(preview)
        if approved_ids.count(current_id) != 1:
            raise TransitionUserError("현재 연결이 정리할 출석부 목록과 다릅니다.")

        matching_saved = (
            saved
            if saved_state == "building"
            and saved.get("fingerprint") == expected_fingerprint
            and saved.get("source_spreadsheet_id") == current_id
            and saved.get("approved_cleanup_ids") == list(approved_ids)
            else {}
        )
        install_progress = dict(matching_saved.get("install_progress") or {})
        record_progress = dict(matching_saved.get("record_progress") or {})
        if not record_progress:
            record_progress = {
                "stage": "preview",
                "fingerprint": expected_fingerprint,
                "sources": [],
            }
        transition_state: dict[str, object] = {
            "schema_version": 2,
            "state": "building",
            "fingerprint": expected_fingerprint,
            "source_spreadsheet_id": current_id,
            "approved_cleanup_ids": list(approved_ids),
            "install_progress": dict(install_progress),
            "record_progress": dict(record_progress),
        }
        _atomic_json(state_path, transition_state)

        def remember_install(created: dict) -> None:
            install_progress.clear()
            install_progress.update(
                {str(key): str(value) for key, value in created.items() if value}
            )
            transition_state["install_progress"] = dict(install_progress)
            _atomic_json(state_path, transition_state)

        result = deps.installer(
            profile_path,
            runner=deps.runner,
            resume=install_progress,
            progress=remember_install,
            creation_reason=install_attendance_automation.ATTENDANCE_CREATION_SPLIT_REPAIR,
            source_spreadsheet_id=current_id,
            consolidation_fingerprint=expected_fingerprint,
            write_record_on_success=False,
            central_chat_sender_url="",
            gemini_api_key=install_attendance_automation.local_gemini_api_key(config_dir),
            gws_executable=deps.gws_executable,
        )
        if not _consolidation_candidate_ok(
            result, forbidden_ids=approved_ids, expected_name=pending_name
        ):
            raise TransitionUserError("새 정식 출석부 후보를 확인하지 못했어요.")
        candidate_id = str(result.spreadsheet_id)

        def remember_records(progress: dict[str, object]) -> None:
            nonlocal record_progress
            record_progress = dict(progress)
            transition_state["record_progress"] = dict(record_progress)
            _atomic_json(state_path, transition_state)

        migration = deps.record_migrator(
            config_dir=config_dir,
            runner=deps.runner,
            gws_executable=deps.gws_executable,
            account=deps.account,
            profile=profile,
            current_record=record,
            source_spreadsheet_id=current_id,
            destination_spreadsheet_id=candidate_id,
            preview=preview,
            install_result=result,
            fingerprint=expected_fingerprint,
            progress=record_progress,
            remember=remember_records,
        )
        counts_value = migration.get("moved_row_counts") if isinstance(migration, dict) else None
        if not (
            isinstance(migration, dict)
            and migration.get("verified") is True
            and isinstance(counts_value, tuple)
            and len(counts_value) == len(approved_ids)
            and all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in counts_value)
        ):
            raise TransitionUserError("새 출석부 후보의 모든 기록을 확인하지 못했어요.")
        moved_row_counts = tuple(counts_value)
        resources_verified = deps.resource_verifier(
            config_dir=config_dir,
            runner=deps.runner,
            gws_executable=deps.gws_executable,
            account=deps.account,
            source_spreadsheet_id=current_id,
            destination_spreadsheet_id=candidate_id,
            install_result=result,
        )
        if resources_verified is not True:
            raise TransitionUserError("새 출석부가 쓰는 연결 자료를 확인하지 못했어요.")
        pending_action_state = {
            "schema_version": 4,
            "state": "ai-action-required",
            "fingerprint": expected_fingerprint,
            "source_spreadsheet_id": current_id,
            "approved_cleanup_ids": list(approved_ids),
            "candidate_spreadsheet_id": candidate_id,
            "candidate": _candidate_state(result),
            "spreadsheet_url": str(result.spreadsheet_url),
            "moved_row_counts": list(moved_row_counts),
            "record_progress": dict(record_progress),
        }
        if _validated_ai_action_state(
            pending_action_state,
            expected_fingerprint=expected_fingerprint,
        ) is None:
            raise TransitionUserError("새 출석부 후보의 재개 상태를 만들지 못했어요.")
        _atomic_json(state_path, pending_action_state)
        ai_status = deps.ai_inspector(
            runner=deps.runner,
            workdir=config_dir,
            gws_executable=deps.gws_executable,
            spreadsheet_id=candidate_id,
        )
        trigger_count = getattr(ai_status, "trigger_count", 0)
        if not (
            getattr(ai_status, "ok", False) is True
            and getattr(ai_status, "state", "") == "verified"
            and trigger_count == 1
        ):
            return TransitionResult(
                state="ai-action-required",
                spreadsheet_url=str(result.spreadsheet_url),
                moved_row_counts=moved_row_counts,
            )
        named = deps.candidate_finalizer(
            runner=deps.runner,
            workdir=config_dir,
            gws_executable=deps.gws_executable,
            destination_spreadsheet_id=candidate_id,
            pending_name=pending_name,
            final_name=final_name,
            data_verified=True,
            services_verified=True,
            ai_verified=True,
        )
        if named is not True:
            raise TransitionUserError("새 출석부의 정식 이름을 확인하지 못했어요.")
        write_result = replace(result, workbook_name=final_name)
        saved_switch_state = {
            "schema_version": 3,
            "state": "candidate-finalized",
            "fingerprint": expected_fingerprint,
            "source_spreadsheet_id": current_id,
            "approved_cleanup_ids": list(approved_ids),
            "candidate_spreadsheet_id": candidate_id,
            "candidate": _candidate_state(write_result),
            "spreadsheet_url": str(write_result.spreadsheet_url),
            "moved_row_counts": list(moved_row_counts),
            "trigger_count": trigger_count,
            "total_cleanup_count": len(approved_ids),
            "remaining_cleanup_ids": list(approved_ids),
            "record_progress": dict(record_progress),
        }
        saved_switch_state = anchored_switch_state(
            saved_switch_state,
            source_id=current_id,
            candidate_id=candidate_id,
            approved_ids=approved_ids,
            moved_row_counts=moved_row_counts,
        )
        _atomic_json(state_path, saved_switch_state)
        return finish_switch(
            result=write_result,
            source_id=current_id,
            approved_ids=approved_ids,
            moved_row_counts=moved_row_counts,
            trigger_count=trigger_count,
            central_route="source",
            switch_state=saved_switch_state,
        )
    except TransitionUserError:
        return TransitionResult(state="failed")
    except Exception:  # noqa: BLE001 - 외부 원문은 화면이나 진행 기록에 보내지 않는다
        return TransitionResult(state="failed")


def start_new_school_year_workbook(
    config_dir: Path,
    *,
    deps: TransitionDeps,
) -> TransitionResult:
    """옛 학년도 자료는 복사하지 않고 정식 기본 출석부 후보로 전환한다."""

    config_dir = Path(config_dir)
    profile_path = config_dir / "profile.generated.json"
    record_path = config_dir / "attendance-install.generated.json"
    state_path = config_dir / "attendance-workbook-transition.generated.json"
    try:
        profile = _read_dict(profile_path)
        record = _read_dict(record_path)
        current_id = str(record.get("spreadsheet_id", "") or "").strip()
        if not current_id:
            raise TransitionUserError("현재 출석부 연결을 확인하지 못했어요.")
        school_year = str((profile.get("school") or {}).get("year", "") or "").strip()
        record_year = str(record.get("school_year", "") or "").strip()
        if not school_year or not record_year:
            raise TransitionUserError("현재 학년도와 새 학년도를 모두 확인하지 못했어요.")
        if school_year == record_year:
            raise TransitionUserError("학년도가 같아서 새 출석부를 만들지 않았어요.")
        if (
            str(record.get("workbook_role", "") or "")
            != attendance_workbook_identity.ATTENDANCE_ROLE_VALUE
        ):
            raise TransitionUserError("먼저 출결 시트 하나로 정리를 끝내 주세요.")

        reusable = {
            key: str(record.get(key, "") or "").strip()
            for key in (
                "template_doc_id",
                "template_doc_url",
                "folder_id",
                "task_list_id",
            )
        }
        missing = [
            key
            for key in ("template_doc_id", "folder_id", "task_list_id")
            if not reusable[key]
        ]
        if missing:
            labels = {
                "template_doc_id": "결석 신고서 양식",
                "folder_id": "출결 파일 보관 폴더",
                "task_list_id": "할 일 목록",
            }
            raise TransitionUserError(
                "기존 자료에서 다시 사용할 연결을 찾지 못해 새 학년도 출석부를 만들지 않았어요. "
                "확인할 항목: " + ", ".join(labels[key] for key in missing)
            )

        saved_state: dict = {}
        if state_path.exists():
            try:
                loaded = _read_dict(state_path)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                raise TransitionUserError(
                    "새 학년도 진행 기록을 안전하게 확인하지 못했어요."
                ) from error
            if loaded.get("reason") == (
                install_attendance_automation.ATTENDANCE_CREATION_NEW_SCHOOL_YEAR
            ):
                if not _valid_new_school_year_transition_state(loaded):
                    raise TransitionUserError(
                        "새 학년도 진행 기록을 안전하게 확인하지 못했어요."
                    )
                if (
                    loaded.get("previous_spreadsheet_id") == current_id
                    and loaded.get("school_year") == school_year
                ):
                    saved_state = loaded
            else:
                _other_state, invalid_other = _load_consolidation_transition_state(
                    state_path
                )
                if invalid_other:
                    raise TransitionUserError(
                        "저장된 출결 진행 기록을 안전하게 확인하지 못했어요."
                    )
        progress = {
            str(key): str(value)
            for key, value in dict(saved_state.get("progress") or {}).items()
            if value
        }
        for key, value in reusable.items():
            if value:
                progress.setdefault(key, value)
        transition_state = {
            "state": "building",
            "reason": install_attendance_automation.ATTENDANCE_CREATION_NEW_SCHOOL_YEAR,
            "previous_spreadsheet_id": current_id,
            "school_year": school_year,
            "progress": dict(progress),
        }
        _atomic_json(state_path, transition_state)

        def remember(created: dict) -> None:
            progress.clear()
            progress.update(
                {str(key): str(value) for key, value in created.items() if value}
            )
            transition_state["progress"] = dict(progress)
            _atomic_json(state_path, transition_state)

        result = deps.installer(
            profile_path,
            runner=deps.runner,
            resume=progress,
            progress=remember,
            attendance_task_list_id=reusable["task_list_id"],
            attendance_task_list_title="조종례시 담임학급 안내사항",
            central_chat_sender_url="",
            gemini_api_key=install_attendance_automation.local_gemini_api_key(
                config_dir
            ),
            gws_executable=deps.gws_executable,
            creation_reason=(
                install_attendance_automation.ATTENDANCE_CREATION_NEW_SCHOOL_YEAR
            ),
            write_record_on_success=False,
        )
        if not _candidate_ok(result, profile, current_id, current_id):
            raise TransitionUserError(
                "새 학년도 출석부와 자동 기능, 파일 이름을 모두 확인하지 못했어요."
            )
        candidate_id = str(result.spreadsheet_id)
        transition_state.update(
            {
                "state": "candidate-verified",
                "spreadsheet_id": candidate_id,
                "spreadsheet_url": str(result.spreadsheet_url),
            }
        )
        _atomic_json(state_path, transition_state)
        _switch_record_last(
            record_path,
            profile_path,
            result,
            write_record=deps.write_record,
        )
        return TransitionResult(
            state="complete",
            spreadsheet_url=str(result.spreadsheet_url),
        )
    except TransitionUserError:
        return TransitionResult(state="failed")
    except Exception:  # noqa: BLE001 - 외부 원문은 화면에 보내지 않는다
        return TransitionResult(state="failed")


__all__ = [
    "ConsolidationCheckpoint",
    "TransitionDeps",
    "TransitionResult",
    "consolidate_attendance_workbooks",
    "find_split_repair_sources",
    "make_transition_deps",
    "migrate_attendance_records",
    "move_central_for_transition",
    "read_validated_consolidation_checkpoint",
    "rollback_central_for_transition",
    "start_new_school_year_workbook",
    "trash_attendance_workbook",
    "verify_attendance_resources",
]
