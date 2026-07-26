"""출결 설치 기록의 연결값을 확인하고 기존 추가값을 보존한다."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ATTENDANCE_RECORD_BROKEN = "ATTENDANCE_RECORD_BROKEN"

CONNECTION_FIELDS = {
    "spreadsheet_id",
    "spreadsheet_url",
    "template_doc_id",
    "template_doc_url",
    "script_id",
    "deployment_id",
    "folder_id",
    "task_list_id",
}


class AttendanceInstallRecordError(ValueError):
    """설치 기록을 안전하게 읽거나 쓸 수 없을 때 발생한다."""

    code = ATTENDANCE_RECORD_BROKEN


@dataclass(frozen=True)
class InstallRecordSnapshot:
    """같은 시점의 원본 바이트, 엄격히 읽은 값, 바이트 지문."""

    raw: bytes
    record: dict[str, Any]
    sha256: str


def _error(
    detail: str,
    *,
    cause: Exception | None = None,
) -> AttendanceInstallRecordError:
    error = AttendanceInstallRecordError(
        f"{ATTENDANCE_RECORD_BROKEN}: 출결 설치 기록을 사용할 수 없어요. {detail}"
    )
    if cause is not None:
        error.__cause__ = cause
    return error


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _error(f"같은 항목이 두 번 적혀 있어요: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise _error(f"JSON에서 쓸 수 없는 숫자가 있어요: {value}")


def _check_json_value(value: Any, location: str = "기록") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise _error(f"{location}의 항목 이름은 글자여야 해요.")
            _check_json_value(nested, f"{location}.{key}")
        return
    if isinstance(value, list):
        for index, nested in enumerate(value):
            _check_json_value(nested, f"{location}[{index}]")
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise _error(f"{location}에 NaN이나 무한대 값은 넣을 수 없어요.")
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    raise _error(f"{location}에 JSON으로 저장할 수 없는 값이 있어요.")


def validate_attendance_install_record(record: dict) -> dict:
    """8개 연결값을 확인하고 그 밖의 정상 JSON 값은 그대로 돌려준다."""

    if not isinstance(record, dict):
        raise _error("파일 맨 바깥은 JSON 객체여야 해요.")
    _check_json_value(record)
    missing = sorted(CONNECTION_FIELDS - set(record))
    if missing:
        raise _error(f"필수 연결값이 빠졌어요: {', '.join(missing)}")
    for key in CONNECTION_FIELDS:
        if not isinstance(record[key], str):
            raise _error(f"{key} 연결값은 글자여야 해요.")
    return copy.deepcopy(record)


def _parse_attendance_install_raw(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise _error("파일을 UTF-8 글자로 끝까지 읽지 못했어요.", cause=exc)
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except AttendanceInstallRecordError:
        raise
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise _error("JSON 내용이 잘렸거나 형식이 맞지 않아요.", cause=exc)
    return validate_attendance_install_record(parsed)


def read_attendance_install_snapshot(path: Path) -> InstallRecordSnapshot:
    """원본 바이트와 엄격한 기록, SHA-256을 한 번의 읽기로 묶는다."""

    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise _error("파일을 끝까지 읽지 못했어요.", cause=exc)
    return InstallRecordSnapshot(
        raw=raw,
        record=_parse_attendance_install_raw(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def load_attendance_install_record(path: Path) -> dict:
    """중복 이름과 잘린 JSON을 거절하고 설치 기록을 읽는다."""

    return copy.deepcopy(read_attendance_install_snapshot(path).record)


def ensure_create_only_install_backup(
    backup_path: Path,
    expected: InstallRecordSnapshot,
) -> InstallRecordSnapshot:
    """없을 때만 원본 바이트를 쓰고, 있으면 같은 백업만 재사용한다."""

    if not isinstance(expected, InstallRecordSnapshot):
        raise _error("백업할 설치 기록 snapshot을 확인하지 못했어요.")
    backup_path = Path(backup_path)
    try:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            backup_file = backup_path.open("xb")
        except FileExistsError:
            backup_file = None
        if backup_file is not None:
            with backup_file:
                backup_file.write(expected.raw)
                backup_file.flush()
                os.fsync(backup_file.fileno())
    except OSError as exc:
        raise _error(
            "기존 설치 기록의 고정 백업을 새 파일로 만들지 못했어요.",
            cause=exc,
        )

    backup = read_attendance_install_snapshot(backup_path)
    if backup.raw != expected.raw:
        raise _error(
            "기존 고정 백업이 지금 원본 바이트와 달라서 덮어쓸 수 없어요."
        )
    if backup.record != expected.record or backup.sha256 != expected.sha256:
        raise _error("고정 백업을 다시 확인한 결과가 원본과 달라요.")
    return backup


def _atomic_write(path: Path, record: dict) -> dict:
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}-",
            suffix=".tmp",
            dir=str(path.parent),
        )
    except OSError as exc:
        raise _error("옆 임시 파일을 만들지 못했어요.", cause=exc)

    temp_path = Path(temp_name)
    try:
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
                file.write(
                    json.dumps(
                        record,
                        ensure_ascii=False,
                        indent=2,
                        allow_nan=False,
                    )
                    + "\n"
                )
                file.flush()
                os.fsync(file.fileno())
        except (OSError, TypeError, ValueError) as exc:
            raise _error("임시 파일을 끝까지 쓰지 못했어요.", cause=exc)

        checked = load_attendance_install_record(temp_path)
        if checked != record:
            raise _error("임시 파일을 다시 읽은 값이 쓰려던 내용과 달라요.")
        try:
            os.replace(temp_path, path)
        except OSError as exc:
            raise _error("확인한 임시 파일로 기존 기록을 바꾸지 못했어요.", cause=exc)
        return checked
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def write_attendance_install_record(path: Path, record: dict) -> dict:
    """연결값을 쓰되 기존의 알 수 없는 정상 JSON 값은 바꾸지 않는다."""

    incoming = validate_attendance_install_record(record)
    path = Path(path)
    if path.exists():
        current = load_attendance_install_record(path)
        merged = copy.deepcopy(current)
        for key in CONNECTION_FIELDS:
            merged[key] = incoming[key]
        for key, value in incoming.items():
            if key not in CONNECTION_FIELDS and key not in merged:
                merged[key] = copy.deepcopy(value)
    else:
        merged = incoming
    return _atomic_write(path, validate_attendance_install_record(merged))


def replace_attendance_install_record(
    path: Path,
    record: dict,
    expected: InstallRecordSnapshot,
) -> InstallRecordSnapshot:
    """활성 파일이 처음 읽은 바이트 그대로일 때만 한 번에 교체한다."""

    if not isinstance(expected, InstallRecordSnapshot):
        raise _error("교체 전 설치 기록 snapshot을 확인하지 못했어요.")
    path = Path(path)
    current = read_attendance_install_snapshot(path)
    if current.raw != expected.raw or current.sha256 != expected.sha256:
        raise _error("교체 직전 활성 설치 기록이 처음 읽은 내용과 달라졌어요.")
    wanted = validate_attendance_install_record(record)
    _atomic_write(path, wanted)
    final = read_attendance_install_snapshot(path)
    if final.record != wanted:
        raise _error("교체 뒤 활성 설치 기록이 쓰려던 내용과 달라요.")
    return final
