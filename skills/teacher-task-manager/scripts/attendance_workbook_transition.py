"""출결 후보를 끝까지 확인한 뒤 현재 연결을 마지막에 바꾼다."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import install_attendance_automation
import attendance_central_move
import attendance_workbook_identity


@dataclass
class TransitionDeps:
    source_finder: Callable
    installer: Callable
    write_record: Callable
    central_mover: Callable
    gws_executable: str
    runner: Callable | None = None


@dataclass(frozen=True)
class TransitionResult:
    state: str
    detail: str = ""
    source_spreadsheet_id: str = ""
    spreadsheet_id: str = ""
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
    )


def _read_dict(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
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


def consolidate_attendance_workbooks(
    config_dir: Path,
    *,
    deps: TransitionDeps,
) -> TransitionResult:
    """예전 출결 원본을 새 정식 파일로 복사하고 연결번호를 마지막에 바꾼다."""

    config_dir = Path(config_dir)
    profile_path = config_dir / "profile.generated.json"
    record_path = config_dir / "attendance-install.generated.json"
    state_path = config_dir / "attendance-workbook-transition.generated.json"
    try:
        profile = _read_dict(profile_path)
        record = _read_dict(record_path) if record_path.exists() else {}
        current_id = str(record.get("spreadsheet_id", "") or "").strip()
        if record_path.exists() and not current_id:
            raise TransitionUserError("현재 출석부 연결을 확인하지 못했어요.")
        candidates = list(
            deps.source_finder(
                profile,
                deps.runner,
                config_dir,
                deps.gws_executable,
            )
            or []
        )
        if len(candidates) != 1:
            if candidates:
                names = _candidate_names(candidates)
                shown = ", ".join(names) if names else "이름을 읽지 못한 출석부"
                raise TransitionUserError(
                    "예전에 쓰던 출석부가 여러 개라 자동으로 고르지 않았어요. "
                    f"파일 이름: {shown}. Google Drive에서 파일 이름으로 열어 확인한 뒤 "
                    "사용할 파일 하나만 남기고 다시 시도해 주세요."
                )
            raise TransitionUserError(
                "예전에 쓰던 출석부를 한 개로 확인하지 못했어요. "
                "Google Drive에서 기존 출석부가 열리는지 확인해 주세요."
            )
        source = candidates[0]
        source_id = str(source.get("id", "") or "").strip()
        if not source_id:
            raise TransitionUserError("정리할 기존 출석부의 연결을 확인하지 못했어요.")

        saved_state: dict = {}
        if state_path.exists():
            try:
                loaded = _read_dict(state_path)
            except (OSError, ValueError, json.JSONDecodeError):
                loaded = {}
            if (
                loaded.get("reason")
                == install_attendance_automation.ATTENDANCE_CREATION_SPLIT_REPAIR
                and loaded.get("source_spreadsheet_id") == source_id
                and loaded.get("school_year")
                == str((profile.get("school") or {}).get("year", "") or "")
            ):
                saved_state = loaded
        progress = dict(saved_state.get("progress") or {})
        transition_state = {
            "state": "building",
            "reason": install_attendance_automation.ATTENDANCE_CREATION_SPLIT_REPAIR,
            "source_spreadsheet_id": source_id,
            "school_year": str(
                (profile.get("school") or {}).get("year", "") or ""
            ),
            "progress": progress,
            "central_complete": saved_state.get("central_complete") is True,
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
            creation_reason=(
                install_attendance_automation.ATTENDANCE_CREATION_SPLIT_REPAIR
            ),
            source_spreadsheet_id=source_id,
            write_record_on_success=False,
            central_chat_sender_url="",
            gemini_api_key=install_attendance_automation.local_gemini_api_key(
                config_dir
            ),
            gws_executable=deps.gws_executable,
        )
        if not _candidate_ok(result, profile, source_id, current_id):
            raise TransitionUserError(
                "새 정식 출석부와 자동 기능, 파일 이름을 모두 확인하지 못했어요."
            )
        candidate_id = str(result.spreadsheet_id)
        if transition_state["central_complete"] is not True:
            moved = deps.central_mover(
                config_dir=config_dir,
                source_spreadsheet_id=source_id,
                destination_spreadsheet_id=candidate_id,
                runner=deps.runner,
                gws_executable=deps.gws_executable,
            )
            if moved is not True and str(getattr(moved, "outcome", "")) not in {
                "moved",
                "not_registered",
            }:
                raise TransitionUserError("학급 단톡방 연결을 옮긴 결과를 확인하지 못했어요.")
            transition_state["central_complete"] = True

        # 이 진행 기록까지 완성한 뒤 현재 연결번호를 바꾼다. 아래 교체 뒤에는 다른
        # 로컬 파일을 쓰지 않아, 교체 성공과 화면 결과가 어긋나지 않게 한다.
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
            source_spreadsheet_id=source_id,
            spreadsheet_id=candidate_id,
            spreadsheet_url=str(result.spreadsheet_url),
        )
    except TransitionUserError as error:
        return TransitionResult(state="failed", detail=str(error))
    except Exception:  # noqa: BLE001 - 외부 원문은 화면에 보내지 않는다
        return TransitionResult(state="failed", detail=CONSOLIDATION_FAILURE)


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
            except (OSError, ValueError, json.JSONDecodeError):
                loaded = {}
            if (
                loaded.get("reason")
                == install_attendance_automation.ATTENDANCE_CREATION_NEW_SCHOOL_YEAR
                and loaded.get("previous_spreadsheet_id") == current_id
                and loaded.get("school_year") == school_year
            ):
                saved_state = loaded
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
            source_spreadsheet_id=current_id,
            spreadsheet_id=candidate_id,
            spreadsheet_url=str(result.spreadsheet_url),
        )
    except TransitionUserError as error:
        return TransitionResult(state="failed", detail=str(error))
    except Exception:  # noqa: BLE001 - 외부 원문은 화면에 보내지 않는다
        return TransitionResult(state="failed", detail=NEW_SCHOOL_YEAR_FAILURE)


__all__ = [
    "TransitionDeps",
    "TransitionResult",
    "consolidate_attendance_workbooks",
    "find_split_repair_sources",
    "make_transition_deps",
    "move_central_for_transition",
    "start_new_school_year_workbook",
]
