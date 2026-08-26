"""Teacher Manager 출결 Google Sheet의 정식 이름과 학년도를 계산한다."""

from __future__ import annotations

import datetime
import hashlib
from pathlib import Path
from typing import Any, Mapping


ATTENDANCE_WORKBOOK_TITLE_SUFFIX = "(Teacher manager 출결 자동화)"
# 2.3 시험판 일부가 잠시 만든 잘못된 이름이다. 새 파일 이름에는 절대 쓰지 않고,
# 첫 준비에서 이 이름이 보이면 중복 생성을 멈추고 연결 선택을 안내하는 데만 쓴다.
PREVIOUS_ATTENDANCE_WORKBOOK_TITLE_SUFFIX = (
    " (Teacher Manager 출결 신고서 자동화)"
)
ATTENDANCE_ROLE_PROPERTY = "teacherManagerAttendanceRole"
ATTENDANCE_ROLE_VALUE = "canonical-v1"
ATTENDANCE_SCHOOL_YEAR_PROPERTY = "teacherManagerAttendanceSchoolYear"


def attendance_connection_code(spreadsheet_id: object) -> str:
    """전체 Google Sheet 번호에서 사람이 양쪽 화면을 대조할 확인번호를 만든다.

    실제 열기·입력은 언제나 잘리지 않은 ``spreadsheet_id``를 쓴다. 이 48비트 값은
    화면 대조와 사용자가 명시적으로 붙여 넣는 복구에만 쓴다. 복구 때도 같은 계정의
    검증된 후보가 정확히 하나와 일치해야 하며, 마지막 선택은 전체 ID로 다시 확인한다.
    """

    checked = str(spreadsheet_id or "").strip()
    if not checked:
        return ""
    digest = hashlib.sha256(checked.encode("utf-8")).hexdigest()[:12].upper()
    return f"TM-{digest[:6]}-{digest[6:]}"


def current_school_year(today: datetime.date | None = None) -> str:
    """한국 학년도는 3월 1일에 바뀐다."""

    day = today or datetime.date.today()
    return str(day.year if day.month >= 3 else day.year - 1)


def attendance_workbook_name(
    profile: Mapping[str, Any], today: datetime.date | None = None
) -> str:
    """사람이 알아볼 정식 출결 파일 이름을 만든다."""

    school = profile.get("school") or {}
    year = str(school.get("year", "") or "").strip() or current_school_year(today)
    homeroom = profile.get("homeroom") or {}
    grade = str(homeroom.get("grade", "") or "").strip()
    klass = str(homeroom.get("class", "") or "").strip()
    if homeroom.get("enabled") and grade and klass:
        stem = f"{year}학년도 {grade}학년 {klass}반 출석부"
    else:
        stem = f"{year}학년도 출석부"
    return stem + ATTENDANCE_WORKBOOK_TITLE_SUFFIX


def attendance_workbook_name_from_record(record: Mapping[str, Any]) -> str:
    """화면의 오래된 내 정보가 아니라 현재 출결 기록의 학년도·학년·반으로 만든다."""

    year = str(record.get("school_year", "") or "").strip()
    grade = str(record.get("homeroom_grade", "") or "").strip()
    klass = str(record.get("homeroom_class", "") or "").strip()
    if not year:
        return ""
    stem = (
        f"{year}학년도 {grade}학년 {klass}반 출석부"
        if grade and klass
        else f"{year}학년도 출석부"
    )
    return stem + ATTENDANCE_WORKBOOK_TITLE_SUFFIX


def legacy_year_workbook_name(
    profile: Mapping[str, Any], today: datetime.date | None = None
) -> str:
    """2.3 이전에 만들던 괄호 없는 학년도 이름을 찾을 때만 쓴다."""

    canonical = attendance_workbook_name(profile, today)
    return canonical[: -len(ATTENDANCE_WORKBOOK_TITLE_SUFFIX)]


def previous_attendance_workbook_name(
    profile: Mapping[str, Any], today: datetime.date | None = None
) -> str:
    """2.3 시험판의 잘못된 이름. 중복 생성 중지용 검색에만 쓴다."""

    return (
        legacy_year_workbook_name(profile, today)
        + PREVIOUS_ATTENDANCE_WORKBOOK_TITLE_SUFFIX
    )


def attendance_workbook_app_properties(
    profile: Mapping[str, Any], today: datetime.date | None = None
) -> dict[str, str]:
    """이름이 바뀌어도 정식 출결 파일을 다시 찾게 하는 Drive 표식."""

    school = profile.get("school") or {}
    year = str(school.get("year", "") or "").strip() or current_school_year(today)
    return {
        ATTENDANCE_ROLE_PROPERTY: ATTENDANCE_ROLE_VALUE,
        ATTENDANCE_SCHOOL_YEAR_PROPERTY: year,
    }


def current_attendance_spreadsheet_id(config_dir: Path) -> str:
    """현재 출결 연결번호를 로컬 설치 기록 한 곳에서만 읽는다."""

    record_path = Path(config_dir) / "attendance-install.generated.json"
    try:
        from attendance_install_record import (
            AttendanceInstallRecordError,
            read_verified_canonical_record,
        )

        saved = read_verified_canonical_record(record_path)
    except (OSError, AttendanceInstallRecordError):
        return ""
    return str(saved.get("spreadsheet_id", "") or "").strip()


__all__ = [
    "ATTENDANCE_WORKBOOK_TITLE_SUFFIX",
    "PREVIOUS_ATTENDANCE_WORKBOOK_TITLE_SUFFIX",
    "ATTENDANCE_ROLE_PROPERTY",
    "ATTENDANCE_ROLE_VALUE",
    "ATTENDANCE_SCHOOL_YEAR_PROPERTY",
    "attendance_connection_code",
    "attendance_workbook_name",
    "attendance_workbook_name_from_record",
    "attendance_workbook_app_properties",
    "current_attendance_spreadsheet_id",
    "current_school_year",
    "legacy_year_workbook_name",
    "previous_attendance_workbook_name",
]
