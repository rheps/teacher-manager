"""One active configuration and short owner tokens; never archive account trees.

The original settings remain in place. A confirmed different identity receives a
fresh editable folder, and returning identities do not restore past folders.
Only actual writes and credential changes share the exclusion lock. Reads use
atomic metadata and compare their owner token again before returning results.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path

from . import component_lock, google_account

STATE_NAME = 'account-session.json'
SETTINGS = 'brity-bridge/settings.json'
PC_FIELDS = frozenset(('hotkey', 'brity_download_dir', 'error_reports_enabled', 'gws_command'))
CURRENT_DIR = 'account-current'
# Compatibility recovery reads only these named settings, never archives/logs.
ESSENTIAL = frozenset(('profile.generated.json', 'teacher-profile.csv', 'weekly-timetable.xlsx',
    'weekly-timetable.csv', 'setup-state.json', 'attendance-install.generated.json',
    'attendance-setup-status.generated.json', 'homeroom-roster.generated.json',
    'attendance-update-progress.generated.json', SETTINGS))
PHASES = frozenset(('active', 'signed_out', 'login_pending', 'logout_pending', 'paused', 'checking'))
_LOCAL = threading.local()
_SAFE_MESSAGE = '사용 중인 계정이 바뀌었어요. 현재 계정의 화면에서 다시 진행해 주세요.'
_STORAGE_MESSAGE = '이 컴퓨터의 설정을 읽거나 저장하지 못했어요. 기존 자료는 보존했습니다. 저장 폴더의 접근 권한과 남은 공간을 확인해 주세요.'


class AccountSessionError(RuntimeError):
    def __init__(self, message=_SAFE_MESSAGE):
        super().__init__(message)


class AccountSessionBusy(AccountSessionError):
    """An actual write is still running; credential changes must wait."""


class AccountSessionStorageError(AccountSessionError):
    """Local configuration failure, independent of Google authentication."""


def root_config_dir(config_dir):
    root = Path(os.path.abspath(str(config_dir)))
    if root.parent.name == CURRENT_DIR and re.fullmatch('[0-9a-f]{16}', root.name):
        return root.parent.parent
    return root


def _email(value):
    if not google_account.is_goedu_email(value):
        raise AccountSessionError()
    return google_account.require_goedu_email(value).casefold()


def _storage_error(root, error):
    # Minimal local diagnostic deliberately excludes paths, account identifiers,
    # exception text, teacher data and credentials. A full disk may prevent it.
    try:
        _write(root_config_dir(root) / 'account-local-error.json',
               (json.dumps({'code': 'LOCAL_ACCOUNT_SETTINGS_UNAVAILABLE',
                            'error_type': type(error).__name__}) + '\n').encode())
    except (OSError, ValueError):
        pass
    return AccountSessionStorageError(_STORAGE_MESSAGE)


@contextmanager
def session_lock(config_dir, timeout=10.0, *, _name='.account-write.lock'):
    """Reentrant write exclusion; never acquired by ordinary state reads."""
    root = root_config_dir(config_dir)
    key = os.path.normcase(str(root / _name))
    held = getattr(_LOCAL, 'held', None)
    if held is None:
        held = _LOCAL.held = set()
    if key in held:
        yield
        return
    lock = component_lock.exclusive_file_lock(root / _name, timeout=timeout)
    try:
        lock.__enter__()
    except TimeoutError as exc:
        raise AccountSessionBusy('저장 또는 전송 작업이 진행 중이에요. 끝난 뒤 다시 진행해 주세요.') from exc
    except (OSError, ValueError) as exc:
        raise _storage_error(root, exc) from exc
    held.add(key)
    try:
        yield
    finally:
        held.remove(key)
        lock.__exit__(None, None, None)


@contextmanager
def auth_observation(config_dir, expected_token, account='', timeout=10.0):
    """Reconcile one auth reply only if its starting generation still owns it.

    Same-account observations take only a short metadata lock. Transitions also
    exclude actual writes, always in write-then-metadata lock order.
    """
    root = root_config_dir(config_dir)
    state = read_state(root)
    unchanged = state.get('phase') == 'active' and state.get('account') == account and state.get('settings_owner') == account
    write_lock = nullcontext() if unchanged else session_lock(root, timeout=timeout)
    with write_lock, session_lock(root, timeout=timeout, _name='.account-context.lock'):
        state = read_state(root)
        # Foreground/background startup may confirm the same activation at once.
        # Accept only that exact preceding token, never an arbitrary old reply
        # from the same email (which may have signed out or switched A -> B -> A).
        same_activation = (bool(account) and state.get('phase') == 'active'
            and state.get('account') == account and state.get('settings_owner') == account
            and list(expected_token or ()) == state.get('activated_from'))
        if expected_token is None or (
                tuple(expected_token) != (state['account'], state['generation']) and not same_activation):
            raise AccountSessionError()
        yield state


def _read(path):
    path = component_lock.prepare_direct_file_path(path)
    with path.open('rb') as stream:
        component_lock.assert_open_file_is_direct(path, stream)
        return stream.read()


def _json(path):
    value = json.loads(_read(path).decode('utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('Expected settings object')
    return value


def _write(path, content):
    path = component_lock.prepare_direct_file_path(path)
    # Short independent basename also supports bounded legacy recovery paths.
    temporary = path.with_name('.' + uuid.uuid4().hex[:12] + '.tmp')
    identity = None
    try:
        with temporary.open('xb') as stream:
            identity = component_lock.assert_open_file_is_direct(temporary, stream)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        component_lock.prepare_direct_file_path(path)
        os.replace(temporary, path)
    finally:
        if identity is not None:
            component_lock.remove_owned_file(temporary, identity)


def _write_json(path, value):
    _write(path, (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


def peek_state(config_dir):
    root = root_config_dir(config_dir)
    try:
        path = component_lock.prepare_direct_file_path(root / STATE_NAME)
        if not path.exists():
            return {'managed': False, 'account': '', 'phase': 'legacy', 'generation': '', 'data_dir': ''}
        state = _json(path)
        if (state.get('managed') is not True or state.get('phase') not in PHASES
                or not re.fullmatch('[0-9a-f]{32}', str(state.get('generation', '')))
                or not isinstance(state.get('account'), str)):
            raise ValueError('Invalid account context')
        if state['account']:
            _email(state['account'])
        if state['phase'] in ('active', 'checking') and not state['account']:
            raise ValueError('Missing owner')
        folder = state.get('data_dir', '')
        if folder and not re.fullmatch(CURRENT_DIR + '/[0-9a-f]{16}', str(folder)):
            raise ValueError('Invalid settings location')
        return state
    except (OSError, ValueError, TypeError) as exc:
        raise _storage_error(root, exc) from exc


def read_state(config_dir):
    return peek_state(config_dir)


def active_config_dir(config_dir, state=None):
    root = root_config_dir(config_dir)
    state = peek_state(root) if state is None else state
    folder = state.get('data_dir', '')
    target = root / folder if folder else root
    # Validate direct ancestry; no symlink/junction redirects to another owner.
    try:
        component_lock.prepare_direct_directory(target)
    except (OSError, ValueError) as exc:
        raise _storage_error(root, exc) from exc
    return target


def peek_token(config_dir):
    state = peek_state(config_dir)
    return state['account'], state['generation']


def token(config_dir):
    return peek_token(config_dir)


@contextmanager
def work(config_dir, expected_token=None, require_active=True, timeout=10.0):
    with session_lock(config_dir, timeout=timeout):
        state = read_state(config_dir)
        if expected_token is not None and tuple(expected_token) != (state['account'], state['generation']):
            raise AccountSessionError()
        if require_active and state['managed'] and state['phase'] != 'active':
            raise AccountSessionError('설정에서 Google 연결을 확인한 뒤 다시 진행해 주세요.')
        yield state


def _legacy_owner(root):
    owners = set()
    for name, field in (('attendance-install.generated.json', 'setup_account'),
                        ('attendance-setup-status.generated.json', 'account'),
                        ('setup-state.json', 'account')):
        path = component_lock.prepare_direct_file_path(root / name)
        if path.exists():
            value = _json(path).get(field)
            if value:
                owners.add(_email(value))
    return next(iter(owners), '') if len(owners) <= 1 else 'conflicting'


def _state(root, phase, account='', **extra):
    value = {'managed': True, 'phase': phase, 'account': account,
             'generation': uuid.uuid4().hex, **extra}
    try:
        with session_lock(root, _name='.account-context.lock'):
            _write_json(root / STATE_NAME, value)
    except (OSError, ValueError, TypeError) as exc:
        raise _storage_error(root, exc) from exc
    return value


def _begin(config_dir, phase):
    with session_lock(config_dir):
        root = root_config_dir(config_dir)
        state = read_state(root)
        # Each actual login start owns a fresh generation, including retries
        # after cancellation. The bridge rejects duplicate running starts first.
        if state['phase'] == phase and phase != 'login_pending':
            return state
        extra = {key: state[key] for key in ('data_dir', 'settings_owner', 'recovery_account', 'recovery_snapshot') if key in state}
        # No settings inventory, copies, deletions or profile restoration here.
        return _state(root, phase, state['account'], **extra)


def begin_login(config_dir):
    return _begin(config_dir, 'login_pending')


def begin_logout(config_dir):
    return _begin(config_dir, 'logout_pending')


def suspend(config_dir):
    # A temporarily unavailable Google read is not a durable local account phase.
    return read_state(config_dir)


def _recover_missing_essentials(root, account, state):
    if (root / 'attendance-install.generated.json').exists() and any((root / name).exists() for name in ('profile.generated.json', 'teacher-profile.csv')):
        return  # Valid active settings always take precedence over old archives.
    snapshot = state.get('recovery_snapshot')
    if not snapshot or state.get('recovery_account') != account:
        return
    missing = [name for name in ESSENTIAL if not (root / name).exists()]
    if not missing:
        return
    if not re.fullmatch('[0-9a-f]{32}', str(snapshot)):
        raise ValueError('Invalid recovery pointer')
    folder = root / 'account-profiles' / hashlib.sha256(account.encode()).hexdigest() / snapshot
    manifest = _json(folder / 'manifest.json')
    if manifest.get('version') != 1 or manifest.get('account') != account or not isinstance(manifest.get('files'), dict):
        raise ValueError('Unverified recovery manifest')
    data = {}
    for name in missing:
        digest = manifest['files'].get(name)
        if digest is None:
            continue
        content = _read(folder / name)
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError('Recovery settings checksum mismatch')
        if name in ('attendance-install.generated.json', 'attendance-setup-status.generated.json', 'setup-state.json'):
            value = json.loads(content.decode('utf-8-sig'))
            owner = value.get('setup_account') if name == 'attendance-install.generated.json' else value.get('account')
            if not owner or _email(owner) != account:
                raise ValueError('Recovery settings owner mismatch')
        data[name] = content
    # Verify every selected file before restoring any missing essential setting.
    for name, content in data.items():
        if not (root / name).exists():
            _write(root / name, content)


def activate_verified(config_dir, email, explicit_login=False):
    account = _email(email)
    root = root_config_dir(config_dir)
    state = read_state(root)
    if state['phase'] == 'active' and state['account'] == account and state.get('settings_owner') == account:
        return {**state, 'changed': False}
    with session_lock(root):
        state = read_state(root)
        if state['phase'] == 'active' and state['account'] == account and state.get('settings_owner') == account:
            return {**state, 'changed': False}
        try:
            current = active_config_dir(root, state)
            owner = state.get('settings_owner') or _legacy_owner(current)
            # Old metadata may be paused before a snapshot, or halfway through a
            # destructive restore. Only recover missing verified same-owner files.
            if not state.get('data_dir') and owner in ('', account) and state.get('recovery_account') == account:
                _recover_missing_essentials(root, account, state)
                owner = _legacy_owner(root)
            has_saved_data = any((current / name).exists() for name in ESSENTIAL if name != SETTINGS)
            if owner == account or (not owner and not has_saved_data and not state.get('settings_owner')):
                folder = state.get('data_dir', '')
            else:
                folder = CURRENT_DIR + '/' + uuid.uuid4().hex[:16]
                target = component_lock.prepare_direct_directory(root / folder)
                settings_path = component_lock.prepare_direct_file_path(current / SETTINGS)
                pc = {k: v for k, v in _json(settings_path).items() if k in PC_FIELDS} if settings_path.exists() else {}
                _write_json(target / SETTINGS, pc)
            return {**_state(root, 'active', account, settings_owner=account, data_dir=folder,
                            activated_from=[state['account'], state['generation']]), 'changed': True}
        except (OSError, ValueError, TypeError) as exc:
            raise _storage_error(root, exc) from exc


def complete_logout(config_dir):
    with session_lock(config_dir):
        root = root_config_dir(config_dir)
        state = read_state(root)
        if state['phase'] == 'signed_out':
            return state
        extra = {key: state[key] for key in ('settings_owner', 'data_dir', 'recovery_account', 'recovery_snapshot') if key in state}
        return _state(root, 'signed_out', state['account'], **extra)
