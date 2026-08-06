from __future__ import annotations

import argparse
import csv
import json
import os
import re
import stat
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from brity_bridge import bundle_paths


DAYS = ["월", "화", "수", "목", "금"]
LAST_PERIOD_KEYS = {
    "월": "월요일마지막교시",
    "화": "화요일마지막교시",
    "수": "수요일마지막교시",
    "목": "목요일마지막교시",
    "금": "금요일마지막교시",
}
CLASS_MINUTES_BY_LEVEL = {
    "초": 40,
    "초등": 40,
    "초등학교": 40,
    "중": 45,
    "중등": 45,
    "중학교": 45,
    "고": 50,
    "고등": 50,
    "고등학교": 50,
}
BASE_REQUIRED_PROFILE_KEYS = [
    "선생님이름",
    "학교명",
    "학교급",
    "담임여부",
    "출근시간",
    "퇴근시간",
    "조회시작",
    "1교시시작",
    "점심종료시간",
    "월요일마지막교시",
    "화요일마지막교시",
    "수요일마지막교시",
    "목요일마지막교시",
    "금요일마지막교시",
]
LINK_REQUIRED_PROFILE_KEYS = ["업무캘린더ID", "학사일정캘린더ID"]
HOMEROOM_BASE_REQUIRED_KEYS = ["담임학년", "담임반"]
HOMEROOM_LINK_REQUIRED_KEYS = ["담임안내Tasks목록ID"]
TIME_KEYS = ["출근시간", "퇴근시간", "조회시작", "1교시시작", "점심종료시간"]


def parse_config_dir(config_dir: str | Path, *, require_links: bool = True) -> Path:
    config_dir = Path(config_dir)
    _reject_reparse_components(config_dir, "개인 설정 폴더")
    profile_path = config_dir / "teacher-profile.csv"
    xlsx_path = config_dir / "weekly-timetable.xlsx"
    csv_path = config_dir / "weekly-timetable.csv"
    for source in (profile_path, xlsx_path, csv_path):
        if source.exists() or source.is_symlink():
            _reject_reparse_components(source, source.name)
    profile = _read_profile_csv(profile_path)
    # 옛 견본(5교시시작)으로 만든 설정 파일도 계속 읽는다. 새 이름이 우선이다.
    if not profile.get("점심종료시간") and profile.get("5교시시작"):
        profile["점심종료시간"] = profile["5교시시작"]
    timetable_rows = _read_timetable(config_dir)

    _require_profile_values(profile, require_links=require_links)
    for key in TIME_KEYS:
        _parse_time(profile[key], key)
    homeroom_enabled = _is_homeroom_teacher(profile["담임여부"])
    class_minutes = _class_minutes(profile["학교급"])
    last_periods = {day: _last_period_value(profile[LAST_PERIOD_KEYS[day]], LAST_PERIOD_KEYS[day]) for day in DAYS}
    max_period = max(last_periods.values())
    period_times = _build_period_times(profile, class_minutes, max_period)

    generated = {
        "teacher": {
            "name": profile["선생님이름"],
            "work_start": profile["출근시간"],
            "work_end": profile["퇴근시간"],
        },
        "school": {
            "name": profile["학교명"],
            "year": profile.get("학년도", "").strip(),
            "level": profile["학교급"],
            "class_minutes": class_minutes,
            "break_minutes": 10,
            "morning_homeroom_start": profile["조회시작"],
            "homeroom_minutes": 10,
        },
        "homeroom": {
            "enabled": homeroom_enabled,
            "grade": profile.get("담임학년", "") if homeroom_enabled else "",
            "class": profile.get("담임반", "") if homeroom_enabled else "",
        },
        "calendars": {
            "work_calendar_id": profile.get("업무캘린더ID", ""),
            "work_calendar_name": profile.get("업무캘린더이름", ""),
            "school_calendar_id": profile.get("학사일정캘린더ID", ""),
            "school_calendar_name": profile.get("학사일정캘린더이름", ""),
            "work_tasks_id": profile.get("업무Tasks목록ID", ""),
            "work_tasks_name": profile.get("업무Tasks목록이름", ""),
            "homeroom_tasks_id": profile.get("담임안내Tasks목록ID", "") if homeroom_enabled else "",
            "homeroom_tasks_name": profile.get("담임안내Tasks목록이름", "") if homeroom_enabled else "",
        },
        "last_periods": last_periods,
        "period_times": period_times,
        "afternoon_homeroom_times": _build_afternoon_homeroom_times(period_times, last_periods),
        "weekly_timetable": _build_weekly_timetable(timetable_rows, last_periods),
        "free_periods": _build_free_periods(timetable_rows, last_periods),
        "special_rows": _build_special_rows(timetable_rows),
    }

    output_path = config_dir / "profile.generated.json"
    _atomic_verified_json(output_path, generated)
    return output_path


