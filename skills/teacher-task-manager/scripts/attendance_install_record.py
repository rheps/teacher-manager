"""출결 설치 기록의 연결값을 확인하고 기존 추가값을 보존한다."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


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
SCRIPT_UPDATE_REQUIRED_FIELD = "script_update_required"
SCRIPT_ATTESTATION_FIELD = "script_attestation"
SETUP_ACCOUNT_FIELD = "setup_account"
SCRIPT_ATTESTATION_SCHEMA = 1
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_RECORD_WRITE_TIMEOUT_SECONDS = 10.0
_RECORD_THREAD_LOCKS: dict[str, threading.Lock] = {}
_RECORD_THREAD_LOCKS_GUARD = threading.Lock()
_CANONICAL_WORKBOOK_ROLE = "canonical-v1"
_CANONICAL_WORKBOOK_SUFFIX = "(Teacher manager 출결 자동화)"


class AttendanceInstallRecordError(ValueError):
    """설치 기록을 안전하게 읽거나 쓸 수 없을 때 발생한다."""

    code = ATTENDANCE_RECORD_BROKEN


def _record_thread_lock(path: Path) -> threading.Lock:
    key = os.path.normcase(os.path.abspath(str(path)))
    with _RECORD_THREAD_LOCKS_GUARD:
        return _RECORD_THREAD_LOCKS.setdefault(key, threading.Lock())


def _try_lock_record_file(handle) -> bool:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock_record_file(handle) -> None:
    handle.seek(0)
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def attendance_install_record_lock(path: Path):
    """같은 설치 기록의 읽기-비교-교체를 창과 프로세스 사이에서 한 덩어리로 묶는다."""

    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _error("출결 설치 기록 잠금 폴더를 준비하지 못했어요.", cause=exc)
    lock_path = path.with_name(f".{path.name}.lock")
    local_lock = _record_thread_lock(lock_path)
    if not local_lock.acquire(timeout=_RECORD_WRITE_TIMEOUT_SECONDS):
        raise _error("다른 창의 출결 설치 기록 저장이 끝나지 않았어요.")
    handle = None
    locked = False
    try:
        try:
            handle = lock_path.open("a+b")
        except OSError as exc:
            raise _error("출결 설치 기록 잠금 파일을 열지 못했어요.", cause=exc)
        deadline = time.monotonic() + _RECORD_WRITE_TIMEOUT_SECONDS
        while not _try_lock_record_file(handle):
            if time.monotonic() >= deadline:
                raise _error("다른 프로그램의 출결 설치 기록 저장이 끝나지 않았어요.")
            time.sleep(0.02)
        locked = True
        yield
    finally:
        try:
            if handle is not None:
                try:
                    if locked:
                        _unlock_record_file(handle)
                finally:
                    handle.close()
        finally:
            local_lock.release()


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
    if (
        SCRIPT_UPDATE_REQUIRED_FIELD in record
        and type(record[SCRIPT_UPDATE_REQUIRED_FIELD]) is not bool
    ):
        raise _error("script_update_required 값은 true 또는 false여야 해요.")
    if SETUP_ACCOUNT_FIELD in record:
        setup_account = record[SETUP_ACCOUNT_FIELD]
        if (
            not isinstance(setup_account, str)
            or re.fullmatch(r"[^@\s]+@goedu\.kr", setup_account.strip(), re.IGNORECASE)
            is None
        ):
            raise _error("setup_account는 확인된 @goedu.kr 계정이어야 해요.")
    if SCRIPT_ATTESTATION_FIELD in record:
        attestation = record[SCRIPT_ATTESTATION_FIELD]
        if (
            not isinstance(attestation, dict)
            or set(attestation) != {
                "schema",
                "spreadsheet_id",
                "script_id",
                "deployment_id",
                "bundle_sha256",
            }
        ):
            raise _error("script_attestation 확인값의 모양이 올바르지 않아요.")
        schema = attestation.get("schema")
        if type(schema) is not int or schema != SCRIPT_ATTESTATION_SCHEMA:
            raise _error("script_attestation 확인값의 판을 읽을 수 없어요.")
        for key in ("spreadsheet_id", "script_id", "deployment_id"):
            value = attestation.get(key)
            if (
                not isinstance(value, str)
                or not value.strip()
                or value != record[key]
            ):
                raise _error(f"script_attestation의 {key}가 현재 연결값과 달라요.")
        bundle_sha256 = attestation.get("bundle_sha256")
        if not isinstance(bundle_sha256, str) or _SHA256_PATTERN.fullmatch(bundle_sha256) is None:
            raise _error("script_attestation의 bundle_sha256은 소문자 64자리 SHA-256이어야 해요.")
    return copy.deepcopy(record)


def build_script_attestation(record: dict, bundle_sha256: str) -> dict:
    """현재 연결 세 값에 묶인 출결 코드 확인표를 만든다."""

    if not isinstance(record, dict):
        raise _error("출결 기능 확인표를 만들 연결 기록이 없어요.")
    for key in ("spreadsheet_id", "script_id", "deployment_id"):
        value = record.get(key)
        if not isinstance(value, str) or not value.strip():
            raise _error(f"출결 기능 확인표에 필요한 {key} 연결값이 비어 있어요.")
    if not isinstance(bundle_sha256, str) or _SHA256_PATTERN.fullmatch(bundle_sha256) is None:
        raise _error("확인한 출결 기능의 SHA-256 값이 올바르지 않아요.")
    return {
        "schema": SCRIPT_ATTESTATION_SCHEMA,
        "spreadsheet_id": record.get("spreadsheet_id"),
        "script_id": record.get("script_id"),
        "deployment_id": record.get("deployment_id"),
        "bundle_sha256": bundle_sha256,
    }


def attendance_script_is_attested(record: dict, bundle_sha256: str) -> bool:
    """확인표가 지금 연결 세 값과 현재 프로그램 코드에 정확히 묶였는지 본다."""

    try:
        checked = validate_attendance_install_record(record)
    except AttendanceInstallRecordError:
        return False
    attestation = checked.get(SCRIPT_ATTESTATION_FIELD)
    return (
        isinstance(attestation, dict)
        and isinstance(bundle_sha256, str)
        and _SHA256_PATTERN.fullmatch(bundle_sha256) is not None
        and attestation.get("bundle_sha256") == bundle_sha256
    )


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


def _spreadsheet_id_from_url(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return ""
    if parsed.scheme != "https" or parsed.hostname != "docs.google.com":
        return ""
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) < 3 or parts[:2] != ["spreadsheets", "d"]:
        return ""
    return parts[2].strip()


def validate_verified_canonical_record(record: dict) -> dict:
    """평상시 열기·업데이트에 쓸 정본 표시와 Sheet 주소를 함께 확인한다."""

    record = validate_attendance_install_record(record)
    if record.get("workbook_role") != _CANONICAL_WORKBOOK_ROLE:
        raise _error("현재 연결 기록에 정식 출석부 확인 표시가 없어요.")
    spreadsheet_id = str(record.get("spreadsheet_id", "") or "").strip()
    if not spreadsheet_id:
        raise _error("정식 출석부 연결번호가 비어 있어요.")
    linked_id = _spreadsheet_id_from_url(record.get("spreadsheet_url"))
    if linked_id != spreadsheet_id:
        raise _error("정식 출석부 연결번호와 열기 주소가 서로 달라요.")
    for key in ("school_year", "workbook_name"):
        if not isinstance(record.get(key), str) or not str(record[key]).strip():
            raise _error(f"정식 출석부의 {key} 확인값이 비어 있어요.")
    school_year = str(record["school_year"]).strip()
    grade = str(record.get("homeroom_grade", "") or "").strip()
    klass = str(record.get("homeroom_class", "") or "").strip()
    stem = (
        f"{school_year}학년도 {grade}학년 {klass}반 출석부"
        if grade and klass
        else f"{school_year}학년도 출석부"
    )
    expected_name = stem + _CANONICAL_WORKBOOK_SUFFIX
    if str(record["workbook_name"]).strip() != expected_name:
        raise _error("현재 연결 기록의 출석부 이름이 정식 이름과 달라요.")
    return record


def read_verified_canonical_record(path: Path) -> dict:
    """파일을 엄격히 읽고 검증된 정본 기록만 돌려준다."""

    return validate_verified_canonical_record(load_attendance_install_record(path))


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

    path = Path(path)
    incoming = validate_attendance_install_record(record)
    with attendance_install_record_lock(path):
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
    wanted = validate_attendance_install_record(record)
    with attendance_install_record_lock(path):
        current = read_attendance_install_snapshot(path)
        if current.raw != expected.raw or current.sha256 != expected.sha256:
            raise _error("교체 직전 활성 설치 기록이 처음 읽은 내용과 달라졌어요.")
        _atomic_write(path, wanted)
        final = read_attendance_install_snapshot(path)
        if final.record != wanted:
            raise _error("교체 뒤 활성 설치 기록이 쓰려던 내용과 달라요.")
        return final


def mark_attendance_script_current(
    path: Path,
    expected: InstallRecordSnapshot,
    bundle_sha256: str,
) -> dict:
    """확인한 현재 코드 지문을 남기고 옛판 표식을 한 번에 지운다.

    화면에서 원격 Apps Script와 실제 배포판을 모두 확인한 뒤에만 부른다. 확인을
    시작할 때 읽은 기록이 다른 창에서 바뀌었으면 새 기록을 덮지 않고 멈춘다.
    """

    if not isinstance(expected, InstallRecordSnapshot):
        raise _error("출결 기능 확인을 시작할 때 읽은 설치 기록을 확인하지 못했어요.")
    if not isinstance(bundle_sha256, str) or _SHA256_PATTERN.fullmatch(bundle_sha256) is None:
        raise _error("확인한 출결 기능의 SHA-256 값이 올바르지 않아요.")
    wanted = copy.deepcopy(expected.record)
    wanted[SCRIPT_ATTESTATION_FIELD] = build_script_attestation(
        wanted, bundle_sha256
    )
    wanted.pop(SCRIPT_UPDATE_REQUIRED_FIELD, None)
    if wanted == expected.record:
        current = read_attendance_install_snapshot(path)
        if current.raw != expected.raw or current.sha256 != expected.sha256:
            raise _error("출결 기능 확인 도중 설치 기록이 다른 내용으로 바뀌었어요.")
        return copy.deepcopy(expected.record)
    return replace_attendance_install_record(path, wanted, expected).record
