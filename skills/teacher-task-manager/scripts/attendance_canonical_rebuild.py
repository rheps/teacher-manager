"""같은 이름의 출결 파일 전체를 읽어 쓰기 전 안전 미리보기를 만든다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Callable, Mapping, Sequence


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import attendance_script_update
import attendance_workbook_identity
import install_attendance_automation
from attendance_install_record import (
    AttendanceInstallRecordError,
    load_attendance_install_record,
)


SPREADSHEET_MIME_TYPE = "application/vnd.google-apps.spreadsheet"
MONTH_NAMES = tuple(f"{month}월" for month in range(1, 13))
AI_INPUT_HINTS = frozenset(
    {
        "",
        "AI 출결 입력",
        "AI 출결 입력 (준비 중)",
        '여기에 "3월 12일 김철수 병결" 처럼 적고 Enter를 누르세요',
    }
)
IDENTITY_SETTING_LABELS = {
    "SCHOOL_NAME": "학교",
    "SCHOOL_YEAR": "학년도",
    "GRADE": "학년",
    "CLASS_NUMBER": "반",
    "TEACHER_NAME": "담임",
}
RECORD_RANGES = (
    *( (name, f"'{name}'!A3:M", 3, 13) for name in MONTH_NAMES ),
    ("개인톡", "'메신저 개인톡 내용'!A2:J", 2, 10),
    ("단체톡", "'메신저 단체톡 내용'!A2:G", 2, 7),
    ("발송기록", "'발송기록'!A2:G", 2, 7),
)
RECORD_DESTINATIONS = {
    **{
        name: (name, 3, 13, "M")
        for name in MONTH_NAMES
    },
    "개인톡": ("메신저 개인톡 내용", 2, 10, "J"),
    "단체톡": ("메신저 단체톡 내용", 2, 7, "G"),
    "발송기록": ("발송기록", 2, 7, "G"),
}
AUTHORITATIVE_RANGES = {
    "'학생명단'!A1:D": 4,
    "'휴일'!A1:F": 6,
}

_FAILED_DETAIL = (
    "출결 파일 목록과 자료를 안전하게 끝까지 확인하지 못했어요. "
    "기존 출결 자료와 현재 연결은 바꾸지 않았습니다."
)


@dataclass(frozen=True)
class SourceWorkbook:
    spreadsheet_id: str
    name: str
    created_time: str
    modified_time: str


@dataclass(frozen=True)
class RecordBlock:
    source_spreadsheet_id: str
    sheet_name: str
    start_row: int
    rows: tuple[tuple[object, ...], ...]
    sha256: str


@dataclass(frozen=True)
class ConsolidationPreview:
    state: str
    fingerprint: str
    sources: tuple[SourceWorkbook, ...]
    counts_by_source: tuple[dict[str, object], ...]
    total_rows: int
    detail: str = ""


@dataclass(frozen=True)
class ConsolidationEligibility:
    state: str
    sources: tuple[SourceWorkbook, ...] = ()
    detail: str = ""


class _PreviewHold(RuntimeError):
    pass


class CanonicalRebuildHold(RuntimeError):
    """후보를 자동으로 덮거나 지우지 않고 현재 위치에서 멈추는 상태."""


def _need(condition: Any) -> None:
    if not condition:
        raise _PreviewHold()


def _read_dict(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    _need(isinstance(value, dict))
    return value


def _compact(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _run_json(runner, args: Sequence[str], workdir: Path) -> Any:
    # Apps Script 안전 점검과 같은 혼합 출력/단일 JSON 검사 규칙을 쓴다.
    return attendance_script_update._run_one_json(runner, args, Path(workdir))


def _drive_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _valid_time(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _source_from_drive_item(
    item: Any,
    *,
    account: str,
    expected_name: str | None = None,
) -> SourceWorkbook:
    _need(isinstance(item, dict))
    spreadsheet_id = item.get("id")
    name = item.get("name")
    owners = item.get("owners")
    _need(
        isinstance(spreadsheet_id, str)
        and bool(spreadsheet_id.strip())
        and isinstance(name, str)
        and bool(name.strip())
        and (expected_name is None or name == expected_name)
        and item.get("mimeType") == SPREADSHEET_MIME_TYPE
        and item.get("trashed") is False
        and item.get("ownedByMe") is True
        and isinstance(owners, list)
        and bool(owners)
        and all(isinstance(owner, dict) for owner in owners)
        and any(owner.get("emailAddress") == account for owner in owners)
        and _valid_time(item.get("createdTime"))
        and _valid_time(item.get("modifiedTime"))
    )
    return SourceWorkbook(
        spreadsheet_id=spreadsheet_id,
        name=name,
        created_time=item["createdTime"],
        modified_time=item["modifiedTime"],
    )


def _discover_sources(
    *,
    runner,
    workdir: Path,
    gws_executable: str,
    account: str,
    title: str,
    current_spreadsheet_id: str,
) -> tuple[SourceWorkbook, ...]:
    current_reply = _run_json(
        runner,
        [
            gws_executable,
            "drive",
            "files",
            "get",
            "--params",
            _compact(
                {
                    "fileId": current_spreadsheet_id,
                    "fields": (
                        "id,name,mimeType,trashed,ownedByMe,owners,"
                        "createdTime,modifiedTime"
                    ),
                    "supportsAllDrives": True,
                }
            ),
            "--format",
            "json",
        ],
        workdir,
    )
    current = _source_from_drive_item(current_reply, account=account)
    _need(current.spreadsheet_id == current_spreadsheet_id)
    query = (
        f"name = '{_drive_quote(title)}' and "
        f"mimeType = '{SPREADSHEET_MIME_TYPE}' and "
        f"trashed = false and '{_drive_quote(account)}' in owners"
    )
    page_token = ""
    seen_tokens: set[str] = set()
    found: list[SourceWorkbook] = [current]
    seen_ids: set[str] = {current.spreadsheet_id}
    while True:
        params: dict[str, Any] = {
            "q": query,
            "fields": (
                "nextPageToken,incompleteSearch,"
                "files(id,name,mimeType,trashed,ownedByMe,owners,createdTime,modifiedTime)"
            ),
            "pageSize": 1000,
            "supportsAllDrives": True,
        }
        if page_token:
            params["pageToken"] = page_token
        reply = _run_json(
            runner,
            [
                gws_executable,
                "drive",
                "files",
                "list",
                "--params",
                _compact(params),
                "--format",
                "json",
            ],
            workdir,
        )
        _need(isinstance(reply, dict) and reply.get("incompleteSearch") is False)
        files = reply.get("files", [])
        _need(isinstance(files, list))
        for item in files:
            source = _source_from_drive_item(
                item, account=account, expected_name=title
            )
            if source.spreadsheet_id == current.spreadsheet_id:
                # 저장된 ID가 권위다. 검색 결과의 같은 항목은 중복으로 세지 않는다.
                continue
            _need(source.spreadsheet_id not in seen_ids)
            seen_ids.add(source.spreadsheet_id)
            found.append(source)
        next_token = reply.get("nextPageToken", "")
        _need(isinstance(next_token, str))
        if not next_token:
            break
        _need(next_token not in seen_tokens)
        seen_tokens.add(next_token)
        page_token = next_token

    return tuple(sorted(found, key=lambda source: (source.created_time, source.spreadsheet_id)))


def _valid_cell(value: Any) -> bool:
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
) -> tuple[tuple[object, ...], ...]:
    params = {
        "spreadsheetId": spreadsheet_id,
        "range": range_name,
        "majorDimension": "ROWS",
        "valueRenderOption": "UNFORMATTED_VALUE",
        "dateTimeRenderOption": "SERIAL_NUMBER",
    }
    reply = _run_json(
        runner,
        [
            gws_executable,
            "sheets",
            "spreadsheets",
            "values",
            "get",
            "--params",
            _compact(params),
            "--format",
            "json",
        ],
        workdir,
    )
    _need(
        isinstance(reply, dict)
        and isinstance(reply.get("range"), str)
        and bool(reply["range"])
        and reply.get("majorDimension") == "ROWS"
    )
    values = reply.get("values", [])
    _need(isinstance(values, list))
    normalized: list[tuple[object, ...]] = []
    for row in values:
        _need(
            isinstance(row, list)
            and len(row) <= column_count
            and all(_valid_cell(cell) for cell in row)
        )
        padded = tuple("" if cell is None else cell for cell in row) + ("",) * (
            column_count - len(row)
        )
        if any(cell != "" for cell in padded):
            normalized.append(padded)
    return tuple(normalized)


def _rows_sha256(rows: tuple[tuple[object, ...], ...]) -> str:
    serialized = json.dumps(
        rows,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _read_record_blocks(
    *,
    source: SourceWorkbook,
    runner,
    workdir: Path,
    gws_executable: str,
) -> tuple[RecordBlock, ...]:
    blocks: list[RecordBlock] = []
    for sheet_name, range_name, start_row, column_count in RECORD_RANGES:
        rows = _read_rows(
            runner=runner,
            workdir=workdir,
            gws_executable=gws_executable,
            spreadsheet_id=source.spreadsheet_id,
            range_name=range_name,
            column_count=column_count,
        )
        blocks.append(
            RecordBlock(
                source_spreadsheet_id=source.spreadsheet_id,
                sheet_name=sheet_name,
                start_row=start_row,
                rows=rows,
                sha256=_rows_sha256(rows),
            )
        )
    return tuple(blocks)


def _read_settings(
    *, source: SourceWorkbook, runner, workdir: Path, gws_executable: str
) -> dict[str, object]:
    rows = _read_rows(
        runner=runner,
        workdir=workdir,
        gws_executable=gws_executable,
        spreadsheet_id=source.spreadsheet_id,
        range_name="'설정'!A1:D200",
        column_count=4,
    )
    settings: dict[str, object] = {}
    for row in rows:
        key = str(row[0]).strip()
        if not key:
            continue
        _need(key not in settings)
        settings[key] = row[1]
    return settings


def _read_roster(
    *, source: SourceWorkbook, runner, workdir: Path, gws_executable: str
) -> tuple[tuple[object, ...], ...]:
    return _read_rows(
        runner=runner,
        workdir=workdir,
        gws_executable=gws_executable,
        spreadsheet_id=source.spreadsheet_id,
        range_name="'학생명단'!A2:D200",
        column_count=4,
    )


def _unprocessed_ai_months(
    *, source: SourceWorkbook, runner, workdir: Path, gws_executable: str
) -> tuple[str, ...]:
    pending: list[str] = []
    for month_name in MONTH_NAMES:
        month_has_pending_input = False
        for cell in ("A1", "B1"):
            rows = _read_rows(
                runner=runner,
                workdir=workdir,
                gws_executable=gws_executable,
                spreadsheet_id=source.spreadsheet_id,
                range_name=f"'{month_name}'!{cell}",
                column_count=1,
            )
            if not rows:
                continue
            _need(len(rows) == 1)
            shown = str(rows[0][0]).strip()
            if shown not in AI_INPUT_HINTS:
                month_has_pending_input = True
        if month_has_pending_input:
            pending.append(month_name)
    return tuple(pending)


def _identity_value(value: object) -> str:
    return str(value if value is not None else "").strip()


def _source_label(source: SourceWorkbook) -> str:
    return f"{source.name} ({source.created_time})"


def _preview(
    state: str,
    *,
    sources: tuple[SourceWorkbook, ...] = (),
    counts_by_source: tuple[dict[str, object], ...] = (),
    total_rows: int = 0,
    fingerprint: str = "",
    detail: str = "",
) -> ConsolidationPreview:
    return ConsolidationPreview(
        state=state,
        fingerprint=fingerprint,
        sources=sources,
        counts_by_source=counts_by_source,
        total_rows=total_rows,
        detail=detail,
    )


def _fingerprint(
    *,
    account: str,
    current_spreadsheet_id: str,
    title: str,
    sources: tuple[SourceWorkbook, ...],
    blocks: tuple[RecordBlock, ...],
    current_script_sha256: str,
) -> str:
    value = {
        "account": account,
        "current_spreadsheet_id": current_spreadsheet_id,
        "title": title,
        "sources": [
            {
                "source_spreadsheet_id": source.spreadsheet_id,
                "modified_time": source.modified_time,
            }
            for source in sources
        ],
        "blocks": [
            {
                "source_spreadsheet_id": block.source_spreadsheet_id,
                "sheet_name": block.sheet_name,
                "row_count": len(block.rows),
                "sha256": block.sha256,
            }
            for block in blocks
        ],
        "current_script_sha256": current_script_sha256,
    }
    serialized = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def inspect_consolidation_eligibility(
    *,
    config_dir: Path,
    runner,
    gws_executable: str,
    account: str,
) -> ConsolidationEligibility:
    """현재 저장 ID와 정확한 정식 이름 파일을 한 계약으로 읽는다."""

    config_dir = Path(config_dir)
    record_path = config_dir / "attendance-install.generated.json"
    if not record_path.exists():
        return ConsolidationEligibility(
            "setup-required",
            detail="현재 출석부 연결 기록이 없어 먼저 다시 연결해야 합니다.",
        )
    try:
        record = load_attendance_install_record(record_path)
    except (AttendanceInstallRecordError, OSError):
        return ConsolidationEligibility(
            "setup-required",
            detail="현재 출석부 연결 기록이 올바르지 않아 먼저 다시 연결해야 합니다.",
        )
    try:
        profile = _read_dict(config_dir / "profile.generated.json")
        current_id = str(record.get("spreadsheet_id", "") or "").strip()
        account = str(account or "").strip()
        gws = str(gws_executable or "").strip()
        _need(current_id and account and gws and callable(runner))
        title = attendance_workbook_identity.attendance_workbook_name(profile)
        sources = _discover_sources(
            runner=runner,
            workdir=config_dir,
            gws_executable=gws,
            account=account,
            title=title,
            current_spreadsheet_id=current_id,
        )
        next(source for source in sources if source.spreadsheet_id == current_id)
        state = "current" if len(sources) == 1 else "ready"
        return ConsolidationEligibility(state, sources)
    except Exception:
        return ConsolidationEligibility("failed", detail=_FAILED_DETAIL)


def build_consolidation_preview(
    *,
    config_dir: Path,
    runner,
    gws_executable: str,
    account: str,
) -> ConsolidationPreview:
    """외부 자료를 읽기만 하며 화면에 업무 내용이나 비밀값을 돌려주지 않는다."""

    config_dir = Path(config_dir)
    record_path = config_dir / "attendance-install.generated.json"
    if not record_path.exists():
        return _preview(
            "setup-required",
            detail=(
                "이 컴퓨터에 현재 출석부 연결 기록이 없어 정본을 안전하게 고를 수 "
                "없습니다. 먼저 기존 출석부를 다시 연결해 주세요."
            ),
        )
    try:
        record = load_attendance_install_record(record_path)
    except (AttendanceInstallRecordError, OSError):
        return _preview(
            "setup-required",
            detail=(
                "이 컴퓨터의 현재 출석부 연결 기록이 올바르지 않아 정본을 안전하게 "
                "고를 수 없습니다. 먼저 기존 출석부를 다시 연결해 주세요."
            ),
        )
    try:
        profile = _read_dict(config_dir / "profile.generated.json")
        current_id = str(record.get("spreadsheet_id", "") or "").strip()
        script_id = str(record.get("script_id", "") or "").strip()
        deployment_id = str(record.get("deployment_id", "") or "").strip()
        account = str(account or "").strip()
        gws_executable = str(gws_executable or "").strip()
        title = attendance_workbook_identity.attendance_workbook_name(profile)
        _need(
            bool(current_id)
            and bool(script_id)
            and bool(deployment_id)
            and bool(account)
            and bool(gws_executable)
            and callable(runner)
        )

        eligibility = inspect_consolidation_eligibility(
            config_dir=config_dir,
            runner=runner,
            gws_executable=gws_executable,
            account=account,
        )
        if eligibility.state == "setup-required":
            return _preview("setup-required", detail=eligibility.detail)
        _need(eligibility.state in {"current", "ready"})
        sources = eligibility.sources
        if eligibility.state == "current":
            return _preview("current", sources=sources)
        inspected = attendance_script_update.inspect_attendance_script_update(
            current_id,
            script_id,
            deployment_id,
            assets_dir=SCRIPTS_DIR.parent / "assets",
            runner=runner,
            gws_executable=gws_executable,
        )
        if inspected.state == "customized":
            return _preview(
                "customized",
                sources=sources,
                detail=(
                    "현재 연결된 출석부의 자동 기능에 직접 바꾼 내용이 있을 수 있어 "
                    "자료 통합을 시작하지 않았습니다."
                ),
            )
        if inspected.state in {"update_available", "finishing_required"}:
            return _preview(
                "script-update-required",
                sources=sources,
                detail="현재 연결된 출석부의 자동 기능을 먼저 최신판으로 맞춰 주세요.",
            )
        _need(inspected.state == "current" and inspected.verified is True)
        script_sha256 = str(inspected.current_bundle_sha256 or "")
        _need(len(script_sha256) == 64)

        settings_by_id: dict[str, dict[str, object]] = {}
        rosters_by_id: dict[str, tuple[tuple[object, ...], ...]] = {}
        ai_pending: list[tuple[SourceWorkbook, tuple[str, ...]]] = []
        all_blocks: list[RecordBlock] = []
        summaries: list[dict[str, object]] = []
        total_rows = 0
        for source in sources:
            settings_by_id[source.spreadsheet_id] = _read_settings(
                source=source,
                runner=runner,
                workdir=config_dir,
                gws_executable=gws_executable,
            )
            rosters_by_id[source.spreadsheet_id] = _read_roster(
                source=source,
                runner=runner,
                workdir=config_dir,
                gws_executable=gws_executable,
            )
            pending_months = _unprocessed_ai_months(
                source=source,
                runner=runner,
                workdir=config_dir,
                gws_executable=gws_executable,
            )
            if pending_months:
                ai_pending.append((source, pending_months))
            blocks = _read_record_blocks(
                source=source,
                runner=runner,
                workdir=config_dir,
                gws_executable=gws_executable,
            )
            all_blocks.extend(blocks)
            counts = {block.sheet_name: len(block.rows) for block in blocks}
            source_total = sum(counts.values())
            total_rows += source_total
            summaries.append(
                {
                    "name": source.name,
                    "created_time": source.created_time,
                    "modified_time": source.modified_time,
                    "counts": counts,
                    "total_rows": source_total,
                }
            )

        fingerprint = _fingerprint(
            account=account,
            current_spreadsheet_id=current_id,
            title=title,
            sources=sources,
            blocks=tuple(all_blocks),
            current_script_sha256=script_sha256,
        )
        counts_by_source = tuple(summaries)
        current_settings = settings_by_id[current_id]
        current_roster = rosters_by_id[current_id]

        conflicts: list[tuple[SourceWorkbook, list[str]]] = []
        for source in sources:
            if source.spreadsheet_id == current_id:
                continue
            categories: list[str] = []
            if rosters_by_id[source.spreadsheet_id] != current_roster:
                categories.append("학생명단")
            source_settings = settings_by_id[source.spreadsheet_id]
            for key, label in IDENTITY_SETTING_LABELS.items():
                if _identity_value(source_settings.get(key)) != _identity_value(
                    current_settings.get(key)
                ):
                    categories.append(label)
            if categories:
                conflicts.append((source, categories))
        if conflicts:
            detail = "설정 차이를 확인해 주세요: " + "; ".join(
                f"{_source_label(source)} - {', '.join(categories)}"
                for source, categories in conflicts
            )
            return _preview(
                "conflict",
                sources=sources,
                counts_by_source=counts_by_source,
                total_rows=total_rows,
                fingerprint=fingerprint,
                detail=detail,
            )

        if ai_pending:
            detail = "처리되지 않은 AI 입력을 확인해 주세요: " + "; ".join(
                f"{_source_label(source)} - {', '.join(months)}"
                for source, months in ai_pending
            )
            return _preview(
                "unprocessed-ai",
                sources=sources,
                counts_by_source=counts_by_source,
                total_rows=total_rows,
                fingerprint=fingerprint,
                detail=detail,
            )

        local_key = install_attendance_automation.local_gemini_api_key(config_dir)
        sheet_key = _identity_value(current_settings.get("GEMINI_API_KEY"))
        if local_key and sheet_key and local_key != sheet_key:
            return _preview(
                "key-conflict",
                sources=sources,
                counts_by_source=counts_by_source,
                total_rows=total_rows,
                fingerprint=fingerprint,
                detail=(
                    "이 컴퓨터와 현재 출석부에 저장된 AI 연결값이 서로 달라 "
                    "자동으로 고르지 않았습니다."
                ),
            )

        return _preview(
            "ready",
            sources=sources,
            counts_by_source=counts_by_source,
            total_rows=total_rows,
            fingerprint=fingerprint,
        )
    except Exception:  # 외부 원문, 계정, 경로와 비밀값을 화면으로 내보내지 않는다.
        return _preview("failed", detail=_FAILED_DETAIL)


def _sheet_range(sheet_name: str, cell_range: str) -> str:
    return "'" + sheet_name.replace("'", "''") + "'!" + cell_range


def _checked_record_blocks(blocks: tuple[RecordBlock, ...]) -> None:
    if not isinstance(blocks, tuple):
        raise CanonicalRebuildHold("출결 기록 묶음의 모양을 확인하지 못했어요.")
    seen: set[tuple[str, str]] = set()
    for block in blocks:
        if not isinstance(block, RecordBlock):
            raise CanonicalRebuildHold("출결 기록 묶음의 모양을 확인하지 못했어요.")
        identity = (block.source_spreadsheet_id, block.sheet_name)
        destination = RECORD_DESTINATIONS.get(block.sheet_name)
        if (
            not block.source_spreadsheet_id
            or identity in seen
            or destination is None
            or block.start_row != destination[1]
            or not isinstance(block.rows, tuple)
        ):
            raise CanonicalRebuildHold("출결 기록 묶음의 순서와 범위를 확인하지 못했어요.")
        seen.add(identity)
        width = destination[2]
        if any(
            not isinstance(row, tuple)
            or len(row) != width
            or not any(cell != "" for cell in row)
            or not all(_valid_cell(cell) for cell in row)
            for row in block.rows
        ):
            raise CanonicalRebuildHold("출결 기록 묶음의 값 모양을 확인하지 못했어요.")
        if block.sha256 != _rows_sha256(block.rows):
            raise CanonicalRebuildHold("출결 기록 묶음의 지문이 읽은 값과 다릅니다.")


def _checked_progress(progress: dict[str, object]) -> dict[str, object]:
    if not isinstance(progress, dict) or set(progress) != {
        "stage",
        "fingerprint",
        "sources",
    }:
        raise CanonicalRebuildHold("출결 정리 진행 기록의 모양을 확인하지 못했어요.")
    fingerprint = progress.get("fingerprint")
    sources = progress.get("sources")
    if not (
        isinstance(progress.get("stage"), str)
        and isinstance(fingerprint, str)
        and len(fingerprint) == 64
        and all(character in "0123456789abcdef" for character in fingerprint)
        and isinstance(sources, list)
    ):
        raise CanonicalRebuildHold("출결 정리 진행 기록을 확인하지 못했어요.")
    copied_sources: list[dict[str, object]] = []
    for source in sources:
        if not isinstance(source, dict) or set(source) not in ({
            "source_spreadsheet_id",
            "sheet_name",
            "row_count",
            "appended_range",
            "sha256",
            "trashed",
        }, {
            "source_spreadsheet_id",
            "sheet_name",
            "row_count",
            "appended_range",
            "sha256",
            "trashed",
            "write_state",
        }):
            raise CanonicalRebuildHold("출결 정리 진행 범위를 확인하지 못했어요.")
        migrated = dict(source)
        # 스키마가 알려진 옛 진행 기록만 명시적으로 옮긴다. 옛 기록은 쓰기 직전
        # 저장됐으므로 완료로 추정하지 않고, 대상 범위를 다시 읽는 planned로 둔다.
        migrated.setdefault("write_state", "planned")
        if migrated["write_state"] not in {"planned", "confirmed"}:
            raise CanonicalRebuildHold("출결 정리 진행 상태를 확인하지 못했어요.")
        copied_sources.append(migrated)
    return {
        "stage": str(progress["stage"]),
        "fingerprint": fingerprint,
        "sources": copied_sources,
    }


def _progress_identity(value: Mapping[str, object]) -> tuple[str, str]:
    return (str(value.get("source_spreadsheet_id", "")), str(value.get("sheet_name", "")))


def _expected_record_progress(
    blocks: tuple[RecordBlock, ...],
) -> tuple[tuple[RecordBlock, str], ...]:
    next_rows = {
        sheet_name: destination[1]
        for sheet_name, destination in RECORD_DESTINATIONS.items()
    }
    expected: list[tuple[RecordBlock, str]] = []
    for block in blocks:
        if not block.rows:
            continue
        destination_title, _start_row, _column_count, end_column = RECORD_DESTINATIONS[
            block.sheet_name
        ]
        first_row = next_rows[block.sheet_name]
        last_row = first_row + len(block.rows) - 1
        expected.append(
            (
                block,
                _sheet_range(
                    destination_title, f"A{first_row}:{end_column}{last_row}"
                ),
            )
        )
        next_rows[block.sheet_name] = last_row + 1
    return tuple(expected)


def _validate_saved_progress_prefix(
    blocks: tuple[RecordBlock, ...],
    entries: list[dict[str, object]],
    *,
    require_complete: bool,
) -> tuple[tuple[RecordBlock, str], ...]:
    expected = _expected_record_progress(blocks)
    if len(entries) > len(expected) or (require_complete and len(entries) != len(expected)):
        raise CanonicalRebuildHold("출결 정리 진행 기록의 순서를 확인하지 못했어요.")
    for index, entry in enumerate(entries):
        block, expected_range = expected[index]
        if (
            _progress_identity(entry)
            != (block.source_spreadsheet_id, block.sheet_name)
            or entry.get("row_count") != len(block.rows)
            or entry.get("appended_range") != expected_range
            or entry.get("sha256") != block.sha256
            or entry.get("trashed") is not False
            or entry.get("write_state") not in {"planned", "confirmed"}
        ):
            raise CanonicalRebuildHold(
                "출결 정리 진행 기록의 순서와 범위가 원본 묶음과 다릅니다."
            )
    if require_complete and any(
        entry.get("write_state") != "confirmed" for entry in entries
    ):
        raise CanonicalRebuildHold("출결 정리 완료 범위를 다시 확인하지 못했어요.")
    return expected


def _read_record_range(
    *,
    runner,
    workdir: Path,
    gws_executable: str,
    spreadsheet_id: str,
    range_name: str,
    column_count: int,
) -> tuple[tuple[object, ...], ...]:
    try:
        return _read_rows(
            runner=runner,
            workdir=workdir,
            gws_executable=gws_executable,
            spreadsheet_id=spreadsheet_id,
            range_name=range_name,
            column_count=column_count,
        )
    except Exception as error:
        raise CanonicalRebuildHold(
            "새 출석부 후보의 기록 범위를 안전하게 다시 읽지 못했어요."
        ) from error


def append_record_blocks(
    *,
    runner,
    workdir: Path,
    gws_executable: str,
    destination_spreadsheet_id: str,
    blocks: tuple[RecordBlock, ...],
    progress: dict[str, object],
    remember: Callable[[dict[str, object]], None],
) -> dict[str, object]:
    """각 원본 묶음을 한 번만 이어 붙이고, 같은 범위를 즉시 다시 읽는다."""

    if not callable(runner) or not callable(remember):
        raise CanonicalRebuildHold("출결 기록을 옮길 준비를 확인하지 못했어요.")
    destination_id = str(destination_spreadsheet_id or "").strip()
    gws_executable = str(gws_executable or "").strip()
    if not destination_id or not gws_executable:
        raise CanonicalRebuildHold("새 출석부 후보의 연결을 확인하지 못했어요.")
    _checked_record_blocks(blocks)
    state = _checked_progress(progress)
    entries = state["sources"]
    assert isinstance(entries, list)
    expected_progress = _validate_saved_progress_prefix(
        blocks,
        entries,
        require_complete=state["stage"] == "records-complete",
    )
    expected_ranges = {
        (block.source_spreadsheet_id, block.sheet_name): exact_range
        for block, exact_range in expected_progress
    }
    entries_by_identity: dict[tuple[str, str], dict[str, object]] = {}
    blocks_by_identity = {
        (block.source_spreadsheet_id, block.sheet_name): block for block in blocks
    }
    for entry in entries:
        assert isinstance(entry, dict)
        identity = _progress_identity(entry)
        if identity in entries_by_identity or identity not in blocks_by_identity:
            raise CanonicalRebuildHold("출결 정리 진행 기록이 이번 원본과 다릅니다.")
        entries_by_identity[identity] = entry

    verified_rows: dict[str, list[tuple[object, ...]]] = {
        name: [] for name in RECORD_DESTINATIONS
    }
    for block in blocks:
        destination_title, start_row, column_count, end_column = RECORD_DESTINATIONS[
            block.sheet_name
        ]
        identity = (block.source_spreadsheet_id, block.sheet_name)
        saved = entries_by_identity.get(identity)
        if not block.rows:
            if saved is not None:
                raise CanonicalRebuildHold("빈 출결 기록에 붙인 범위가 남아 있어 멈췄어요.")
            continue
        if saved is not None:
            if (
                saved.get("row_count") != len(block.rows)
                or saved.get("sha256") != block.sha256
                or saved.get("trashed") is not False
                or not isinstance(saved.get("appended_range"), str)
                or saved.get("write_state") not in {"planned", "confirmed"}
            ):
                raise CanonicalRebuildHold("출결 정리 진행 기록이 원본 묶음과 다릅니다.")
            exact_range = str(saved["appended_range"])
            reread = _read_record_range(
                runner=runner,
                workdir=Path(workdir),
                gws_executable=gws_executable,
                spreadsheet_id=destination_id,
                range_name=exact_range,
                column_count=column_count,
            )
            if reread == block.rows and _rows_sha256(reread) == block.sha256:
                if saved["write_state"] == "planned":
                    saved["write_state"] = "confirmed"
                    remember(
                        {
                            "stage": state["stage"],
                            "fingerprint": state["fingerprint"],
                            "sources": [dict(item) for item in entries],
                        }
                    )
                verified_rows[block.sheet_name].extend(block.rows)
                continue
            if saved["write_state"] == "confirmed" or reread:
                raise CanonicalRebuildHold(
                    "전에 붙인 출결 범위가 원본과 달라 덮어쓰거나 지우지 않았어요."
                )
            # planned 범위가 아직 비어 있으면, 그 앞의 확인 완료 범위가 정확한
            # 경우에만 같은 쓰기를 한 번 더 보낼 수 있다.
            full_range = _sheet_range(
                destination_title, f"A{start_row}:{end_column}"
            )
            current_rows = _read_record_range(
                runner=runner,
                workdir=Path(workdir),
                gws_executable=gws_executable,
                spreadsheet_id=destination_id,
                range_name=full_range,
                column_count=column_count,
            )
            if current_rows != tuple(verified_rows[block.sheet_name]):
                raise CanonicalRebuildHold(
                    "새 출석부 후보에 진행 기록으로 설명되지 않는 줄이 있어 덮어쓰지 않았어요."
                )
            exact_range = str(saved["appended_range"])
        else:
            full_range = _sheet_range(
                destination_title, f"A{start_row}:{end_column}"
            )
            current_rows = _read_record_range(
                runner=runner,
                workdir=Path(workdir),
                gws_executable=gws_executable,
                spreadsheet_id=destination_id,
                range_name=full_range,
                column_count=column_count,
            )
            if current_rows != tuple(verified_rows[block.sheet_name]):
                raise CanonicalRebuildHold(
                    "새 출석부 후보에 진행 기록으로 설명되지 않는 줄이 있어 덮어쓰지 않았어요."
                )
            exact_range = expected_ranges[identity]
            planned = {
                "source_spreadsheet_id": block.source_spreadsheet_id,
                "sheet_name": block.sheet_name,
                "row_count": len(block.rows),
                "appended_range": exact_range,
                "sha256": block.sha256,
                "trashed": False,
                "write_state": "planned",
            }
            entries.append(planned)
            entries_by_identity[identity] = planned
            saved = planned
            state["stage"] = "records"
            remember(
                {
                    "stage": state["stage"],
                    "fingerprint": state["fingerprint"],
                    "sources": [dict(item) for item in entries],
                }
            )
        try:
            updated = _run_json(
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
                            "spreadsheetId": destination_id,
                            "range": exact_range,
                            "valueInputOption": "RAW",
                        }
                    ),
                    "--json",
                    json.dumps(
                        {
                            "majorDimension": "ROWS",
                            "values": [list(row) for row in block.rows],
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                    "--format",
                    "json",
                ],
                Path(workdir),
            )
        except Exception as error:
            raise CanonicalRebuildHold(
                "출결 기록을 붙인 응답을 끝까지 받지 못했어요. 같은 범위를 확인한 뒤에만 이어갑니다."
            ) from error
        if not (
            isinstance(updated, dict)
            and updated.get("updatedRange") == exact_range
            and updated.get("updatedRows") == len(block.rows)
        ):
            raise CanonicalRebuildHold(
                "출결 기록을 붙인 정확한 범위를 확인하지 못했어요."
            )
        reread = _read_record_range(
            runner=runner,
            workdir=Path(workdir),
            gws_executable=gws_executable,
            spreadsheet_id=destination_id,
            range_name=exact_range,
            column_count=column_count,
        )
        if reread != block.rows or _rows_sha256(reread) != block.sha256:
            raise CanonicalRebuildHold(
                "새 출석부 후보에 붙인 출결 기록을 다시 읽은 값이 원본과 다릅니다."
            )
        assert saved is not None
        saved["write_state"] = "confirmed"
        verified_rows[block.sheet_name].extend(block.rows)
        remember(
            {
                "stage": state["stage"],
                "fingerprint": state["fingerprint"],
                "sources": [dict(item) for item in entries],
            }
        )

    state["stage"] = "records-complete"
    completed = {
        "stage": state["stage"],
        "fingerprint": state["fingerprint"],
        "sources": [dict(item) for item in entries],
    }
    remember(completed)
    return completed


def _normalized_authority_rows(value: object, width: int) -> tuple[tuple[object, ...], ...]:
    if not isinstance(value, (tuple, list)):
        raise CanonicalRebuildHold("기준 자료의 모양을 확인하지 못했어요.")
    rows: list[tuple[object, ...]] = []
    for row in value:
        if not isinstance(row, (tuple, list)) or len(row) > width:
            raise CanonicalRebuildHold("기준 자료의 줄 모양을 확인하지 못했어요.")
        normalized = tuple("" if cell is None else cell for cell in row) + ("",) * (
            width - len(row)
        )
        if not all(_valid_cell(cell) for cell in normalized):
            raise CanonicalRebuildHold("기준 자료의 값 모양을 확인하지 못했어요.")
        if any(cell != "" for cell in normalized):
            rows.append(normalized)
    return tuple(rows)


def verify_rebuilt_workbook(
    *,
    runner,
    workdir: Path,
    gws_executable: str,
    destination_spreadsheet_id: str,
    authoritative_snapshot,
    blocks: tuple[RecordBlock, ...],
    progress: dict[str, object],
) -> bool:
    """기준 탭, 설정 연결, 전체 줄 수와 모든 붙인 범위를 다시 대조한다."""

    try:
        _checked_record_blocks(blocks)
        state = _checked_progress(progress)
        _need(state["stage"] == "records-complete")
        entries = state["sources"]
        _need(isinstance(entries, list))
        _validate_saved_progress_prefix(blocks, entries, require_complete=True)
        _need(isinstance(authoritative_snapshot, Mapping))
        ranges = authoritative_snapshot.get("ranges")
        expected_settings = authoritative_snapshot.get("settings")
        _need(
            isinstance(ranges, Mapping)
            and set(ranges) == set(AUTHORITATIVE_RANGES)
            and isinstance(expected_settings, Mapping)
            and bool(expected_settings)
        )
        destination_id = str(destination_spreadsheet_id or "").strip()
        _need(bool(destination_id) and bool(str(gws_executable or "").strip()))
        for range_name, width in AUTHORITATIVE_RANGES.items():
            expected = _normalized_authority_rows(ranges[range_name], width)
            actual = _read_rows(
                runner=runner,
                workdir=Path(workdir),
                gws_executable=gws_executable,
                spreadsheet_id=destination_id,
                range_name=range_name,
                column_count=width,
            )
            _need(actual == expected)

        settings_rows = _read_rows(
            runner=runner,
            workdir=Path(workdir),
            gws_executable=gws_executable,
            spreadsheet_id=destination_id,
            range_name="'설정'!A1:D200",
            column_count=4,
        )
        settings: dict[str, object] = {}
        for row in settings_rows:
            key = str(row[0]).strip()
            if key:
                _need(key not in settings)
                settings[key] = row[1]
        for key, expected in expected_settings.items():
            _need(isinstance(key, str) and bool(key) and settings.get(key) == expected)

        expected_entries = [block for block in blocks if block.rows]
        _need(len(entries) == len(expected_entries))
        entry_by_identity: dict[tuple[str, str], dict[str, object]] = {}
        for entry in entries:
            _need(isinstance(entry, dict))
            identity = _progress_identity(entry)
            _need(identity not in entry_by_identity)
            entry_by_identity[identity] = entry
        expected_by_sheet: dict[str, list[tuple[object, ...]]] = {
            name: [] for name in RECORD_DESTINATIONS
        }
        for block in expected_entries:
            identity = (block.source_spreadsheet_id, block.sheet_name)
            entry = entry_by_identity.get(identity)
            _need(
                isinstance(entry, dict)
                and entry.get("row_count") == len(block.rows)
                and entry.get("sha256") == block.sha256
                and entry.get("trashed") is False
                and isinstance(entry.get("appended_range"), str)
            )
            destination_title, _start, width, _end_column = RECORD_DESTINATIONS[
                block.sheet_name
            ]
            exact_rows = _read_rows(
                runner=runner,
                workdir=Path(workdir),
                gws_executable=gws_executable,
                spreadsheet_id=destination_id,
                range_name=str(entry["appended_range"]),
                column_count=width,
            )
            _need(exact_rows == block.rows and _rows_sha256(exact_rows) == block.sha256)
            expected_by_sheet[block.sheet_name].extend(block.rows)
        _need(sum(len(rows) for rows in expected_by_sheet.values()) == sum(
            len(block.rows) for block in blocks
        ))
        for sheet_name, expected_rows in expected_by_sheet.items():
            destination_title, start_row, width, end_column = RECORD_DESTINATIONS[
                sheet_name
            ]
            actual_rows = _read_rows(
                runner=runner,
                workdir=Path(workdir),
                gws_executable=gws_executable,
                spreadsheet_id=destination_id,
                range_name=_sheet_range(
                    destination_title, f"A{start_row}:{end_column}"
                ),
                column_count=width,
            )
            _need(actual_rows == tuple(expected_rows))
        return True
    except Exception:
        return False


def finalize_rebuilt_workbook_name(
    *,
    runner,
    workdir: Path,
    gws_executable: str,
    destination_spreadsheet_id: str,
    pending_name: str,
    final_name: str,
    data_verified: bool,
    services_verified: bool,
    ai_verified: bool,
) -> bool:
    """세 확인이 모두 끝난 뒤에만 정식 이름으로 바꾸고 Drive에서 재확인한다."""

    if not (
        data_verified is True
        and services_verified is True
        and ai_verified is True
    ):
        return False
    try:
        destination_id = str(destination_spreadsheet_id or "").strip()
        pending_name = str(pending_name or "").strip()
        final_name = str(final_name or "").strip()
        _need(
            bool(destination_id)
            and bool(pending_name)
            and bool(final_name)
            and pending_name != final_name
            and callable(runner)
            and bool(str(gws_executable or "").strip())
        )

        def read_file() -> dict[str, object]:
            value = _run_json(
                runner,
                [
                    gws_executable,
                    "drive",
                    "files",
                    "get",
                    "--params",
                    _compact(
                        {
                            "fileId": destination_id,
                            "fields": "id,name,mimeType,trashed",
                        }
                    ),
                    "--format",
                    "json",
                ],
                Path(workdir),
            )
            _need(
                isinstance(value, dict)
                and value.get("id") == destination_id
                and value.get("mimeType") == SPREADSHEET_MIME_TYPE
                and value.get("trashed") is False
                and isinstance(value.get("name"), str)
            )
            return value

        before = read_file()
        _need(before["name"] in {pending_name, final_name})
        if before["name"] == pending_name:
            updated = _run_json(
                runner,
                [
                    gws_executable,
                    "drive",
                    "files",
                    "update",
                    "--params",
                    _compact(
                        {
                            "fileId": destination_id,
                            "fields": "id,name,mimeType,trashed",
                        }
                    ),
                    "--json",
                    _compact({"name": final_name}),
                    "--format",
                    "json",
                ],
                Path(workdir),
            )
            _need(
                isinstance(updated, dict)
                and updated.get("id") == destination_id
                and updated.get("name") == final_name
            )
        after = read_file()
        return after.get("id") == destination_id and after.get("name") == final_name
    except Exception:
        return False


__all__ = [
    "CanonicalRebuildHold",
    "ConsolidationPreview",
    "RecordBlock",
    "SourceWorkbook",
    "append_record_blocks",
    "build_consolidation_preview",
    "finalize_rebuilt_workbook_name",
    "verify_rebuilt_workbook",
]
