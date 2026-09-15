"""Finish an explicit server selection without storing workbook identity on disk."""
import json

from attendance_binding import AttendanceBindingError
from attendance_server_record import record_from_scope
from attendance_script_update import _run_one_json


def connect_selected_workbook(client, scope, runner, workdir, gws):
    record = record_from_scope(scope, client.email)
    sid = record['spreadsheet_id']
    wanted = {'ATTENDANCE_MONTH_SHEET_IDS': json.dumps(record['monthly_sheet_ids'], separators=(',', ':')),
              'ATTENDANCE_PROTOCOL_VERSION': '1', 'ATTENDANCE_SUBJECT_KEY': scope.payload['subjectKey'],
              'ATTENDANCE_BINDING_GENERATION': str(record['binding_generation'])}

    def read():
        reply = _run_one_json(runner, [gws, 'sheets', 'spreadsheets', 'values', 'get', '--params',
            json.dumps({'spreadsheetId': sid, 'range': "'설정'!A:B"}), '--format', 'json'], workdir)
        rows = reply.get('values')
        if not isinstance(rows, list):
            raise AttendanceBindingError('ATTENDANCE_STRUCTURE_UNVERIFIED')
        positions, actual = {}, {}
        for index, row in enumerate(rows):
            if not isinstance(row, list):
                raise AttendanceBindingError('ATTENDANCE_STRUCTURE_UNVERIFIED')
            key = str(row[0] or '') if row else ''
            if not key:
                continue
            if key in positions:
                raise AttendanceBindingError('ATTENDANCE_STRUCTURE_UNVERIFIED')
            positions[key] = index + 1
            actual[key] = str(row[1]) if len(row) > 1 else ''
        if actual.get('SCHOOL_YEAR') != record['school_year']:
            raise AttendanceBindingError('ATTENDANCE_YEAR_MISMATCH')
        return rows, positions, actual

    def matches(key, actual):
        if key != 'ATTENDANCE_MONTH_SHEET_IDS':
            return actual == wanted[key]
        try:
            return json.loads(actual) == record['monthly_sheet_ids']
        except (TypeError, ValueError):
            return False

    rows, positions, actual = read()
    changes, next_row = [], len(rows) + 1
    for key, value in wanted.items():
        if matches(key, actual.get(key)):
            continue
        if key in positions:
            changes.append({'range': "'설정'!B" + str(positions[key]), 'values': [[value]]})
        else:
            changes.append({'range': f"'설정'!A{next_row}:B{next_row}", 'values': [[key, value]]})
            next_row += 1
    if changes:
        # Authorize the selected full ID and generation again immediately before
        # writing. This never asks the previous workbook to become healthy.
        client.authorize_workbook(record)
        write_error = None
        try:
            _run_one_json(runner, [gws, 'sheets', 'spreadsheets', 'values', 'batchUpdate', '--params',
                json.dumps({'spreadsheetId': sid}), '--json', json.dumps({'valueInputOption': 'RAW', 'data': changes}),
                '--format', 'json'], workdir)
        except Exception as error:
            write_error = error
        # These four exact values are idempotent. A lost reply is resolved by
        # reading, never by repeating a possibly completed write automatically.
        _, _, after = read()
        if not all(matches(key, after.get(key)) for key in wanted):
            raise AttendanceBindingError('ATTENDANCE_VERIFY_UNAVAILABLE') from write_error
    fresh = client.current()
    fresh.require_same(scope)
    if (fresh.payload.get('automationState') != 'AUTHORIZED'
            or fresh.payload.get('verificationState') != 'VERIFIED'):
        raise AttendanceBindingError('ATTENDANCE_VERIFY_UNAVAILABLE')
    return fresh