def init_config_dir(config_dir: str | Path) -> list[Path]:
    config_dir = Path(config_dir)
    _reject_reparse_components(config_dir, "개인 설정 폴더")
    config_dir.mkdir(parents=True, exist_ok=True)
    _reject_reparse_components(config_dir, "개인 설정 폴더")
    template_dir = bundle_paths.bundle_root() / "templates"
    copied = []
    for template_path in template_dir.iterdir():
        if template_path.name == "weekly-timetable.csv":
            continue
        target_path = config_dir / template_path.name
        if target_path.exists() or target_path.is_symlink():
            _reject_reparse_components(target_path, target_path.name)
        if target_path.exists():
            continue
        if template_path.name == "README-setup.txt":
            text = _readme_text(template_path, target_path, config_dir)
            made = _atomic_create_bytes(target_path, text.encode("utf-8"))
        else:
            encoding = "utf-8-sig" if target_path.suffix.lower() == ".csv" else "utf-8"
            text = template_path.read_text(encoding="utf-8-sig")
            made = _atomic_create_bytes(target_path, text.encode(encoding))
        if made:
            copied.append(target_path)
    timetable_path = config_dir / "weekly-timetable.xlsx"
    if timetable_path.exists() or timetable_path.is_symlink():
        _reject_reparse_components(timetable_path, timetable_path.name)
    if not timetable_path.exists():
        if _atomic_create_timetable(timetable_path):
            copied.append(timetable_path)
    return copied


def _reject_reparse_components(path: Path, label: str) -> None:
    """설정 입출력이 symlink·junction을 따라 다른 폴더로 새지 않게 막는다."""
    absolute = Path(os.path.abspath(str(path)))
    flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    for candidate in (absolute, *absolute.parents):
        try:
            info = os.lstat(candidate)
        except FileNotFoundError:
            continue
        attributes = int(getattr(info, "st_file_attributes", 0) or 0)
        if stat.S_ISLNK(info.st_mode) or bool(attributes & flag):
            raise ValueError(f"{label}에 바로가기나 연결 폴더를 사용할 수 없습니다.")


def _atomic_verified_json(path: Path, value: dict) -> None:
    """완성 파일을 다시 읽어 확인한 뒤 한 번에 바꾸고, 실패하면 옛 파일을 둔다."""
    path = Path(path)
    _reject_reparse_components(path.parent, "개인 설정 폴더")
    if path.exists() or path.is_symlink():
        _reject_reparse_components(path, path.name)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            file.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
            file.flush()
            os.fsync(file.fileno())
        reread = json.loads(temporary.read_text(encoding="utf-8"))
        if reread != value:
            raise ValueError("새 개인 설정 파일을 다시 읽은 값이 다릅니다.")
        if path.exists() or path.is_symlink():
            _reject_reparse_components(path, path.name)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _publish_create_only(temporary: Path, target: Path) -> bool:
    """완성 임시 파일을 대상이 아직 없을 때만 한 번에 공개한다."""
    try:
        os.link(temporary, target)
        return True
    except FileExistsError:
        _reject_reparse_components(target, target.name)
        return False


