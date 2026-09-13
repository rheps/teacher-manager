"""Explicit, server-verified migration of one exact legacy workbook."""
from __future__ import annotations
import json
import hashlib
from pathlib import Path
from attendance_binding import AttendanceBindingError
from attendance_sheet_layout import validate_month_sheet_ids
from attendance_script_update import _run_one_json


def adopt_existing_workbook(client, spreadsheet_id, runner, workdir, gws):
    candidate = client.adoption_candidate(spreadsheet_id)
    value = candidate.payload
    if value.get("spreadsheetId") != spreadsheet_id:
        raise AttendanceBindingError("ATTENDANCE_SCOPE_CHANGED")
    months = validate_month_sheet_ids((value.get("resourceManifest") or {}).get("monthlySheetIds"))
    reply = _run_one_json(runner, [gws, "sheets", "spreadsheets", "values", "get", "--params",
        json.dumps({"spreadsheetId": spreadsheet_id, "range": "'설정'!A:B"}), "--format", "json"], workdir)
    rows = reply.get("values")
    if not isinstance(rows, list):
        raise AttendanceBindingError("ATTENDANCE_STRUCTURE_UNVERIFIED")
    positions, settings = {}, {}
    for index, row in enumerate(rows):
        if not isinstance(row, list):
            raise AttendanceBindingError("ATTENDANCE_STRUCTURE_UNVERIFIED")
        key = str(row[0] or "") if row else ""
        if not key: continue
        if key in positions:
            raise AttendanceBindingError("ATTENDANCE_STRUCTURE_UNVERIFIED")
        positions[key] = index + 1
        settings[key] = str(row[1]) if len(row) > 1 else ""
    if settings.get("SCHOOL_YEAR") != str(candidate.year):
        raise AttendanceBindingError("ATTENDANCE_YEAR_UNVERIFIED")
    generation = value["generation"] if value["bindingState"] == "ACTIVE" else value["generation"] + 1
    from attendance_workbook_transition import _read_dict
    from dashboard.engine import _atomic_write_json
    journal = Path(workdir) / "attendance-recovery-journal" / (hashlib.sha256(spreadsheet_id.encode()).hexdigest() + ".json")
    context = {"spreadsheet_id": spreadsheet_id, "subject_key": value["subjectKey"], "school_year": candidate.year,
               "binding_generation": generation, "monthly_sheet_ids": months}
    if journal.exists():
        previous = _read_dict(journal)
        if previous.get("context") != context:
            raise AttendanceBindingError("ATTENDANCE_RECOVERY_REQUIRED")
    wanted = {"ATTENDANCE_MONTH_SHEET_IDS": json.dumps(months, separators=(",", ":")),
        "ATTENDANCE_PROTOCOL_VERSION": "1", "ATTENDANCE_SUBJECT_KEY": value["subjectKey"],
        "ATTENDANCE_BINDING_GENERATION": str(generation)}
    changes = []
    next_row = len(rows) + 1
    for key, expected in wanted.items():
        actual = settings.get(key, "").strip()
        if actual:
            agrees = (validate_month_sheet_ids(json.loads(actual)) == months if key == "ATTENDANCE_MONTH_SHEET_IDS" else actual == expected)
            if not agrees:
                raise AttendanceBindingError("ATTENDANCE_BINDING_CONFLICT")
            continue
        if key in positions:
            changes.append({"range": "'설정'!B" + str(positions[key]), "values": [[expected]]})
        else:
            changes.append({"range": "'설정'!A" + str(next_row) + ":B" + str(next_row), "values": [[key, expected]]})
            next_row += 1
    if changes:
        fresh = client.adoption_candidate(spreadsheet_id)
        fresh.require_same(candidate)
        if validate_month_sheet_ids(fresh.payload["resourceManifest"]["monthlySheetIds"]) != months:
            raise AttendanceBindingError("ATTENDANCE_SCOPE_CHANGED")
        _atomic_write_json(journal, {"context": context, "state": "DISPATCHED_OR_UNKNOWN"})
        _run_one_json(runner, [gws, "sheets", "spreadsheets", "values", "batchUpdate", "--params",
            json.dumps({"spreadsheetId": spreadsheet_id}), "--json", json.dumps({"valueInputOption": "RAW", "data": changes}),
            "--format", "json"], workdir)
    adopted = client.adopt(spreadsheet_id)
    _atomic_write_json(journal, {"context": context, "state": "CONFIRMED_RESULT"})
    return adopted
