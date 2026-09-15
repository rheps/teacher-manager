"""Keep workbook references on the account server, preserving local draft bytes.

Local journals retain only opaque content hashes. Restoring their historical
evidence requires the same authenticated account; it never selects a workbook.
Student rows and credentials are not uploaded by this adapter.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

FIELDS = frozenset(('CENTRAL_CHAT_SHEET_ID', 'spreadsheet_id', 'spreadsheetId', 'spreadsheet_url', 'spreadsheetUrl',
    'previousSpreadsheetId', 'publishedSpreadsheetId', 'previous_spreadsheet_id',
    'source_spreadsheet_id', 'target_spreadsheet_id', 'connection_code', 'workbook_name',
    'canonical_workbook_name', 'resourceManifest', 'script_attestation', 'attendance_scope',
    'script_id', 'deployment_id', 'template_doc_id', 'template_doc_url', 'folder_id', 'task_list_id',
    'monthly_sheet_ids', 'monthlySheetIds'))
_FIELD = re.compile(r'"(' + '|'.join(sorted(FIELDS)) + r')"\s*:\s*')
_TAG = '$attendanceServerRef'
_DECODER = json.JSONDecoder()
_HISTORY = {'attendance-archive', 'attendance-replacement-history', 'attendance-operation-history', 'attendance-record-history'}


def config_for(path):
    path = Path(path)
    if path.suffix != '.json':
        return None
    for parent in path.parents:
        if parent.name in _HISTORY:
            return parent.parent
    if path.name.startswith(('attendance-', 'homeroom-roster')):
        return path.parent
    return None


def _transform(raw, replace):
    text = raw.decode('utf-8-sig')
    parts, start = [], 0
    while match := _FIELD.search(text, start):
        index = match.end()
        value, end = _DECODER.raw_decode(text, index)
        parts.append(text[start:index])
        parts.append(replace(match.group(1), value, text[index:end]))
        start = end
    parts.append(text[start:])
    return (('\ufeff' if raw.startswith(b'\xef\xbb\xbf') else '') + ''.join(parts)).encode('utf-8')


def protect(path, raw):
    root = config_for(path)
    if root is None:
        return raw
    from attendance_server_record import client_for
    client = None
    def replace(field, value, original):
        nonlocal client
        if value in (None, '') or isinstance(value, dict) and set(value) == {_TAG}:
            return original
        client = client or client_for(root)
        key = hashlib.sha256((field + '\0' + original).encode('utf-8')).hexdigest()
        client.store_reference(field, original, key)
        return json.dumps({_TAG: key}, separators=(',', ':'))
    return _transform(raw, replace)


def restore(path, raw):
    root = config_for(path)
    if root is None or _TAG.encode() not in raw:
        return raw
    from attendance_server_record import client_for
    client = client_for(root)
    def replace(field, value, original):
        if not isinstance(value, dict) or set(value) != {_TAG}:
            return original
        key = value[_TAG]
        text = client.read_reference(key)
        if hashlib.sha256((field + '\0' + text).encode('utf-8')).hexdigest() != key:
            raise ValueError('서버의 출석부 작업 기록을 확인하지 못했어요.')
        return text
    return _transform(raw, replace)


def read_bytes(path):
    return restore(path, Path(path).read_bytes())


def read_text(path, encoding='utf-8', errors=None):
    return read_bytes(path).decode(encoding, errors=errors or 'strict')


def migrate_known_references(config_dir):
    from brity_bridge import account_sessions
    with account_sessions.session_lock(config_dir):
        _migrate_known_references(config_dir)
        from dashboard.central_chat import migrate_handover_references
        migrate_handover_references(config_dir)


def _migrate_known_references(config_dir):
    """One-way migration: confirm remote storage before replacing local bytes."""
    from brity_bridge import component_lock
    root = Path(config_dir)
    candidates = list(root.glob('attendance-*.json'))
    roster = root / 'homeroom-roster.generated.json'
    if roster.exists():
        candidates.append(roster)
    for name in _HISTORY:
        history = root / name
        if history.is_dir():
            candidates.extend(history.rglob('*.json'))
    for path in candidates:
        # Never follow another account's directory or a link outside this root.
        resolved = path.resolve()
        if not resolved.is_relative_to(root.resolve()) or path.is_symlink():
            raise ValueError('출석부 기록의 저장 위치를 확인하지 못했어요.')
        component_lock.prepare_direct_file_path(path)
        raw = path.read_bytes()
        try:
            updated = protect(path, raw)
        except (json.JSONDecodeError, UnicodeError):
            # A corrupt write journal is evidence, never an empty/success state.
            raise ValueError('기존 출석부 작업 기록을 안전하게 옮기지 못했어요.') from None
        if updated != raw:
            if restore(path, updated) != raw:
                raise ValueError('서버에 보관한 출석부 기록이 원본과 달라요.')
            descriptor, temporary = tempfile.mkstemp(prefix='.attendance-reference-', dir=path.parent)
            temporary = Path(temporary)
            try:
                with os.fdopen(descriptor, 'wb') as output:
                    output.write(updated)
                    output.flush()
                    os.fsync(output.fileno())
                if path.read_bytes() != raw:
                    raise ValueError('출석부 기록이 옮기는 동안 바뀌었어요. 원본을 보존했습니다.')
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
