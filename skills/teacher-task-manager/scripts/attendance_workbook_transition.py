"""사용자가 확인한 새 학년도 전환만 수행한다.

과거의 여러 출석부 통합·기록 이관·Google Drive 휴지통 이동 기능은 제거했다.
이 모듈은 기존 출석부를 그대로 둔 채 새 학년도 출석부 후보를 완성하고, 모든
확인이 끝난 뒤 로컬의 현재 연결번호만 마지막에 바꾼다.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

import attendance_workbook_identity
import install_attendance_automation


@dataclass
class TransitionDeps:
    installer: Callable
    write_record: Callable
    gws_executable: str
    runner: Callable | None = None
    account: str = ""


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


def make_transition_deps(*, runner, gws_executable: str, account: str) -> TransitionDeps:
    return TransitionDeps(
        installer=install_attendance_automation.install_attendance_automation,
        write_record=install_attendance_automation.write_install_record,
        gws_executable=gws_executable,
        runner=runner,
        account=account,
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
        text = path.read_bytes().decode("utf-8")
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
            file.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
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
    if state == "candidate-verified":
        required |= {"spreadsheet_id", "spreadsheet_url"}
    elif state != "building":
        return False
    previous_id = value.get("previous_spreadsheet_id")
    progress = value.get("progress")
    if not (
        set(value) == required
        and isinstance(previous_id, str)
        and _GOOGLE_ID_PATTERN.fullmatch(previous_id) is not None
        and isinstance(value.get("school_year"), str)
        and re.fullmatch(r"20[0-9]{2}", value["school_year"]) is not None
        and _valid_install_progress(progress, previous_id)
    ):
        return False
    if state == "candidate-verified":
        candidate_id = value.get("spreadsheet_id")
        return bool(
            isinstance(candidate_id, str)
            and _GOOGLE_ID_PATTERN.fullmatch(candidate_id) is not None
            and candidate_id != previous_id
            and value.get("spreadsheet_url")
            == f"https://docs.google.com/spreadsheets/d/{candidate_id}/edit"
        )
    return True


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
            record_path.unlink(missing_ok=True)
        else:
            _atomic_bytes(record_path, previous)
        raise


def start_new_school_year_workbook(
    config_dir: Path,
    *,
    deps: TransitionDeps,
) -> TransitionResult:
    """명시적인 새 학년도 요청에서만 새 출석부를 만들고 마지막에 연결한다."""

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
        if str(record.get("workbook_role", "") or "") != (
            attendance_workbook_identity.ATTENDANCE_ROLE_VALUE
        ):
            raise TransitionUserError("현재 사용할 출석부 연결을 먼저 다시 골라 주세요.")

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
                "기존 자료에서 다시 사용할 연결을 찾지 못해 새 학년도 출석부를 "
                "만들지 않았어요. 확인할 항목: "
                + ", ".join(labels[key] for key in missing)
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
                if not _valid_new_school_year_state(loaded):
                    raise TransitionUserError(
                        "새 학년도 진행 기록을 안전하게 확인하지 못했어요."
                    )
                if (
                    loaded.get("previous_spreadsheet_id") == current_id
                    and loaded.get("school_year") == school_year
                ):
                    saved_state = loaded
            # 옛 통합 진행표는 실행하지 않는다. 사용자가 누른 새 학년도 진행표로
            # 안전하게 덮고, Google의 기존 파일은 전혀 건드리지 않는다.

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
        if isinstance(result, install_attendance_automation.AttendanceInstallResult):
            result = replace(result, setup_account=deps.account.strip().lower())
        if not _candidate_ok(result, profile, current_id):
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
        return TransitionResult(state="failed", detail=NEW_SCHOOL_YEAR_FAILURE)
    except Exception:  # noqa: BLE001 - 외부 원문은 화면에 보내지 않는다
        return TransitionResult(state="failed", detail=NEW_SCHOOL_YEAR_FAILURE)


__all__ = [
    "NEW_SCHOOL_YEAR_FAILURE",
    "TransitionDeps",
    "TransitionResult",
    "make_transition_deps",
    "start_new_school_year_workbook",
]
