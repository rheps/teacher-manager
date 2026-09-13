"""Seoul calendar arithmetic and strict, server-issued attendance scope.

Local clock calculations are display estimates only. A write must obtain a new
scope from AttendanceBindingClient; a serialized cache is never a capability.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Mapping

SEOUL = dt.timezone(dt.timedelta(hours=9), name="Asia/Seoul")
PROTOCOL_VERSION = 1


class AttendanceScopeError(ValueError):
    def __init__(self, code: str, detail: str = "출석부의 현재 계정과 학년도를 확인하지 못했어요."):
        super().__init__(detail)
        self.code = code


def parse_instant(value: str | dt.datetime) -> dt.datetime:
    try:
        moment = value if isinstance(value, dt.datetime) else dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as error:
        raise AttendanceScopeError("INVALID_SERVER_TIME") from error
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise AttendanceScopeError("INVALID_SERVER_TIME")
    return moment


def school_year_at(value: str | dt.datetime) -> int:
    day = parse_instant(value).astimezone(SEOUL)
    return day.year if day.month >= 3 else day.year - 1


def school_year_for_date(value: str | dt.date) -> int:
    try:
        day = value if type(value) is dt.date else dt.date.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise AttendanceScopeError("INVALID_ATTENDANCE_DATE") from error
    return day.year if day.month >= 3 else day.year - 1


def validate_event_year(event_date: str, workbook_year: int) -> None:
    if type(workbook_year) is not int or school_year_for_date(event_date) != workbook_year:
        raise AttendanceScopeError("ATTENDANCE_EVENT_YEAR_MISMATCH", "출결 날짜가 이 출석부의 학년도와 달라요. 기존 자료는 그대로입니다.")


@dataclass(frozen=True)
class AttendanceScope:
    payload: dict[str, Any]

    @classmethod
    def parse(cls, value: Mapping[str, Any], *, expected_subject: str = "") -> "AttendanceScope":
        if not isinstance(value, dict) or type(value.get("protocolVersion")) is not int or value.get("protocolVersion") != PROTOCOL_VERSION:
            raise AttendanceScopeError("ATTENDANCE_PROTOCOL_REQUIRED")
        subject = value.get("subjectKey")
        if not isinstance(subject, str) or not subject or (expected_subject and subject != expected_subject):
            raise AttendanceScopeError("ATTENDANCE_ACCOUNT_CHANGED")
        year = value.get("currentSchoolYear")
        if type(year) is not int or school_year_at(value.get("serverNow")) != year:
            raise AttendanceScopeError("ATTENDANCE_YEAR_UNVERIFIED")
        boundary = parse_instant(value.get("boundaryAt"))
        expected_boundary = dt.datetime(year + 1, 3, 1, tzinfo=SEOUL)
        if boundary != expected_boundary:
            raise AttendanceScopeError("ATTENDANCE_YEAR_UNVERIFIED")
        generation = value.get("generation")
        if type(generation) is not int or generation < 0:
            raise AttendanceScopeError("ATTENDANCE_GENERATION_UNVERIFIED")
        for key in ("bindingState", "verificationState", "automationState"):
            if not isinstance(value.get(key), str) or not value[key]:
                raise AttendanceScopeError("ATTENDANCE_SCOPE_UNVERIFIED")
        if 'initialPreparationAllowed' in value:
            if type(value['initialPreparationAllowed']) is not bool:
                raise AttendanceScopeError('ATTENDANCE_SCOPE_UNVERIFIED')
            if value['initialPreparationAllowed'] and (value['generation'] != 0
                    or value.get('workbookSchoolYear') not in (None, value['currentSchoolYear'])
                    or value['bindingState'] != 'UNBOUND_CONFIRMED'
                    or value['verificationState'] != 'VERIFIED' or value.get('createAllowed') is not True
                    or value.get('operationId') or value.get('spreadsheetId')):
                raise AttendanceScopeError('ATTENDANCE_SCOPE_UNVERIFIED')
        if value.get("bindingState") == "ACTIVE":
            if generation < 1:
                raise AttendanceScopeError("ATTENDANCE_GENERATION_UNVERIFIED")
            if not isinstance(value.get("spreadsheetId"), str) or not value["spreadsheetId"]:
                raise AttendanceScopeError("ATTENDANCE_BINDING_UNVERIFIED")
            if type(value.get("workbookSchoolYear")) is not int or value["workbookSchoolYear"] != year:
                raise AttendanceScopeError("ATTENDANCE_YEAR_UNVERIFIED")
        replacement = value.get("replacement")
        if replacement is not None:
            if not isinstance(replacement, dict) or type(replacement.get("eligible")) is not bool:
                raise AttendanceScopeError("ATTENDANCE_REPLACEMENT_UNVERIFIED")
            if replacement["eligible"]:
                previous_id = replacement.get("previousSpreadsheetId")
                previous_operation = replacement.get("previousOperationId")
                pending = value["bindingState"] in ("PREPARING", "CREATE_RESULT_UNKNOWN")
                unavailable = replacement.get("reason") == "replace-unavailable"
                failure = replacement.get("failureCode")
                stage = replacement.get("failureStage")
                unavailable_evidence = (
                    failure == "ATTENDANCE_ACCESS_OR_MISSING" and stage in (
                        "file-metadata", "workbook-metadata", "workbook-settings", "workbook-headers")
                    or failure in ("ATTENDANCE_STRUCTURE_UNVERIFIED", "ATTENDANCE_ROLE_MANIFEST_REQUIRED",
                        "ATTENDANCE_ROLE_COLLISION", "ATTENDANCE_YEAR_UNVERIFIED") and stage == "workbook-structure")
                if ((previous_operation is not None and (not isinstance(previous_operation, str) or not previous_operation))
                        or (pending and (not previous_operation or previous_operation != value.get("operationId")))
                        or (not pending and not unavailable and previous_operation is not None)
                        or (unavailable and (value["bindingState"] != "ACTIVE" or not previous_operation
                            or previous_operation != value.get("operationId") or not unavailable_evidence
                            or failure != value["verificationState"]))):
                    raise AttendanceScopeError("ATTENDANCE_REPLACEMENT_UNVERIFIED")
                if (replacement.get("reason") not in ("replace-trashed", "replace-unavailable")
                        or not isinstance(previous_id, str) or not previous_id
                        or type(replacement.get("expectedGeneration")) is not int
                        or replacement["expectedGeneration"] != generation
                        or type(replacement.get("currentSchoolYear")) is not int
                        or replacement["currentSchoolYear"] != year
                        or value["bindingState"] not in ("ACTIVE", "MIGRATION_UNKNOWN", "UNBOUND_CONFIRMED", "PREPARING", "CREATE_RESULT_UNKNOWN")
                        or value.get("spreadsheetId") not in (None, "", previous_id)
                        or (not unavailable and value["verificationState"] != "ATTENDANCE_FILE_TRASHED")
                        or value["automationState"] != "BLOCKED" or value.get("createAllowed") is not False
                        or parse_instant(replacement.get("verifiedAt")) > parse_instant(value["serverNow"])):
                    raise AttendanceScopeError("ATTENDANCE_REPLACEMENT_UNVERIFIED")
        return cls(dict(value))

    @property
    def year(self) -> int:
        return self.payload["currentSchoolYear"]

    @property
    def create_allowed(self) -> bool:
        return (self.payload.get("createAllowed") is True
                and self.payload["bindingState"] == "UNBOUND_CONFIRMED"
                and self.payload["verificationState"] == "VERIFIED")

    @property
    def replacement_allowed(self) -> bool:
        return (self.payload.get("replacement") or {}).get("eligible") is True

    @property
    def initial_preparation_allowed(self) -> bool:
        return self.create_allowed and self.payload.get('initialPreparationAllowed') is True

    def require_same(self, previous: "AttendanceScope") -> None:
        for key in ("subjectKey", "currentSchoolYear", "generation", "spreadsheetId"):
            if self.payload.get(key) != previous.payload.get(key):
                raise AttendanceScopeError("ATTENDANCE_SCOPE_CHANGED", "확인하는 동안 출석부의 계정·학년도·연결이 바뀌었어요. 현재 상태를 다시 읽어 주세요.")


@dataclass(frozen=True)
class HistoricalPublicationReceipt:
    """Durable old-year publication evidence, never a current/write scope."""
    payload: dict[str, Any]

    @classmethod
    def parse(cls, value: Mapping[str, Any], *, operation: Mapping[str, Any]) -> "HistoricalPublicationReceipt":
        error = "ATTENDANCE_HISTORICAL_RECEIPT_INVALID"
        if not isinstance(value, dict) or not isinstance(operation, dict):
            raise AttendanceScopeError(error)
        resources = operation.get("resources")
        sheet = resources.get("spreadsheet") if isinstance(resources, dict) else None
        if (not isinstance(sheet, dict) or sheet.get("phase") != "CONFIRMED_RESULT"
                or not sheet.get("resourceId") or not all(isinstance(item, dict) and item.get("phase") == "CONFIRMED_RESULT" for item in resources.values())):
            raise AttendanceScopeError(error)
        year, generation = operation.get("schoolYear"), operation.get("generation")
        current_year = value.get("currentSchoolYear")
        if (type(year) is not int or type(generation) is not int or generation < 0
                or type(current_year) is not int or current_year <= year
                or type(value.get("protocolVersion")) is not int or value["protocolVersion"] != PROTOCOL_VERSION
                or value.get("historical") is not True or value.get("bindingState") != "ACTIVE"
                or value.get("createAllowed") is not False or value.get("subjectKey") != operation.get("subjectKey")
                or not value.get("subjectKey") or value.get("operationId") != operation.get("operationId")
                or not value.get("operationId") or value.get("spreadsheetId") != sheet["resourceId"]
                or type(value.get("workbookSchoolYear")) is not int or value["workbookSchoolYear"] != year
                or type(value.get("generation")) is not int or value["generation"] != generation + 1):
            raise AttendanceScopeError(error)
        if (school_year_at(value.get("serverNow")) != current_year
                or parse_instant(value.get("boundaryAt")) != dt.datetime(current_year + 1, 3, 1, tzinfo=SEOUL)):
            raise AttendanceScopeError(error)
        # Only the nonsecret receipt contract is persisted, not arbitrary server fields.
        fields = ("protocolVersion", "subjectKey", "operationId", "spreadsheetId", "workbookSchoolYear",
                  "generation", "currentSchoolYear", "serverNow", "boundaryAt", "historical", "bindingState", "createAllowed")
        return cls({key: value[key] for key in fields})
