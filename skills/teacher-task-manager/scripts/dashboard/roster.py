"""Account-owned roster drafts and explicit, same-workbook synchronization."""
from __future__ import annotations

import attendance_reference_storage as attendance_references

import hashlib
import json
import re
import uuid
from functools import wraps
from pathlib import Path

from brity_bridge import component_lock, google_account, paths, process_win

FILENAME = 'homeroom-roster.generated.json'
MAX_STUDENTS = 199


class RosterRequestError(RuntimeError):
    def __init__(self, code, *, status=0, operation=''):
        self.code = code
        self.status = status if type(status) is int and 100 <= status <= 599 else 0
        self.operation = operation if operation in {'settings-read', 'roster-read', 'roster-write'} else ''
        super().__init__(code)


def validate_rows(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_STUDENTS:
        raise ValueError('학생명단은 1명부터 199명까지 입력해 주세요.')
    result, numbers, emails = [], set(), set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, list) or len(row) != 3 or any(not isinstance(v, str) for v in row):
            raise ValueError('번호·이름·이메일 세 칸으로 입력해 주세요.')
        number, name, email = [v.strip() for v in row]
        if not re.fullmatch(r'[0-9]{1,4}', number) or int(number) < 1:
            raise ValueError(f'{index}번째 학생 번호를 확인해 주세요.')
        number = str(int(number))
        if not name or len(name) > 100 or any(ord(c) < 32 for c in name):
            raise ValueError(f'{index}번째 학생 이름을 확인해 주세요.')
        if len(email) > 254 or not google_account.is_goedu_email(email):
            raise ValueError(f'{index}번째 학생 이메일을 빠짐없이 올바르게 입력해 주세요.')
        if number in numbers or email.casefold() in emails:
            raise ValueError(f'{index}번째 학생 번호 또는 이메일이 중복돼요.')
        numbers.add(number)
        emails.add(email.casefold())
        result.append([number, name, email])
    return result


def student_choices(rows):
    return [[f'{row[0]}{row[1]}'] for row in rows if row[0] and row[1]]


def digest(rows):
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def _locked(action):
    @wraps(action)
    def call(self, *args, **kwargs):
        with component_lock.exclusive_file_lock(self.path.with_suffix('.lock')):
            return action(self, *args, **kwargs)
    return call


def preserve_for_replacement(config_dir, account, scope):
    """Detach a pre-confirmation draft without deleting its original bytes.

    One receipt belongs to the captured old binding. Retries cannot detach a
    roster that the teacher explicitly entered after this preservation step.
    """
    from attendance_workbook_transition import _atomic_bytes, _atomic_json
    root = Path(config_dir)
    scope = {key: scope[key] for key in ('subjectKey', 'currentSchoolYear', 'generation', 'previousSpreadsheetId')}
    stamp = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
    history = component_lock.prepare_direct_directory(root / 'attendance-replacement-history')
    receipt = component_lock.prepare_direct_file_path(history / ('roster-scope-' + stamp + '.json'))
    editor = Editor(root, account, lambda: None, lambda *_: None)
    with component_lock.exclusive_file_lock(editor.path.with_suffix('.lock')):
        if receipt.exists():
            saved = json.loads(attendance_references.read_text(receipt, encoding='utf-8'))
            if not isinstance(saved, dict) or saved.get('scope') != scope:
                raise ValueError('기존 명단의 보관 기록을 확인하지 못했어요.')
            archive_hash = saved.get('archive')
            if archive_hash is not None:
                if not isinstance(archive_hash, str) or not re.fullmatch('[a-f0-9]{64}', archive_hash):
                    raise ValueError('기존 명단의 보관 기록을 확인하지 못했어요.')
                archived = component_lock.prepare_direct_file_path(history / ('homeroom-roster-' + archive_hash + '.json'))
                if hashlib.sha256(attendance_references.read_bytes(archived)).hexdigest() != archive_hash:
                    raise ValueError('기존 명단의 보관본을 확인하지 못했어요.')
            return
        archive_hash = None
        if editor.path.exists():
            data = editor._load()
            protected = data.get('replacement_protection') or {}
            if protected.get('scope') == scope:
                archive_hash = protected.get('archive')
                if not isinstance(archive_hash, str) or not re.fullmatch('[a-f0-9]{64}', archive_hash):
                    raise ValueError('기존 명단의 보관 기록을 확인하지 못했어요.')
                archived = component_lock.prepare_direct_file_path(history / ('homeroom-roster-' + archive_hash + '.json'))
                if hashlib.sha256(attendance_references.read_bytes(archived)).hexdigest() != archive_hash:
                    raise ValueError('기존 명단의 보관본을 확인하지 못했어요.')
            else:
                raw = attendance_references.read_bytes(editor.path)
                archive_hash = hashlib.sha256(raw).hexdigest()
                archived = component_lock.prepare_direct_file_path(history / ('homeroom-roster-' + archive_hash + '.json'))
                if not archived.exists():
                    _atomic_bytes(archived, raw)
                if attendance_references.read_bytes(archived) != raw:
                    raise ValueError('기존 명단의 보관본을 확인하지 못했어요.')
                data.update(replacement_protection={'scope': scope, 'archive': archive_hash}, revision=uuid.uuid4().hex)
                editor._store(data)
        _atomic_json(receipt, {'scope': scope, 'archive': archive_hash})


