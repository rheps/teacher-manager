"""출석부 연결을 바꾸기 전에 업무 기록을 새 연결 Sheet로 안전하게 인계한다."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import install_attendance_automation


@dataclass(frozen=True)
class SheetRecordHandoverResult:
    state: str
    moved_rows: int = 0


@dataclass(frozen=True)
class _RangeSpec:
    key: str
    sheet_name: str
    start_row: int
    column_count: int
    last_column: str

    @property
    def full_range(self) -> str:
        return f"'{self.sheet_name}'!A{self.start_row}:{self.last_column}"

    def exact_range(self, start_row: int, row_count: int) -> str:
        end_row = start_row + row_count - 1
        return f"'{self.sheet_name}'!A{start_row}:{self.last_column}{end_row}"


RANGE_SPECS = (
    *(
        _RangeSpec(f"month-{month}", f"{month}월", 3, 13, "M")
        for month in range(1, 13)
    ),
    _RangeSpec("personal-chat", "메신저 개인톡 내용", 2, 10, "J"),
    _RangeSpec("class-chat", "메신저 단체톡 내용", 2, 7, "G"),
    _RangeSpec("send-log", "발송기록", 2, 7, "G"),
)

PROGRESS_FILE_NAME = "attendance-connection-handover.generated.json"


class AttendanceConnectionHandoverError(RuntimeError):
    """원본과 대상을 자동으로 고치지 않고 현재 연결을 유지해야 하는 오류."""


def _compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            file.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_progress(path: Path, source_id: str, target_id: str) -> dict:
    fresh = {
        "schema_version": 2,
        "state": "moving",
        "source_spreadsheet_id": source_id,
        "target_spreadsheet_id": target_id,
        "blocks": [],
    }
    if not path.exists():
        return fresh
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise AttendanceConnectionHandoverError(
            "이전 자료 이동 기록을 안전하게 확인하지 못했어요."
        ) from error
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 2
        or not isinstance(value.get("source_spreadsheet_id"), str)
        or not isinstance(value.get("target_spreadsheet_id"), str)
        or value.get("state") not in {"moving", "complete"}
        or not isinstance(value.get("blocks"), list)
    ):
        raise AttendanceConnectionHandoverError(
            "다른 출석부의 자료 이동 기록이 남아 있어 자동으로 덮지 않았어요."
        )
    specs_by_key = {spec.key: spec for spec in RANGE_SPECS}
    seen: set[str] = set()
    for block in value["blocks"]:
        key = block.get("key") if isinstance(block, dict) else None
        spec = specs_by_key.get(key)
        row_count = block.get("row_count") if isinstance(block, dict) else None
        destination_range = (
            block.get("destination_range") if isinstance(block, dict) else None
        )
        range_match = (
            re.fullmatch(
                rf"'{re.escape(spec.sheet_name)}'!A(\d+):{spec.last_column}(\d+)",
                destination_range,
            )
            if spec is not None and isinstance(destination_range, str)
            else None
        )
        if (
            not isinstance(block, dict)
            or set(block) != {
                "key",
                "source_sha256",
                "source_row_count",
                "rows_sha256",
                "row_count",
                "destination_range",
                "state",
            }
            or spec is None
            or block["key"] in seen
            or block.get("state") not in {"write-planned", "verified"}
            or not isinstance(row_count, int)
            or row_count <= 0
            or not isinstance(block.get("source_row_count"), int)
            or block["source_row_count"] < row_count
            or not isinstance(block.get("source_sha256"), str)
            or len(block["source_sha256"]) != 64
            or not isinstance(block.get("rows_sha256"), str)
            or len(block["rows_sha256"]) != 64
            or range_match is None
            or int(range_match.group(1)) < spec.start_row
            or int(range_match.group(2))
            != int(range_match.group(1)) + row_count - 1
        ):
            raise AttendanceConnectionHandoverError(
                "이전 자료 이동 기록을 안전하게 확인하지 못했어요."
            )
        seen.add(block["key"])
    same_pair = (
        value.get("source_spreadsheet_id") == source_id
        and value.get("target_spreadsheet_id") == target_id
    )
    if not same_pair:
        if value.get("state") == "complete":
            return fresh
        raise AttendanceConnectionHandoverError(
            "다른 출석부의 자료 이동이 끝나지 않아 자동으로 덮지 않았어요."
        )
    return value


def _valid_cell(value: object) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _read_rows(
    *,
    runner,
    workdir: Path,
    gws_executable: str,
    spreadsheet_id: str,
    range_name: str,
    column_count: int,
    keep_empty_positions: bool,
) -> tuple[tuple[object, ...], ...]:
    reply = install_attendance_automation.run_json(
        runner,
        [
            gws_executable,
            "sheets",
            "spreadsheets",
            "values",
            "get",
            "--params",
            _compact(
                {
                    "spreadsheetId": spreadsheet_id,
                    "range": range_name,
                    "majorDimension": "ROWS",
                    "valueRenderOption": "UNFORMATTED_VALUE",
                    "dateTimeRenderOption": "SERIAL_NUMBER",
                }
            ),
            "--format",
            "json",
        ],
        workdir,
    )
    if not isinstance(reply, dict) or reply.get("majorDimension") != "ROWS":
        raise AttendanceConnectionHandoverError("Google Sheet 자료 모양을 확인하지 못했어요.")
    values = reply.get("values", [])
    if not isinstance(values, list):
        raise AttendanceConnectionHandoverError("Google Sheet 자료 모양을 확인하지 못했어요.")
    normalized: list[tuple[object, ...]] = []
    for row in values:
        if (
            not isinstance(row, list)
            or len(row) > column_count
            or not all(_valid_cell(cell) for cell in row)
        ):
            raise AttendanceConnectionHandoverError(
                "Google Sheet 자료 모양을 확인하지 못했어요."
            )
        padded = tuple("" if cell is None else cell for cell in row) + ("",) * (
            column_count - len(row)
        )
        if keep_empty_positions or any(cell != "" for cell in padded):
            normalized.append(padded)
    return tuple(normalized)


def _rows_sha256(rows: tuple[tuple[object, ...], ...]) -> str:
    raw = json.dumps(
        rows,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _missing_occurrences(
    source_rows: tuple[tuple[object, ...], ...],
    target_rows: tuple[tuple[object, ...], ...],
) -> tuple[tuple[object, ...], ...]:
    """대상에 이미 있는 동일 행의 개수만큼만 빼고 원본 순서를 유지한다."""

    remaining = Counter(target_rows)
    missing: list[tuple[object, ...]] = []
    for row in source_rows:
        if remaining[row] > 0:
            remaining[row] -= 1
        else:
            missing.append(row)
    return tuple(missing)


def _write_rows(
    *,
    runner,
    workdir: Path,
    gws_executable: str,
    spreadsheet_id: str,
    range_name: str,
    rows: tuple[tuple[object, ...], ...],
) -> None:
    reply = install_attendance_automation.run_json(
        runner,
        [
            gws_executable,
            "sheets",
            "spreadsheets",
            "values",
            "update",
            "--params",
            _compact(
                {
                    "spreadsheetId": spreadsheet_id,
                    "range": range_name,
                    "valueInputOption": "RAW",
                }
            ),
            "--json",
            _compact({"majorDimension": "ROWS", "values": rows}),
            "--format",
            "json",
        ],
        workdir,
    )
    if not isinstance(reply, dict) or int(reply.get("updatedRows", -1)) != len(rows):
        raise AttendanceConnectionHandoverError("Google Sheet 자료를 옮긴 결과를 확인하지 못했어요.")


def _last_used_offset(rows: tuple[tuple[object, ...], ...]) -> int:
    last = 0
    for index, row in enumerate(rows, start=1):
        if any(cell != "" for cell in row):
            last = index
    return last


def handover_sheet_records(
    config_dir: Path,
    *,
    source_spreadsheet_id: str,
    target_spreadsheet_id: str,
    runner,
    gws_executable: str,
) -> SheetRecordHandoverResult:
    """업무 본문을 진행 파일에 남기지 않고 모든 기록 묶음을 한 번씩 인계한다."""

    workdir = Path(config_dir)
    source_id = str(source_spreadsheet_id or "").strip()
    target_id = str(target_spreadsheet_id or "").strip()
    gws = str(gws_executable or "").strip()
    if not source_id or not target_id or source_id == target_id or not gws:
        raise AttendanceConnectionHandoverError("옮길 출석부 연결을 확인하지 못했어요.")

    progress_path = workdir / PROGRESS_FILE_NAME
    progress = _read_progress(progress_path, source_id, target_id)
    progress_by_key = {block["key"]: block for block in progress["blocks"]}
    moved_rows = 0
    for spec in RANGE_SPECS:
        source_rows = _read_rows(
            runner=runner,
            workdir=workdir,
            gws_executable=gws,
            spreadsheet_id=source_id,
            range_name=spec.full_range,
            column_count=spec.column_count,
            keep_empty_positions=False,
        )
        existing = progress_by_key.get(spec.key)
        if not source_rows:
            if existing is not None:
                raise AttendanceConnectionHandoverError(
                    "이전 출석부 자료가 이동을 시작한 뒤 바뀌었어요."
                )
            continue
        source_sha256 = _rows_sha256(source_rows)
        if existing is not None:
            if (
                existing["source_sha256"] != source_sha256
                or existing["source_row_count"] != len(source_rows)
            ):
                raise AttendanceConnectionHandoverError(
                    "이전 출석부 자료가 이동을 시작한 뒤 바뀌었어요."
                )
            block = existing
            destination_range = block["destination_range"]
            rows_to_move: tuple[tuple[object, ...], ...] = ()
        else:
            target_rows = _read_rows(
                runner=runner,
                workdir=workdir,
                gws_executable=gws,
                spreadsheet_id=target_id,
                range_name=spec.full_range,
                column_count=spec.column_count,
                keep_empty_positions=True,
            )
            rows_to_move = _missing_occurrences(source_rows, target_rows)
            if not rows_to_move:
                continue
            destination_start = spec.start_row + _last_used_offset(target_rows)
            destination_range = spec.exact_range(destination_start, len(rows_to_move))
            block = {
                "key": spec.key,
                "source_sha256": source_sha256,
                "source_row_count": len(source_rows),
                "rows_sha256": _rows_sha256(rows_to_move),
                "row_count": len(rows_to_move),
                "destination_range": destination_range,
                "state": "write-planned",
            }
            progress["blocks"].append(block)
            progress_by_key[spec.key] = block
            progress["state"] = "moving"
            progress.pop("moved_rows", None)
            _atomic_json(progress_path, progress)
        verified = _read_rows(
            runner=runner,
            workdir=workdir,
            gws_executable=gws,
            spreadsheet_id=target_id,
            range_name=destination_range,
            column_count=spec.column_count,
            keep_empty_positions=True,
        )
        verified_matches = (
            len(verified) == block["row_count"]
            and _rows_sha256(verified) == block["rows_sha256"]
        )
        if not verified_matches:
            if any(cell != "" for row in verified for cell in row):
                raise AttendanceConnectionHandoverError(
                    "대상 출석부의 붙일 자리에 다른 자료가 있어 자동으로 덮지 않았어요."
                )
            if existing is not None:
                target_rows = _read_rows(
                    runner=runner,
                    workdir=workdir,
                    gws_executable=gws,
                    spreadsheet_id=target_id,
                    range_name=spec.full_range,
                    column_count=spec.column_count,
                    keep_empty_positions=True,
                )
                rows_to_move = _missing_occurrences(source_rows, target_rows)
                if (
                    len(rows_to_move) != block["row_count"]
                    or _rows_sha256(rows_to_move) != block["rows_sha256"]
                ):
                    raise AttendanceConnectionHandoverError(
                        "대상 출석부 자료가 이동을 시작한 뒤 바뀌었어요."
                    )
            _write_rows(
                runner=runner,
                workdir=workdir,
                gws_executable=gws,
                spreadsheet_id=target_id,
                range_name=destination_range,
                rows=rows_to_move,
            )
            verified = _read_rows(
                runner=runner,
                workdir=workdir,
                gws_executable=gws,
                spreadsheet_id=target_id,
                range_name=destination_range,
                column_count=spec.column_count,
                keep_empty_positions=True,
            )
            if not (
                len(verified) == block["row_count"]
                and _rows_sha256(verified) == block["rows_sha256"]
            ):
                raise AttendanceConnectionHandoverError(
                    "Google Sheet에 옮긴 자료를 다시 확인하지 못했어요."
                )
        block["state"] = "verified"
        moved_rows += block["row_count"]
        _atomic_json(progress_path, progress)

    progress["state"] = "complete"
    progress["moved_rows"] = moved_rows
    _atomic_json(progress_path, progress)
    return SheetRecordHandoverResult(state="complete", moved_rows=moved_rows)


__all__ = [
    "AttendanceConnectionHandoverError",
    "PROGRESS_FILE_NAME",
    "SheetRecordHandoverResult",
    "handover_sheet_records",
]