def _atomic_create_bytes(target: Path, data: bytes) -> bool:
    target = Path(target)
    _reject_reparse_components(target.parent, "개인 설정 폴더")
    if target.exists() or target.is_symlink():
        _reject_reparse_components(target, target.name)
        return False
    descriptor, name = tempfile.mkstemp(
        prefix=f".{target.name}-", suffix=".tmp", dir=str(target.parent)
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        if temporary.read_bytes() != data:
            raise ValueError(f"새 {target.name} 파일을 다시 읽은 값이 다릅니다.")
        return _publish_create_only(temporary, target)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _atomic_create_timetable(target: Path) -> bool:
    target = Path(target)
    _reject_reparse_components(target.parent, "개인 설정 폴더")
    if target.exists() or target.is_symlink():
        _reject_reparse_components(target, target.name)
        return False
    descriptor, name = tempfile.mkstemp(
        prefix=f".{target.name}-", suffix=".tmp", dir=str(target.parent)
    )
    os.close(descriptor)
    temporary = Path(name)
    try:
        write_timetable_xlsx(temporary)
        with zipfile.ZipFile(temporary) as workbook:
            if workbook.testzip() is not None:
                raise ValueError("새 시간표 견본의 압축 내용을 끝까지 읽지 못했습니다.")
        _read_timetable_xlsx(temporary)
        return _publish_create_only(temporary, target)
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def _readme_text(template_path: Path, target_path: Path, config_dir: Path) -> str:
    values = {
        "CONFIG_DIR": str(config_dir),
        "README_PATH": str(target_path),
        "PROFILE_CSV": str(config_dir / "teacher-profile.csv"),
        "TIMETABLE_XLSX": str(config_dir / "weekly-timetable.xlsx"),
        "GENERATED_JSON": str(config_dir / "profile.generated.json"),
    }
    text = template_path.read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text


def write_timetable_xlsx(path: str | Path, rows: list[list[str]] | None = None) -> None:
    path = Path(path)
    if rows is None:
        rows = [
            ["교시", "월", "화", "수", "목", "금"],
            ["1", "", "", "", "", ""],
            ["2", "", "", "", "", ""],
            ["3", "", "", "", "", ""],
            ["4", "", "", "", "", ""],
            ["5", "", "", "", "", ""],
            ["6", "", "", "", "", ""],
            ["7", "", "", "", "", ""],
        ]

    path.parent.mkdir(parents=True, exist_ok=True)
    files = {
        "[Content_Types].xml": _xlsx_content_types(),
        "_rels/.rels": _xlsx_root_rels(),
        "xl/workbook.xml": _xlsx_workbook(),
        "xl/_rels/workbook.xml.rels": _xlsx_workbook_rels(),
        "xl/styles.xml": _xlsx_styles(),
        "xl/worksheets/sheet1.xml": _xlsx_sheet(rows),
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as workbook:
        for name, text in files.items():
            workbook.writestr(name, text.encode("utf-8"))


def _xlsx_content_types() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>
"""


def _xlsx_root_rels() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
"""


def _xlsx_workbook() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="시간표" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>
"""


def _xlsx_workbook_rels() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>
"""


def _xlsx_styles() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="1"><font><sz val="11"/><name val="맑은 고딕"/></font></fonts>
  <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="2">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>
"""


def _xlsx_sheet(rows: list[list[str]]) -> str:
    normalized_rows = [(row + [""] * 6)[:6] for row in rows]
    last_row = max(1, len(normalized_rows))
    row_xml = []
    for row_index, row in enumerate(normalized_rows, start=1):
        cells = []
        for column_index, value in enumerate(row, start=1):
            cell_ref = f"{_column_name(column_index)}{row_index}"
            text = escape(str(value or ""))
            if text:
                cells.append(f'<c r="{cell_ref}" s="1" t="inlineStr"><is><t>{text}</t></is></c>')
            else:
                cells.append(f'<c r="{cell_ref}" s="1"/>')
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <dimension ref="A1:F{last_row}"/>
  <sheetViews><sheetView workbookViewId="0"/></sheetViews>
  <sheetFormatPr defaultRowHeight="18"/>
  <cols><col min="1" max="6" width="14" style="1" customWidth="1"/></cols>
  <sheetData>
    {''.join(row_xml)}
  </sheetData>
</worksheet>
"""


def _column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _read_profile_csv(path: Path) -> dict[str, str]:
    if not path.exists():
        raise ValueError(f"설정 파일이 없습니다: {path}")
    rows: dict[str, str] = {}
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or "항목" not in reader.fieldnames or "값" not in reader.fieldnames:
            raise ValueError("teacher-profile.csv 첫 줄은 반드시 '항목,값'이어야 합니다.")
        for row in reader:
            key = (row.get("항목") or "").strip()
            if not key:
                continue
            rows[key] = (row.get("값") or "").strip()
    return rows


def _read_timetable(config_dir: Path) -> dict[str, dict[str, str]]:
    xlsx_path = config_dir / "weekly-timetable.xlsx"
    if xlsx_path.exists():
        return _read_timetable_xlsx(xlsx_path)
    return _read_timetable_csv(config_dir / "weekly-timetable.csv")


def _read_timetable_xlsx(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        raise ValueError(f"시간표 파일이 없습니다: {path}")
    table = _read_xlsx_first_sheet(path)
    if not table:
        raise ValueError("weekly-timetable.xlsx 첫 줄은 반드시 '교시,월,화,수,목,금'이어야 합니다.")
    header = [(cell or "").strip() for cell in (table[0] + [""] * 6)[:6]]
    required = ["교시", *DAYS]
    if header != required:
        raise ValueError("weekly-timetable.xlsx 첫 줄은 반드시 '교시,월,화,수,목,금'이어야 합니다.")

    rows: dict[str, dict[str, str]] = {}
    for row in table[1:]:
        values = [(cell or "").strip() for cell in (row + [""] * 6)[:6]]
        label = values[0]
        if not label:
            continue
        rows[label] = {day: values[index] for index, day in enumerate(DAYS, start=1)}
    return rows


def _read_xlsx_first_sheet(path: Path) -> list[list[str]]:
    namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as workbook:
        shared_strings = _read_shared_strings(workbook, namespace)
        sheet_xml = workbook.read("xl/worksheets/sheet1.xml")

    root = ElementTree.fromstring(sheet_xml)
    rows: list[list[str]] = []
    for row in root.findall(".//s:sheetData/s:row", namespace):
        values: dict[int, str] = {}
        for cell in row.findall("s:c", namespace):
            cell_ref = cell.get("r", "")
            column_index = _column_index(cell_ref)
            if column_index is None:
                continue
            values[column_index] = _xlsx_cell_text(cell, shared_strings, namespace)
        if values:
            max_column = max(values)
            rows.append([values.get(index, "") for index in range(1, max_column + 1)])
    return rows


def _read_shared_strings(workbook: zipfile.ZipFile, namespace: dict[str, str]) -> list[str]:
    if "xl/sharedStrings.xml" not in workbook.namelist():
        return []
    root = ElementTree.fromstring(workbook.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for item in root.findall("s:si", namespace):
        parts = [text.text or "" for text in item.findall(".//s:t", namespace)]
        strings.append("".join(parts))
    return strings


def _xlsx_cell_text(cell: ElementTree.Element, shared_strings: list[str], namespace: dict[str, str]) -> str:
    cell_type = cell.get("t")
    if cell_type == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(".//s:t", namespace))
    value = cell.find("s:v", namespace)
    if value is None or value.text is None:
        return ""
    if cell_type == "s":
        index = int(value.text)
        return shared_strings[index] if index < len(shared_strings) else ""
    return value.text


def _column_index(cell_ref: str) -> int | None:
    match = re.match(r"([A-Z]+)", cell_ref)
    if not match:
        return None
    index = 0
    for char in match.group(1):
        index = index * 26 + (ord(char) - 64)
    return index


def _read_timetable_csv(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        raise ValueError(f"시간표 파일이 없습니다: {path}")
    rows: dict[str, dict[str, str]] = {}
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        required = ["교시", *DAYS]
        if not reader.fieldnames or any(column not in reader.fieldnames for column in required):
            raise ValueError("weekly-timetable.csv 첫 줄은 반드시 '교시,월,화,수,목,금'이어야 합니다.")
        for row in reader:
            label = (row.get("교시") or "").strip()
            if not label:
                continue
            rows[label] = {day: (row.get(day) or "").strip() for day in DAYS}
    return rows


def _require_profile_values(profile: dict[str, str], *, require_links: bool = True) -> None:
    required_keys = list(BASE_REQUIRED_PROFILE_KEYS)
    homeroom_keys = list(HOMEROOM_BASE_REQUIRED_KEYS)
    if require_links:
        required_keys.extend(LINK_REQUIRED_PROFILE_KEYS)
        homeroom_keys.extend(HOMEROOM_LINK_REQUIRED_KEYS)
    missing = [key for key in required_keys if not profile.get(key)]
    if profile.get("담임여부") and _is_homeroom_teacher(profile["담임여부"]):
        missing.extend(key for key in homeroom_keys if not profile.get(key))
    if missing:
        message = "teacher-profile.csv에 값이 필요합니다: " + ", ".join(missing)
        if any(key.endswith("ID") for key in missing):
            message += (
                "\nID는 직접 찾지 않아도 됩니다. 에이전트에게 '캘린더와 Tasks 목록 보여줘'라고 말하면 "
                "이름으로 고른 뒤 ID를 대신 채워줍니다."
            )
        raise ValueError(message)


def _is_homeroom_teacher(value: str) -> bool:
    value = value.strip()
    if value in {"예", "Y", "y", "yes", "YES", "담임"}:
        return True
    if value in {"아니오", "아니요", "N", "n", "no", "NO", "비담임"}:
        return False
    raise ValueError("담임여부는 예 또는 아니오로 적어주세요.")


def _class_minutes(level: str) -> int:
    level = level.strip()
    if level not in CLASS_MINUTES_BY_LEVEL:
        raise ValueError("학교급은 초, 중, 고 중 하나로 적어주세요.")
    return CLASS_MINUTES_BY_LEVEL[level]


def _int_value(value: str, label: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{label}은 숫자로 적어주세요: {value}") from exc


def _last_period_value(value: str, label: str) -> int:
    period = _int_value(value, label)
    if not 1 <= period <= 7:
        raise ValueError(
            f"{label}은 1에서 7 사이 숫자로 적어주세요. 시간표 파일도 1~7교시까지만 씁니다. 지금 값: {value}"
        )
    return period


def _build_period_times(profile: dict[str, str], class_minutes: int, max_period: int) -> dict[str, dict[str, str]]:
    first_start = _parse_time(profile["1교시시작"], "1교시시작")
    fifth_start = _parse_time(profile["점심종료시간"], "점심종료시간")
    starts: dict[int, datetime] = {}
    for period in range(1, max_period + 1):
        if period == 1:
            starts[period] = first_start
        elif period == 5:
            starts[period] = fifth_start
        else:
            previous_end = starts[period - 1] + timedelta(minutes=class_minutes)
            starts[period] = previous_end + timedelta(minutes=10)

    return {
        str(period): {
            "start": _format_time(start),
            "end": _format_time(start + timedelta(minutes=class_minutes)),
        }
        for period, start in starts.items()
    }


def _build_afternoon_homeroom_times(
    period_times: dict[str, dict[str, str]],
    last_periods: dict[str, int],
) -> dict[str, dict[str, str]]:
    homeroom_times: dict[str, dict[str, str]] = {}
    for day, last_period in last_periods.items():
        start = _parse_time(period_times[str(last_period)]["end"], f"{day} 종례시작")
        homeroom_times[day] = {
            "start": _format_time(start),
            "end": _format_time(start + timedelta(minutes=10)),
        }
    return homeroom_times


def _build_weekly_timetable(
    timetable_rows: dict[str, dict[str, str]],
    last_periods: dict[str, int],
) -> dict[str, dict[str, dict[str, object]]]:
    weekly: dict[str, dict[str, dict[str, object]]] = {}
    for day in DAYS:
        weekly[day] = {}
        for period in range(1, last_periods[day] + 1):
            label = timetable_rows.get(str(period), {}).get(day, "")
            weekly[day][str(period)] = {"busy": bool(label), "label": label}
    return weekly


def _build_free_periods(
    timetable_rows: dict[str, dict[str, str]],
    last_periods: dict[str, int],
) -> dict[str, list[int]]:
    free: dict[str, list[int]] = {}
    for day in DAYS:
        free[day] = []
        for period in range(1, last_periods[day] + 1):
            label = timetable_rows.get(str(period), {}).get(day, "")
            if not label:
                free[day].append(period)
    return free


def _build_special_rows(timetable_rows: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    return {label: values for label, values in timetable_rows.items() if not label.isdigit()}


def _parse_time(value: str, label: str) -> datetime:
    try:
        return datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise ValueError(
            f"{label}은 8:30 또는 08:30처럼 시:분 형식으로 적어주세요 (예: 08:30). 지금 값: {value}"
        ) from exc


def _format_time(value: datetime) -> str:
    return value.strftime("%H:%M")


def main() -> int:
    parser = argparse.ArgumentParser(description="Teacher Task Manager 설정 파일을 JSON으로 바꿉니다.")
    parser.add_argument("--config-dir", default=str(Path.home() / "TeacherTaskManager"))
    parser.add_argument("--init", action="store_true", help="설정 폴더와 입력 견본을 만듭니다.")
    args = parser.parse_args()

    config_dir = Path(args.config_dir)
    if args.init:
        copied = init_config_dir(config_dir)
        print(f"설정 폴더: {config_dir}")
        if copied:
            print("생성한 파일:")
            for path in copied:
                print(f"- {path.name}")
        else:
            print("새로 만들 파일은 없습니다.")
        return 0

    try:
        output_path = parse_config_dir(config_dir)
    except ValueError as exc:
        print(f"설정 오류: {exc}")
        return 2

    print(f"생성 완료: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