class Editor:
    def __init__(self, config_dir, account, read_remote, write_remote, on_failure=None, authorize_target=None):
        self.path = Path(config_dir) / FILENAME
        self.account = google_account.require_goedu_email(account).casefold()
        self.read_remote, self.write_remote = read_remote, write_remote
        self.on_failure = on_failure
        self.authorize_target = authorize_target

    def _explicit_replacement_target(self, data, expected_revision, prior_revision=None):
        """Validate an explicit use of the saved unbound draft, never infer its age."""
        if not expected_revision or expected_revision != data['revision'] or not data['pending']:
            raise ValueError('Roster revision changed')
        protected = data['replacement_protection']
        old = protected['scope']
        archive_hash = protected['archive']
        if not isinstance(archive_hash, str) or not re.fullmatch('[a-f0-9]{64}', archive_hash):
            raise ValueError('Roster archive unavailable')
        history = self.path.parent / 'attendance-replacement-history'
        archived = attendance_references.read_bytes(component_lock.prepare_direct_file_path(history / ('homeroom-roster-' + archive_hash + '.json')))
        if hashlib.sha256(archived).hexdigest() != archive_hash:
            raise ValueError('Roster archive changed')
        original = json.loads(archived)
        if prior_revision is not None and original.get('revision') != prior_revision:
            raise ValueError('Roster revision is not the preserved draft')
        if (original.get('account') != self.account or original.get('replacement_protection')
                or original.get('pending') is not True or not original.get('revision')
                or original.get('rows') != data['rows']
                or any(item.get(key) is not None for item in (original, data) for key in ('target', 'baseline'))):
            raise ValueError('Roster belongs to a previous workbook')
        stamp = hashlib.sha256(json.dumps(old, sort_keys=True).encode()).hexdigest()
        receipt = json.loads(attendance_references.read_text(component_lock.prepare_direct_file_path(history / ('roster-scope-' + stamp + '.json')), encoding='utf-8'))
        if receipt != {'scope': old, 'archive': archive_hash}:
            raise ValueError('Roster preservation changed')
        saved = json.loads(attendance_references.read_text(component_lock.prepare_direct_file_path(paths.attendance_setup_status_path(self.path.parent)), encoding='utf-8'))
        action = saved['attendance_action']
        if (saved.get('account') != self.account or saved.get('state') != 'created'
                or action.get('phase') != 'published' or action.get('reason') not in ('replace-trashed', 'replace-unavailable')
                or action.get('subjectKey') != old['subjectKey']
                or action.get('expectedSchoolYear') != old['currentSchoolYear']
                or action.get('expectedGeneration') != old['generation']
                or action.get('previousSpreadsheetId') != old['previousSpreadsheetId']
                or action.get('publishedGeneration') != old['generation'] + 1
                or not action.get('operationId') or not action.get('publishedSpreadsheetId')
                or action['publishedSpreadsheetId'] == old['previousSpreadsheetId']):
            raise ValueError('Replacement publication changed')
        return {'subjectKey': old['subjectKey'], 'workbookSchoolYear': old['currentSchoolYear'],
                'generation': action['publishedGeneration'], 'spreadsheetId': action['publishedSpreadsheetId']}

    def _authorize_explicit_target(self, expected):
        actual = self.authorize_target() if self.authorize_target else None
        if not isinstance(actual, dict) or actual.get('authorized') is not True or any(actual.get(k) != v for k, v in expected.items()):
            raise ValueError('Current workbook changed')

    def _explicit_current_target(self, data, expected_revision):
        """Use preserved rows only for the teacher's explicit current-sheet save."""
        if not expected_revision or expected_revision != data['revision']:
            raise ValueError('Roster revision changed')
        protected = data['replacement_protection']
        old, archive_hash = protected['scope'], protected['archive']
        if not isinstance(archive_hash, str) or not re.fullmatch('[a-f0-9]{64}', archive_hash):
            raise ValueError('Roster archive unavailable')
        history = self.path.parent / 'attendance-replacement-history'
        raw = attendance_references.read_bytes(component_lock.prepare_direct_file_path(history / ('homeroom-roster-' + archive_hash + '.json')))
        if hashlib.sha256(raw).hexdigest() != archive_hash:
            raise ValueError('Roster archive changed')
        original = json.loads(raw)
        if (original.get('account') != self.account or original.get('rows') != data['rows']
                or not original.get('revision') or any(original.get(key) != data.get(key) for key in ('target', 'baseline'))):
            raise ValueError('Roster preservation changed')
        stamp = hashlib.sha256(json.dumps(old, sort_keys=True).encode()).hexdigest()
        receipt = json.loads(attendance_references.read_text(component_lock.prepare_direct_file_path(history / ('roster-scope-' + stamp + '.json')), encoding='utf-8'))
        if receipt != {'scope': old, 'archive': archive_hash}:
            raise ValueError('Roster preservation changed')
        current = self.authorize_target() if self.authorize_target else None
        if (not isinstance(current, dict) or current.get('authorized') is not True
                or not old.get('subjectKey') or current.get('subjectKey') != old['subjectKey']
                or type(old.get('currentSchoolYear')) is not int or current.get('workbookSchoolYear') != old['currentSchoolYear']
                or type(old.get('generation')) is not int or type(current.get('generation')) is not int
                or current['generation'] < max(1, old['generation'])
                or not isinstance(current.get('spreadsheetId'), str) or not current['spreadsheetId']):
            raise ValueError('Current workbook changed')
        expected = {key: current[key] for key in ('subjectKey', 'workbookSchoolYear', 'generation', 'spreadsheetId')}
        if protected.get('sync_scope') is not None and protected['sync_scope'] != expected:
            raise ValueError('Current workbook changed')
        return expected

    def _protected_detail(self, data):
        try:
            self._explicit_replacement_target(data, data['revision'])
        except (OSError, ValueError, TypeError, KeyError):
            return '이전 명단은 보관했어요. 현재 출석부의 명단을 확인한 뒤 입력해 주세요.'
        return '입력한 명단은 보관 중이에요. [명단 저장] 또는 구글 연결의 [학생명단 연결]을 눌러 현재 출석부에 반영해 주세요.'

    def _load(self):
        if not self.path.exists():
            return {'version': 1, 'account': self.account, 'rows': [], 'revision': '', 'pending': False,
                    'target': None, 'baseline': None}
        try:
            data = json.loads(attendance_references.read_text(component_lock.prepare_direct_file_path(self.path), encoding='utf-8'))
            if not isinstance(data, dict) or data.get('version') != 1 or data.get('account') != self.account:
                raise ValueError()
            if not isinstance(data.get('rows'), list) or not isinstance(data.get('revision'), str):
                raise ValueError()
            return data
        except (OSError, ValueError, TypeError):
            raise ValueError('저장된 명단의 계정과 내용을 확인하지 못했어요. 기존 명단은 보존했습니다.') from None

    def _store(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        component_lock.atomic_write_text_unique(self.path, attendance_references.protect(self.path, (json.dumps(data, ensure_ascii=False) + '\n').encode('utf-8')).decode('utf-8'))

    @staticmethod
    def _view(data, state, detail=''):
        return {'rows': data['rows'], 'revision': data['revision'], 'pending': data['pending'],
                'state': state, 'detail': detail, 'linked': bool(data.get('target'))}

    @_locked
    def read(self, refresh=False, local_only=False, expected_revision=None):
        data = self._load()
        protected = data.get('replacement_protection')
        if local_only:
            accepted = bool(expected_revision and expected_revision == data['revision'])
            if not accepted and protected and expected_revision:
                try:
                    self._explicit_replacement_target(data, data['revision'], prior_revision=expected_revision)
                    accepted = True
                except (OSError, ValueError, TypeError, KeyError):
                    pass
            state = 'conflict' if protected else 'pending' if data['pending'] else 'synced' if data.get('target') else 'local'
            detail = self._protected_detail(data) if protected else ''
            return {**self._view(data, state, detail), 'revision_accepted': accepted}
        if data['pending'] and not refresh and not protected:
            return self._view(data, 'pending', '명단은 저장됐어요. 출석부에 반영할 내용이 남아 있어요.')
        try:
            remote = self.read_remote()
        except Exception:
            return self._view(data, 'unavailable', '시트 명단을 읽지 못했어요. 저장된 명단은 그대로입니다. 다시 불러오기를 눌러 주세요.')
        if remote is None:
            if protected:
                return self._view(data, 'conflict', self._protected_detail(data))
            return self._view(data, 'local', '명단을 저장하면 출석부 연결 후 반영해요.')
        if protected and not refresh:
            return self._view(data, 'conflict', self._protected_detail(data))
        if protected or data['rows'] != remote['rows'] or data.get('target') != remote['target'] or data['pending']:
            data.update(rows=remote['rows'], target=remote['target'], baseline=digest(remote['rows']),
                        pending=False, revision=uuid.uuid4().hex)
            data.pop('replacement_protection', None)
            self._store(data)
        return self._view(data, 'synced', '연결된 출석부의 명단을 불러왔어요.')

    @_locked
    def save(self, rows, expected_revision):
        rows = validate_rows(rows)
        data = self._load()
        if data.get('replacement_protection'):
            raise ValueError(self._protected_detail(data))
        if expected_revision != data['revision']:
            if data['pending'] and data['rows'] == rows:
                return self._view(data, 'pending', '명단을 이 컴퓨터에 저장했어요.')
            raise ValueError('다른 창에서 명단이 바뀌었어요. 다시 불러온 뒤 수정해 주세요.')
        data.update(rows=rows, revision=uuid.uuid4().hex, pending=True)
        self._store(data)
        return self._view(data, 'pending', '명단을 이 컴퓨터에 저장했어요.')

    @_locked
    def sync(self, expected_revision=None, *, use_current_workbook=False):
        data = self._load()
        if expected_revision is not None and (not expected_revision or expected_revision != data['revision']):
            return self._view(data, 'conflict', '저장된 명단이 바뀌었어요. 현재 명단을 확인한 뒤 연결해 주세요.')
        protected = data.get('replacement_protection')
        explicit_target = None
        current_save = protected and use_current_workbook is True and expected_revision is not None
        if protected and expected_revision is not None and not current_save:
            try:
                explicit_target = self._explicit_replacement_target(data, expected_revision)
            except (OSError, ValueError, TypeError, KeyError):
                return self._view(data, 'conflict', '보관된 명단과 현재 출석부의 연결 근거를 확인하지 못했어요. 입력한 명단은 그대로 보관했습니다.')
        if protected and explicit_target is None and not current_save:
            return self._view(data, 'conflict', self._protected_detail(data))
        if not data['pending'] and not current_save:
            return self._view(data, 'synced' if data.get('target') else 'local')
        rows = validate_rows(data['rows'])
        stage = '명단 조회'
        try:
            if current_save:
                from attendance_binding import AttendanceBindingError
                try:
                    explicit_target = self._explicit_current_target(data, expected_revision)
                except AttendanceBindingError:
                    raise
                except (OSError, ValueError, TypeError, KeyError):
                    return self._view(data, 'conflict', '보관된 명단 또는 현재 출석부의 계정·학년도를 확인하지 못했어요. 입력한 명단은 그대로 보관했습니다.')
            if explicit_target:
                self._authorize_explicit_target(explicit_target)
            remote = self.read_remote()
            if remote is None:
                return self._view(data, 'waiting', '명단은 저장됐어요. 출석부 준비가 끝나면 반영해요.')
            target, before = remote['target'], remote['rows']
            if explicit_target:
                empty_template = current_save and all(len(row) == 3 and not row[1] and not row[2]
                    and (not row[0] or re.fullmatch(r'[0-9]{1,4}', row[0])) for row in before)
                if (target.get('spreadsheet_id') != explicit_target['spreadsheetId']
                        or (before and before != rows and not empty_template)
                        or (protected.get('sync_target') is not None and protected['sync_target'] != target)):
                    return self._view(data, 'conflict', '현재 출석부 또는 명단이 달라졌어요. 입력한 명단은 보관하고 덮어쓰지 않았습니다.')
                protected['sync_target'] = target
                if current_save:
                    protected['sync_scope'] = explicit_target
                    data['pending'] = True
                self._store(data)
            conflict = (data.get('target') is not None and data['target'] != target)
            conflict = conflict or (before != rows and (
                (data.get('target') is None and bool(before))
                or (data.get('target') is not None and digest(before) != data.get('baseline'))))
            if conflict and not current_save:
                return self._view(data, 'conflict', '연결된 출석부 또는 시트 명단이 달라졌어요. 덮어쓰지 않았습니다. 시트 명단을 다시 불러온 뒤 수정해 주세요.')
            if data.get('target') is None and not explicit_target:
                data.update(target=target, baseline=digest(before))
                self._store(data)
            if before != rows or remote.get('dropdown') != student_choices(rows):
                stage = '시트 저장'
                if explicit_target:
                    self._authorize_explicit_target(explicit_target)
                self.write_remote(remote, rows)
            stage = '저장 결과 확인'
            after = self.read_remote()
            if not after or after['target'] != target or after['rows'] != rows or after.get('dropdown') != student_choices(rows):
                raise ValueError('unconfirmed write')
            if explicit_target:
                self._authorize_explicit_target(explicit_target)
                data.pop('replacement_protection', None)
                data.update(target=target, revision=uuid.uuid4().hex)
            data.update(pending=False, baseline=digest(rows))
            self._store(data)
            return self._view(data, 'synced', '학생명단과 출석부의 학생 선택목록에 반영했어요.')
        except Exception as error:
            # Keep only controlled provenance before the legacy result drops the
            # exception. Never forward its text, identity or request payload.
            from attendance_binding import AttendanceBindingError, OPERATION_NAMES
            origin = getattr(error, 'attendance_auth_origin', '')
            if origin not in {'restore-failed', 'local-session-missing', 'refresh-rejected'}:
                origin = 'server-rejected' if isinstance(error, AttendanceBindingError) and type(error.status) is int and 400 <= error.status <= 599 else 'unclassified'
            status = getattr(error, 'status', 0)
            diagnostic = {
                'stage': {'명단 조회': 'roster-read', '시트 저장': 'roster-write', '저장 결과 확인': 'roster-readback'}[stage],
                'origin': origin,
                'http_status': status if type(status) is int and 100 <= status <= 599 else 0,
                'operation': (error.operation if isinstance(error, AttendanceBindingError) and error.operation in OPERATION_NAMES
                              else error.operation if isinstance(error, RosterRequestError) else ''),
            }
            if self.on_failure:
                try:
                    self.on_failure(diagnostic)
                except Exception:
                    pass  # Diagnostics must never change the save result.
            code = str(getattr(error, 'code', ''))
            if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', code):
                code = type(error).__name__
            return {**self._view(data, 'unavailable', f'{stage}에 실패했어요 ({code}). 입력한 명단은 이 컴퓨터에 보관돼 있습니다. [명단 저장]을 다시 눌러 주세요.'), 'failure_code': code, 'failure_diagnostic': diagnostic}


class Sheet:
    """The adapter writes only approved roster cells and the student dropdown source."""
    def __init__(self, config_dir, account, run, gws):
        self.config_dir, self.account, self.run, self.gws = Path(config_dir), account, run, gws

    def _request(self, method, params, body=None):
        command = [self.gws, 'sheets', 'spreadsheets', 'values', method, '--params', json.dumps(params, ensure_ascii=False)]
        if body is not None:
            command += ['--json', json.dumps(body, ensure_ascii=False)]
        command += ['--format', 'json']
        read = method in {'get', 'batchGet'}
        return self._execute(command, operation='roster-read' if read else 'roster-write', safe_read=read)

    def _execute(self, command, *, operation, safe_read=False):
        for attempt in range(3 if safe_read else 1):
            result = self.run(command)
            exit_code, output = result[:2] if isinstance(result, tuple) else (0, result)
            try:
                data = process_win.parse_first_json(output)
            except (ValueError, TypeError):
                raise RosterRequestError('GOOGLE_RESPONSE_INVALID', operation=operation) from None
            error = data.get('error') if isinstance(data, dict) else None
            if not exit_code and not error:
                return data
            status = error.get('code') if isinstance(error, dict) else None
            if safe_read and type(status) is int and status in {429, 500, 502, 503, 504} and attempt < 2:
                import time
                time.sleep(attempt + 1)
                continue
            code = f'GOOGLE_{status}' if type(status) is int and 400 <= status <= 599 else 'GOOGLE_COMMAND_FAILED'
            raise RosterRequestError(code, status=status, operation=operation)

    def _settings_run(self, command):
        if list(command[1:5]) == ['sheets', 'spreadsheets', 'values', 'get']:
            # Preserve the Google error before the shared Chat reader reduces it
            # to CentralChatError. Recovery remains local to roster reads.
            return json.dumps(self._execute(command, operation='settings-read', safe_read=True), ensure_ascii=False)
        return self.run(command)

    def _target(self):
        from attendance_install_record import read_verified_canonical_record
        from dashboard import engine
        path = paths.attendance_install_record_path(self.config_dir)
        from attendance_server_record import record_exists
        if not record_exists(path):
            return None
        record = read_verified_canonical_record(path)
        # Verify both the currently authenticated identity and the canonical owner.
        engine._require_google_target_account(self.run, self.gws, self.account)
        title = engine._verified_attendance_roster_name(record, self.config_dir, self._settings_run, self.gws)
        return {'spreadsheet_id': record['spreadsheet_id'], 'title': title}

    def read(self):
        target = self._target()
        if target is None:
            return None
        title = target['title'].replace("'", "''")
        ranges = [f"'{title}'!A:C", "'드롭다운'!J2:J200"]
        data = self._request('batchGet', {'spreadsheetId': target['spreadsheet_id'], 'ranges': ranges,
                                         'valueRenderOption': 'FORMATTED_VALUE'})
        blocks = data.get('valueRanges') if isinstance(data, dict) else None
        if not isinstance(blocks, list) or len(blocks) != 2:
            raise ValueError('Roster unavailable')
        values = blocks[0].get('values', [])
        if not values or values[0][:2] != ['번호', '이름'] or len(values[0]) < 3 or '이메일' not in str(values[0][2]):
            raise ValueError('Roster layout unavailable')
        rows = [[str(v) for v in (row + ['', '', ''])[:3]] for row in values[1:]]
        while rows and not any(rows[-1]):
            rows.pop()
        if len(rows) > MAX_STUDENTS:
            raise ValueError('Roster too large')
        dropdown = [[str(row[0])] for row in blocks[1].get('values', []) if row and str(row[0])]
        return {'target': target, 'rows': rows, 'dropdown': dropdown}

    def write(self, before, rows):
        # A second read detects edits made while the editor was preparing its save.
        current = self.read()
        if current != before or self._target() != before['target']:
            raise ValueError('Roster changed')
        title = before['target']['title'].replace("'", "''")
        count = max(len(before['rows']), len(rows))
        values = rows + [['', '', ''] for _ in range(count - len(rows))]
        choices = student_choices(rows)
        self._request('batchUpdate', {'spreadsheetId': before['target']['spreadsheet_id']}, {
            'valueInputOption': 'RAW',
            'data': [
                {'range': f"'{title}'!A2:C{count + 1}", 'values': values},
                {'range': "'드롭다운'!J2:J200", 'values': choices + [[''] for _ in range(MAX_STUDENTS - len(choices))]},
            ],
        })
