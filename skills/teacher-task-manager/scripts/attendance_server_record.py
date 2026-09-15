"""Authenticated current-workbook records. No disk identity or offline fallback."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import copy
import hashlib
import json
from pathlib import Path

from attendance_binding import AttendanceBindingClient, AttendanceBindingError
from attendance_context import AttendanceScope

RECORD_NAME = 'attendance-install.generated.json'
_contexts = ContextVar('attendance_server_records', default=None)


@contextmanager
def connection_context(config_dir, client, account):
    token = _contexts.set((Path(config_dir).resolve(), client, account))
    try:
        yield
    finally:
        _contexts.reset(token)


def server_connection(method):
    """Keep explicit engine dependencies through nested/background operations."""
    @wraps(method)
    def call(config_dir, *args, **kwargs):
        deps = kwargs.get('deps') or (args[0] if args and hasattr(args[0], 'binding_client') else None)
        client = kwargs.get('binding_client') or (getattr(deps, 'binding_client', None) if deps else None)
        if client is None:
            return method(config_dir, *args, **kwargs)
        with connection_context(config_dir, client, lambda: client.email):
            return method(config_dir, *args, **kwargs)
    return call


def client_for(config_dir):
    """Resolve only a login/session; never read a local workbook reference."""
    directory = Path(config_dir).resolve()
    active = _contexts.get()
    if active is not None and active[0] == directory:
        _, client, expected = active
        expected = expected() if callable(expected) else expected
    else:
        from brity_bridge import account_sessions
        from attendance_session_store import AttendanceSessionStore
        from install_attendance_automation import resolve_central_chat_sender_url
        state = account_sessions.peek_state(directory)
        if state.get('phase') != 'active':
            raise AttendanceBindingError('ATTENDANCE_AUTH_REQUIRED')
        expected = state.get('account', '')
        if account_sessions.active_config_dir(directory, state).resolve() != directory:
            raise AttendanceBindingError('ATTENDANCE_ACCOUNT_CHANGED')
        client = AttendanceBindingClient(resolve_central_chat_sender_url(),
            session_store=AttendanceSessionStore(directory))
    client = client() if callable(client) and not hasattr(client, 'current') else client
    if not expected or client is None:
        raise AttendanceBindingError('ATTENDANCE_AUTH_REQUIRED')
    if isinstance(client, AttendanceBindingClient):
        client.restore(expected)
    if not client.email or client.email.lower() != expected.lower():
        raise AttendanceBindingError('ATTENDANCE_ACCOUNT_CHANGED')
    return client


def record_from_scope(scope, expected_account):
    from attendance_install_record import CONNECTION_FIELDS, validate_verified_canonical_record
    from attendance_sheet_layout import validate_month_sheet_ids
    scope = AttendanceScope.parse(scope.payload)
    value = scope.payload
    if value['bindingState'] != 'ACTIVE':
        if value['bindingState'] in ('UNBOUND_CONFIRMED', 'PREPARING', 'CREATE_RESULT_UNKNOWN'):
            return None
        raise AttendanceBindingError('ATTENDANCE_SCOPE_CHANGED')
    if value['verificationState'] != 'VERIFIED':
        raise AttendanceBindingError(value['verificationState'])
    if str(value.get('email') or '').lower() != expected_account.lower():
        raise AttendanceBindingError('ATTENDANCE_ACCOUNT_CHANGED')
    manifest = value.get('resourceManifest')
    if not isinstance(manifest, dict):
        raise AttendanceBindingError('ATTENDANCE_INVALID_RESPONSE')
    if manifest.get('spreadsheet_id') != value['spreadsheetId']:
        raise AttendanceBindingError('ATTENDANCE_SCOPE_CHANGED')
    record = {key: manifest.get(key) for key in CONNECTION_FIELDS}
    record.update(spreadsheet_url=manifest['spreadsheet_url'],
        school_year=str(value['workbookSchoolYear']), workbook_name=value.get('displayName') or '출석부',
        workbook_role='canonical-v1', setup_account=value['email'], subject_key=value['subjectKey'],
        binding_generation=value['generation'], binding_protocol_version=value['protocolVersion'],
        monthly_sheet_ids=validate_month_sheet_ids(manifest.get('monthlySheetIds')))
    verification = value.get('desktopVerification')
    if isinstance(verification, dict):
        if verification.get('script_attestation') is not None:
            record['script_attestation'] = copy.deepcopy(verification['script_attestation'])
        if verification.get('script_update_required') is True:
            record['script_update_required'] = True
    return validate_verified_canonical_record(record)


def read_record(config_dir):
    client = client_for(config_dir)
    return record_from_scope(client.current(), client.email)


def record_exists(path):
    path = Path(path)
    if path.name != RECORD_NAME:
        return path.exists()
    return read_record(path.parent) is not None


def read_snapshot(path):
    from attendance_install_record import InstallRecordSnapshot
    record = read_record(Path(path).parent)
    if record is None:
        raise AttendanceBindingError('ATTENDANCE_BINDING_UNVERIFIED')
    raw = (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
    return InstallRecordSnapshot(raw, record, hashlib.sha256(raw).hexdigest())


def save_record(path, record, expected=None):
    """Save verification to the same server record, with compare-and-set."""
    client = client_for(Path(path).parent)
    return client.save_workbook_record(record, expected.record if expected else None)
