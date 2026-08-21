# skills/teacher-task-manager/scripts/dashboard/bridge.py
"""pywebview js_api — 화면(JS)이 부르는 유일한 문.

규칙: 이 파일은 표시 문구 조립과 engine 호출만 한다. 화면 로직을 JS에,
업무 로직을 engine 밖에 두지 않는다. 모든 공개 메서드는 guarded로 감싸
{"ok": true, "data": ...} / {"ok": false, "error": "한국어 한 문장"}만 돌려준다.
"""
from __future__ import annotations

import functools
import json
import os
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brity_bridge import (
    bundle_paths,
    capture_store,
    component_lock,
    gws_env,
    paths,
    process_win,
)
from brity_bridge.settings import load_settings
from dashboard import engine
from dashboard import external_url
from dashboard import version

SETUP_STATE_NAME = "setup-state.json"
SETUP_STATE_VERSION = 2
SETUP_LAST_STEP = 9
_FRESH_STATE = {
    "version": SETUP_STATE_VERSION,
    "completed": False,
    "step": 1,
    "max_step": 1,
    "draft": {"profile": {}, "grid": [], "bridge": {}},
}
_V1_STEP_TO_V2 = {1: 1, 2: 3, 3: 4, 4: 5, 5: 6, 6: 7, 7: 9}
_ATTENDANCE_AUTH_BLOCKED_STATES = {
    "gws-required", "login-required", "account-required", "auth-error"
}
_DEFAULT_SCREEN_FAILURE = (
    "작업을 마치지 못했어요. Teacher Manager를 다시 시작한 뒤 다시 시도해 주세요."
)
_SCREEN_FAILURES = {
    "get_app_info": (
        "프로그램을 여는 데 필요한 정보를 읽지 못했어요. "
        "Teacher Manager를 다시 시작해 주세요."
    ),
    "save_setup_state": "처음 설정 내용을 저장하지 못했어요. 프로그램을 다시 시작한 뒤 다시 시도해 주세요.",
    "finish_setup": "처음 설정을 마무리하지 못했어요. @goedu.kr Google 로그인과 입력한 내용을 확인한 뒤 다시 시도해 주세요.",
    "restart_setup": "처음 설정 안내를 다시 열지 못했어요. Teacher Manager를 다시 시작해 주세요.",
    "read_profile": "이 컴퓨터에 저장된 내 정보를 읽지 못했어요. 프로그램을 다시 시작해 주세요.",
    "read_grid": "이 컴퓨터에 저장된 시간표를 읽지 못했어요. 프로그램을 다시 시작해 주세요.",
    "get_messenger_settings": "이 컴퓨터에 저장된 메신저 설정을 읽지 못했어요. 프로그램을 다시 시작해 주세요.",
    "save_profile_grid": "이 컴퓨터 설정을 저장하지 못했어요. 입력한 내용을 확인한 뒤 다시 시도해 주세요.",
    "save_messenger": "이 컴퓨터 설정을 저장하지 못했어요. 입력한 내용을 확인한 뒤 다시 시도해 주세요.",
    "apply_all": "이 컴퓨터 설정을 저장하고 적용하지 못했어요. 입력한 내용을 확인한 뒤 다시 시도해 주세요.",
    "choose_attachment_folder": (
        "첨부파일 폴더를 열지 못했어요. 폴더 위치를 직접 입력하거나 "
        "Teacher Manager를 다시 시작해 주세요."
    ),
    "check_attachment_folder": "첨부파일 폴더 상태를 확인하지 못했어요. 폴더 위치를 다시 확인해 주세요.",
    "attendance_status": "출결 상태를 확인하지 못했어요. 현재 Windows 계정의 Google 로그인과 인터넷 연결을 확인한 뒤 다시 시도해 주세요.",
    "attendance_status_cached": "저장된 출결 상태를 확인하지 못했어요. Teacher Manager를 다시 시작해 주세요.",
    "ensure_attendance": "출결 자료를 준비하지 못했어요. 현재 Windows 계정의 Google 로그인과 인터넷 연결을 확인한 뒤 다시 시도해 주세요.",
    "consolidate_attendance": "출결 자료를 하나로 정리하지 못했어요. 기존 자료는 그대로입니다. Google 로그인과 인터넷 연결을 확인한 뒤 다시 시도해 주세요.",
    "start_new_attendance": "새 학년도 출석부를 시작하지 못했어요. 기존 자료는 그대로입니다. Google 로그인과 인터넷 연결을 확인한 뒤 다시 시도해 주세요.",
    "attendance_prepare_start": "출결 준비를 시작하지 못했어요. 이 컴퓨터 설정과 Google 로그인을 확인한 뒤 다시 시도해 주세요.",
    "attendance_prepare_status": "출결 준비 상태를 확인하지 못했어요. 설정에서 Google 로그인과 인터넷 연결을 확인한 뒤 다시 시도해 주세요.",
    "attendance_first_setup_status": "출석부의 처음 설정 상태를 확인하지 못했어요. 출석부가 열리는지 확인한 뒤 다시 시도해 주세요.",
    "attendance_script_update_status": "출결 기능 상태를 확인하지 못했어요. 처음 준비하던 @goedu.kr Google 계정으로 로그인한 뒤 다시 시도해 주세요. 기존 자료는 그대로입니다.",
    "attendance_script_update_apply": "출결 기능을 바꾸지 못했어요. 처음 준비하던 @goedu.kr Google 계정으로 로그인한 뒤 다시 시도해 주세요. 기존 자료는 그대로입니다.",
    "attendance_chat_status": "학급 단톡방 상태를 확인하지 못했어요. 출결 준비와 @goedu.kr Google 로그인을 확인한 뒤 다시 시도해 주세요.",
    "attendance_chat_connect": "학급 단톡방 연결을 시작하지 못했어요. 출결 기능 안내와 @goedu.kr Google 로그인을 확인한 뒤 다시 시도해 주세요.",
    "attendance_chat_spaces": "학급 단톡방 목록을 가져오지 못했어요. 출결 기능 안내와 @goedu.kr Google 로그인을 확인한 뒤 다시 시도해 주세요.",
    "attendance_chat_set_space": "학급 단톡방 선택을 저장하지 못했어요. 출결 기능 안내와 @goedu.kr Google 로그인을 확인한 뒤 다시 시도해 주세요.",
    "attendance_chat_create_space": "학급 단톡방을 만들지 못했어요. 출결 기능 안내와 @goedu.kr Google 로그인을 확인한 뒤 다시 시도해 주세요.",
    "computer_status": "이 컴퓨터의 준비 상태를 확인하지 못했어요. Teacher Manager를 다시 시작해 주세요.",
    "google_status": "Google 연결 상태를 확인하지 못했어요. 현재 Windows 계정의 설정과 인터넷 연결을 확인해 주세요.",
    "list_calendars": "캘린더 목록을 가져오지 못했어요. 현재 Windows 계정의 @goedu.kr Google 로그인을 확인해 주세요.",
    "list_tasklists": "할 일 목록을 가져오지 못했어요. 현재 Windows 계정의 @goedu.kr Google 로그인을 확인해 주세요.",
    "gws_login_start": "Google 로그인 준비 파일을 확인하지 못했어요. 현재 Windows 계정의 설정을 점검한 뒤 다시 시도해 주세요.",
    "gws_login_status": "Google 로그인 상태를 확인하지 못했어요. 설정에서 다시 점검해 주세요.",
    "gws_logout": "Google 로그아웃을 마치지 못했어요. 현재 Windows 계정의 Google 도구 상태를 점검해 주세요.",
    "ensure_calendar_named": "캘린더를 만들지 못했어요. 이름과 Google 로그인을 확인한 뒤 다시 시도해 주세요.",
    "ensure_tasklist_named": "할 일 목록을 만들지 못했어요. 이름과 Google 로그인을 확인한 뒤 다시 시도해 주세요.",
    "open_logs": "기록 폴더를 열지 못했어요. Teacher Manager를 다시 시작한 뒤 다시 시도해 주세요.",
    "open_url": "안전한 https 주소만 열 수 있어요. 주소를 다시 확인해 주세요.",
}


class ScreenSafeError(RuntimeError):
    """A fixed Korean sentence intentionally prepared for the screen."""


def _ok(data):
    return {"ok": True, "data": data}


def _fail(error, operation: str = ""):
    message = _SCREEN_FAILURES.get(str(operation or ""), _DEFAULT_SCREEN_FAILURE)
    reply = {"ok": False, "error": message}
    if isinstance(error, external_url.ExternalUrlOpenError):
        reply["error"] = str(error)
        reply["code"] = external_url.NO_EXTERNAL_BROWSER
    elif isinstance(
        error,
        (ScreenSafeError, gws_env.GwsAccountStorageError, engine.AttendanceRemoteWorkBusyError),
    ):
        reply["error"] = str(error)
    else:
        try:
            from dashboard import central_chat

            if isinstance(error, central_chat.CentralChatError):
                reply["error"] = central_chat._safe_central_error_detail(error)
        except ImportError:
            pass
    return reply


def guarded(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return _ok(method(self, *args, **kwargs))
        except Exception as error:  # noqa: BLE001 - JS에는 한국어 한 문장만 보낸다
            return _fail(error, method.__name__)

    return wrapper


@dataclass
class BridgeDeps:
    """전 필드 주입 가능. None이면 engine/표준 기본 구현을 쓴다."""

    run_command: object = None
    gemini_transport: object = None
    hotkey_register: object = None
    hotkey_unregister: object = None
    hotkey_modifier_probe: object = None
    home_check_deps: object = None
    apply_deps: object = None
    attendance_deps: object = None
    helper_restart: object = None
    helper_stop: object = None
    helper_hotkey_pause: object = None
    helper_hotkey_resume: object = None
    autostart_checker: object = None
    autostart_enable: object = None
    autostart_disable: object = None
    url_opener: object = None
    edge_exe_url_opener: object = None
    edge_url_opener: object = None
    external_url_platform: object = None
    https_handler_available: object = None
    edge_protocol_available: object = None
    dir_opener: object = None
    popen_factory: object = None
    folder_picker: object = None
    environ: object = None
    gws_config_dir: object = None
    bundled_oauth_client_path: object = None
    node_local_app_data: object = None
    node_opener: object = None
    node_run_command: object = None
    gws_update_checker: object = None
    gws_update_installer: object = None
    gws_runtime_resolver: object = None
    gws_component_root: object = None
    attendance_script_updater: object = None
    attendance_script_runner: object = None
    attendance_remote_work_timeout_seconds: object = None


class Api:
    def __init__(self, config_dir, deps: BridgeDeps | None = None):
        self._config_dir = Path(config_dir)
        self._deps = deps or BridgeDeps()
        self._login = engine.LoginSession()
        self._gws_update_offer = None
        self._gws_update_offer_key = ""
        self._gws_update_last_status = None
        self._gws_update_install_lock = threading.Lock()
        self._attendance_script_update_lock = threading.Lock()
        self._attendance_prepare_lock = threading.Lock()
        self._attendance_prepare_thread = None
        self._attendance_prepare_result = None
        # 완료 확인 폴링용 gws 경로 캐시 — 3초 폴마다 resolve_gws(동봉본 SHA-256 검증
        # + 판 확인 실행)를 통째로 다시 돌리지 않는다(검토 C7). 승인된 갱신을 설치하면
        # 실행 파일이 바뀔 수 있어 install_gws_update 성공 시 비운다.
        self._attendance_gws_cache = None

    def _open_external_url(self, url) -> dict:
        return external_url.open_external_url(
            url,
            default_opener=self._deps.url_opener,
            edge_exe_opener=self._deps.edge_exe_url_opener,
            edge_opener=self._deps.edge_url_opener,
            platform=self._deps.external_url_platform,
            https_handler_available=self._deps.https_handler_available,
            edge_protocol_available=self._deps.edge_protocol_available,
        )

    # ----- setup-state -----

    def _state_path(self) -> Path:
        return self._config_dir / SETUP_STATE_NAME

    def _fresh_state(self) -> dict:
        return json.loads(json.dumps(_FRESH_STATE))

    def _write_state(self, state: dict) -> None:
        self._config_dir.mkdir(parents=True, exist_ok=True)
        component_lock.atomic_write_text_unique(
            self._state_path(),
            json.dumps(state, ensure_ascii=False, indent=1) + "\n",
        )

    def _migrate_state(self, state: dict) -> tuple[dict, bool]:
        """예전 7단계 기록을 9단계로 옮기되 작성 중인 값은 그대로 둔다."""

        merged = self._fresh_state()
        merged.update(state)
        try:
            saved_version = int(state.get("version") or 1)
        except (TypeError, ValueError):
            saved_version = 1

        def bounded_step(value, default=1) -> int:
            try:
                number = int(value)
            except (TypeError, ValueError):
                number = default
            return max(1, min(SETUP_LAST_STEP, number))

        if saved_version >= SETUP_STATE_VERSION:
            if bool(merged.get("completed")):
                # 완료 기록은 중간 단계 숫자가 깨져도 기존 사용자를 처음 화면으로
                # 되돌리지 않는다. 초안과 미래 보조값은 merged에 그대로 남긴다.
                merged["step"] = SETUP_LAST_STEP
                merged["max_step"] = SETUP_LAST_STEP
                return merged, merged != state
            merged["step"] = bounded_step(merged.get("step"), 1)
            merged["max_step"] = max(
                merged["step"],
                bounded_step(merged.get("max_step"), merged["step"]),
            )
            return merged, merged != state

        completed = bool(merged.get("completed"))

        def move(value) -> int:
            try:
                old = int(value or 1)
            except (TypeError, ValueError):
                old = 1
            if completed and old >= 7:
                return SETUP_LAST_STEP
            if old >= 7:
                # 예전 마지막 화면까지 왔지만 완료하지 않은 사람은 새로 생긴
                # 학생 계정·학급 단체톡방 준비 안내(8단계)를 먼저 본다.
                return SETUP_LAST_STEP - 1
            return _V1_STEP_TO_V2.get(max(1, old), 1)

        merged["version"] = SETUP_STATE_VERSION
        merged["step"] = move(merged.get("step"))
        merged["max_step"] = max(
            merged["step"], move(merged.get("max_step") or merged["step"])
        )
        return merged, True

    def _load_state(self) -> dict:
        path = self._state_path()
        if path.exists():
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(state, dict):
                    merged, changed = self._migrate_state(state)
                    if changed:
                        self._write_state(merged)
                    return merged
            except (TypeError, ValueError):
                pass
            if paths.profile_path(self._config_dir).exists():
                # 읽을 수 없는 진행 파일을 빈 값으로 덮지 않는다. 실제 사용자 정보가
                # 있으면 화면만 완료 상태로 열고, 망가진 원본은 그대로 남겨 복구할 수 있게 한다.
                state = self._fresh_state()
                state["completed"] = True
                state["step"] = SETUP_LAST_STEP
                state["max_step"] = SETUP_LAST_STEP
                return state
            return self._fresh_state()
        if paths.profile_path(self._config_dir).exists():
            # 기존 사용자(웹 UI 이전 설치): 마법사로 끌고 가지 않는다.
            state = self._fresh_state()
            state["completed"] = True
            state["step"] = SETUP_LAST_STEP
            state["max_step"] = SETUP_LAST_STEP
            self._write_state(state)
            return state
        return self._fresh_state()

    @guarded
    def get_app_info(self):
        state = self._load_state()
        return {
            "version": version.APP_VERSION,
            "branding": dict(version.BRANDING),
            "mode": "home" if state["completed"] else "wizard",
            "step": state["step"],
            "max_step": int(state.get("max_step") or state["step"]),
            "draft": state["draft"],
            "features": {
                "ai_skill_install_enabled": engine.ai_skill_install_enabled(),
            },
        }

    @guarded
    def get_update_info(self):
        return engine.check_update(version.APP_VERSION)

    @guarded
    def update_offer(self, fetch=None, today=None):
        """켤 때 한 번 묻기 위한 정보. 물을 일이 없으면 ask=False.

        '버전 및 제작 정보' 화면의 배너·상태도 이 결과 하나로 채운다 — 부팅할 때
        get_update_info를 따로 또 부르면 같은 배포 정보를 인터넷에서 두 번 받아 오게
        된다. status/available/latest/notes/url/sha256은 오늘 이미 취소했든 아니든
        실제 확인 결과를 그대로 담고, ask만 "지금 확인창을 띄워도 되는지"를 가른다.
        """
        import datetime as _dt

        day = str(today or _dt.date.today().isoformat())
        info = engine.check_update(version.APP_VERSION, fetch=fetch)
        # 확인 자체가 실패(인터넷 끊김 등)했으면 '오늘 확인함'으로 남기지 않는다 —
        # 남기면 와이파이가 돌아온 뒤에도 그날 하루는 다시 확인할 길이 없어진다.
        if info.get("status") != "failed":
            engine.remember_update_checked(self._config_dir, day)
        base = {
            "status": info.get("status", "failed"),
            "available": bool(info.get("available")),
            "latest": str(info.get("latest", "") or ""),
            "notes": str(info.get("notes", "") or ""),
            "url": info.get("url", "") or "",
            "sha256": info.get("sha256", "") or "",
            "reason": info.get("reason", "") or "",
        }
        if not info.get("available"):
            return {**base, "ask": False}
        if not engine.should_ask_update(self._config_dir, base["latest"], day):
            return {**base, "ask": False}
        return {**base, "ask": True}

    @guarded
    def decline_update(self, latest="", today=""):
        """오늘은 그만 물어 달라는 뜻. latest가 비어 있으면 기록에 아무것도 남기지 않는다 —
        빈 문자열이 declined_version으로 저장되면 다음 확인 때 헷갈릴 수 있다.

        today는 update_offer와 같은 뜻이고 같은 자리에서 온다. 화면은 안 넘기고
        오늘 날짜를 그대로 쓰지만, 시험이 update_offer에만 날짜를 넣고 여기엔 못 넣으면
        두 날짜가 어긋나서 그날그날 결과가 달라진다.
        """
        import datetime as _dt

        latest = str(latest or "").strip()
        if latest:
            when = str(today or "").strip() or _dt.date.today().isoformat()
            engine.remember_update_declined(self._config_dir, latest, when)
        return {"ok": True}

    @guarded
    def start_update(self, url="", latest="", sha256=""):
        # 화면이 이미 아는 url을 넘겨받아 재조회 없이 바로 받는다(통신 깜빡임 오안내 방지).
        return engine.start_update(
            version.APP_VERSION,
            url=url or "",
            latest=latest or "",
            sha256=sha256 or "",
            stop_before_launch=self._deps.helper_stop or engine.stop_helper,
            config_dir=self._config_dir,
        )

    @guarded
    def quit_app(self):
        # 설치 창이 뜬 뒤 프로그램이 스스로 닫힌다 — 응답을 먼저 보내려고 잠깐 늦춘다.
        import threading

        def _close():
            try:
                import webview

                for window in list(webview.windows):
                    window.destroy()
            except Exception:  # noqa: BLE001 - 닫기 실패는 설치기가 대신 닫아준다
                pass

        threading.Timer(0.4, _close).start()
        return True

    @guarded
    def ai_tools_status(self):
        return engine.ai_tools_status()

    @guarded
    def ai_node_status(self):
        kwargs = {}
        if self._deps.node_local_app_data is not None:
            kwargs["local_app_data"] = self._deps.node_local_app_data
        if self._deps.node_run_command is not None:
            kwargs["run_command"] = self._deps.node_run_command
        return engine.ai_node_status(**kwargs)

    @guarded
    def ai_node_prepare(self):
        kwargs = {}
        if self._deps.node_local_app_data is not None:
            kwargs["local_app_data"] = self._deps.node_local_app_data
        if self._deps.node_opener is not None:
            kwargs["opener"] = self._deps.node_opener
        if self._deps.node_run_command is not None:
            kwargs["run_command"] = self._deps.node_run_command
        return engine.ai_node_prepare(**kwargs)

    @guarded
    def ai_skills_install(self, keys, permission_ack=False):
        kwargs = {}
        if self._deps.node_run_command is not None:
            kwargs["run_command"] = self._deps.node_run_command
        return engine.ai_skills_install(keys, permission_ack=permission_ack, **kwargs)

    @guarded
    def save_setup_state(self, state):
        if not isinstance(state, dict):
            raise ValueError("설정 진행 상태 모양이 올바르지 않아요")
        previous = self._load_state()
        combined = dict(previous)
        combined.update(state)
        # 이 문은 작성 중인 칸만 저장한다. 화면이 completed=true를 보내더라도
        # 완료 권한을 주지 않으며, 이미 끝낸 사용자는 늦은 저장으로 되돌리지 않는다.
        combined["completed"] = bool(previous.get("completed"))
        merged, _changed = self._migrate_state(combined)
        self._write_state(merged)
        return True

    @guarded
    def finish_setup(self):
        self._resolve_goedu_gws_or_fail()
        state = self._load_state()
        state["version"] = SETUP_STATE_VERSION
        state["completed"] = True
        state["step"] = SETUP_LAST_STEP
        state["max_step"] = SETUP_LAST_STEP
        state["draft"] = self._fresh_state()["draft"]
        self._write_state(state)  # draft(키 포함)를 비운 채 기록
        return True

    @guarded
    def restart_setup(self):
        self._write_state(self._fresh_state())
        return True

    # ----- 조회·검증 -----

    def _run(self):
        if self._deps.run_command is not None:
            return self._deps.run_command
        base = self._gws_base_environ()
        environment = gws_env.gws_environ(
            base,
            gws_config_dir=self._gws_config_dir(base),
        )

        def run(args):
            return process_win.run_captured(args, env=environment)

        return run

    def _attendance_remote_run(self):
        """출결 Google 명령은 자식 작업까지 실제 제한 시간이 있는 길로 실행한다."""

        if self._deps.run_command is not None:
            return self._deps.run_command
        base = self._gws_base_environ()
        environment = gws_env.gws_environ(
            base,
            gws_config_dir=self._gws_config_dir(base),
        )

        def run(args):
            return engine.attendance_remote_command(args, environment=environment)

        return run

    def _gws_base_environ(self) -> dict:
        """GWS가 물려받을 Windows 환경값의 읽기 전용 사본."""

        return dict(os.environ if self._deps.environ is None else self._deps.environ)

    def _gws_config_dir(self, base: dict | None = None) -> Path:
        """현재 Windows 계정만 쓰는 GWS 로그인 폴더."""

        if self._deps.gws_config_dir is not None:
            return Path(self._deps.gws_config_dir)
        return gws_env.default_gws_config_dir(
            self._gws_base_environ() if base is None else base
        )

    def _unsafe_gws_account_storage(self) -> tuple[str, ...]:
        return gws_env.unsafe_account_storage_overrides(self._gws_base_environ())

    def _require_safe_gws_account_storage(self) -> None:
        if self._unsafe_gws_account_storage():
            raise gws_env.GwsAccountStorageError(
                gws_env.ACCOUNT_STORAGE_ERROR_MESSAGE
            )

    @guarded
    def home_checks(self):
        self._require_safe_gws_account_storage()
        results = engine.home_checks(self._config_dir, deps=self._deps.home_check_deps)
        return [asdict(r) for r in results]

    @guarded
    def attendance_status(self):
        self._require_safe_gws_account_storage()
        status_value = engine.read_attendance_status(self._config_dir, self._run())
        if status_value.state in _ATTENDANCE_AUTH_BLOCKED_STATES:
            return asdict(status_value)
        # 허용 계정임을 확인한 뒤에만 옛 기록에 학년도 도장을 채운다.
        if engine.backfill_attendance_record_stamp(self._config_dir):
            status_value = engine.read_attendance_status(self._config_dir, self._run())
        status = asdict(status_value)
        # 다음에 켤 때 "확인하는 중…" 없이 이 상태부터 보여준다.
        engine.save_attendance_status_cache(self._config_dir, status)
        return status

    @guarded
    def attendance_status_cached(self):
        """켠 직후 화면이 먼저 집는 저장본 — 없으면 None(화면이 확인 문구를 보인다)."""
        return engine.load_attendance_status_cache(self._config_dir)

    @guarded
    def ensure_attendance(self):
        self._require_safe_gws_account_storage()
        deps = self._deps.attendance_deps or engine.AttendanceDeps(
            run_command=self._attendance_remote_run()
        )
        status = asdict(engine.ensure_attendance(self._config_dir, deps=deps))
        # 다음에 켤 때 저장본부터 보여주는 화면이 방금 만든 결과를 곧바로 보게 한다.
        if status.get("state") not in _ATTENDANCE_AUTH_BLOCKED_STATES:
            engine.save_attendance_status_cache(self._config_dir, status)
        return status

    @guarded
    def start_new_attendance(self):
        self._require_safe_gws_account_storage()
        deps = self._deps.attendance_deps or engine.AttendanceDeps(
            run_command=self._attendance_remote_run()
        )
        status = asdict(engine.start_new_attendance(self._config_dir, deps=deps))
        # 다음에 켤 때 저장본부터 보여주는 화면이 방금 만든 결과를 곧바로 보게 한다.
        if status.get("state") not in _ATTENDANCE_AUTH_BLOCKED_STATES:
            engine.save_attendance_status_cache(self._config_dir, status)
        return status

    @guarded
    def consolidate_attendance(self):
        self._require_safe_gws_account_storage()
        deps = self._deps.attendance_deps or engine.AttendanceDeps(
            run_command=self._attendance_remote_run()
        )
        status = asdict(engine.consolidate_attendance(self._config_dir, deps=deps))
        if status.get("state") not in _ATTENDANCE_AUTH_BLOCKED_STATES:
            engine.save_attendance_status_cache(self._config_dir, status)
        return status

    @guarded
    def attendance_prepare_start(self, profile, grid, bridge_updates):
        """메신저 탭 [다음] — 입력 저장 후 출결 준비를 뒤에서 시작한다. 여러 번 불려도 안전."""
        self._require_safe_gws_account_storage()
        # pywebview는 js_api 호출마다 새 스레드를 만든다 — [다음] 더블클릭이면
        # is_alive 확인과 start() 사이(저장은 수백 ms~수 초)에 둘 다 지나가
        # 준비 스레드가 2개 생기고, 진 쪽이 잠금 대기로 죽은 뒤 그 죽은 스레드가
        # _attendance_prepare_thread에 남는다. 확인→저장→시작 전체를 직렬화한다.
        with self._attendance_prepare_lock:
            thread = self._attendance_prepare_thread
            if thread is not None and thread.is_alive():
                return {"started": True, "reason": "이미 준비하는 중이에요"}
            save_deps = self._deps.apply_deps or engine.ApplyDeps(
                run_command=self._attendance_remote_run()
            )
            try:
                ok, reason = engine.save_wizard_inputs(
                    self._config_dir, dict(profile), list(grid), dict(bridge_updates),
                    deps=save_deps,
                )
            except RuntimeError:
                # 로그인 문제(require_goedu_gws_session)는 guarded의 오류 응답이 아니라
                # started=False + 사연으로 화면에 가야 배너를 띄울 수 있다.
                return {
                    "started": False,
                    "reason": (
                        "이 컴퓨터 설정을 저장하지 못했어요. 현재 Windows 계정의 "
                        "@goedu.kr Google 로그인을 확인한 뒤 다시 시도해 주세요."
                    ),
                }
            if not ok:
                return {"started": False, "reason": reason}
            att_deps = self._deps.attendance_deps or engine.AttendanceDeps(
                run_command=self._attendance_remote_run()
            )

            def _prepare():
                # 예외로 조용히 죽으면 화면은 running=False + 사유 0글자만 본다.
                # 성공이든 실패든 결과를 남겨 attendance_prepare_status가 보여준다.
                try:
                    status = asdict(
                        engine.ensure_attendance(self._config_dir, deps=att_deps)
                    )
                    # ensure_attendance와 같은 규칙: 허용 계정으로 만든 결과만 저장본에 남긴다.
                    if status.get("state") not in _ATTENDANCE_AUTH_BLOCKED_STATES:
                        engine.save_attendance_status_cache(self._config_dir, status)
                except Exception as error:  # noqa: BLE001 - 사람이 읽을 문장으로 바꾼다
                    failed_service, detail = engine.friendly_attendance_error(error)
                    status = asdict(engine.AttendanceStatus(
                        state="failed", failed_service=failed_service,
                        detail=detail[:engine.ATTENDANCE_DETAIL_LIMIT],
                    ))
                self._attendance_prepare_result = status

            self._attendance_prepare_result = None
            thread = threading.Thread(
                target=_prepare, name="attendance-prepare", daemon=True
            )
            self._attendance_prepare_thread = thread
            thread.start()
            return {"started": True, "reason": ""}

    @guarded
    def attendance_prepare_status(self):
        """뒤에서 도는 출결 준비의 진행 여부와 현재 출결 상태.

        도는 동안에는 gws를 부르지 않는다 — 3초 폴마다 gws 3회 실행과 동봉본
        SHA-256 해시가 쌓이기 때문. 로컬 진행 기록(attendance-setup-status)만 읽고,
        끝난 뒤에는 준비 스레드가 남긴 결과를 그대로 보여준다.
        """
        thread = self._attendance_prepare_thread
        if thread is not None and thread.is_alive():
            setup = engine._read_setup_status(self._config_dir)
            progress = setup.get("progress")
            return {"running": True, "status": {
                "state": "installing",
                "progress": dict(progress) if isinstance(progress, dict) else {},
            }}
        result = self._attendance_prepare_result
        if result is not None:
            return {"running": False, "status": dict(result)}
        # 이 창에서 준비를 돌린 적이 없을 때(재시작 등)만 실제 상태를 읽는다.
        # 폴마다 부를 수 있는 네트워크 명령이므로 제한 시간 없는 self._run() 대신
        # 자식 작업까지 제한 시간이 있는 감독 실행 경로를 쓴다(검토 C7).
        self._require_safe_gws_account_storage()
        status_value = engine.read_attendance_status(
            self._config_dir, self._attendance_remote_run()
        )
        return {"running": False, "status": asdict(status_value)}

    @guarded
    def attendance_first_setup_status(self):
        """시트의 [처음 설정 한 번에 끝내기] 완료 표시 — 마법사 출결 탭이 폴링한다."""
        self._require_safe_gws_account_storage()
        # 폴마다 부르는 네트워크 명령이므로 제한 시간 없는 self._run() 대신
        # 자식 작업까지 제한 시간이 있는 감독 실행 경로를 쓴다.
        run = self._attendance_remote_run()
        if self._attendance_gws_cache is None:
            # gws 경로 찾기(동봉본 검증 포함)는 폴마다가 아니라 한 번만(검토 C7).
            self._attendance_gws_cache = str(engine.resolve_gws(run))
        return engine.read_first_time_setup_done(
            self._config_dir, run, self._attendance_gws_cache
        )

    def _attendance_script_update(
        self,
        *,
        apply: bool,
        record_snapshot=None,
        resolved=None,
    ):
        """기존 출결 Sheet의 Apps Script만 확인하거나 명시적으로 갱신한다."""

        record_path = paths.attendance_install_record_path(self._config_dir)
        if record_snapshot is None and not record_path.exists():
            return {
                "state": "not-ready",
                "verified": False,
                "detail": "출결 준비를 먼저 마쳐 주세요.",
            }
        from attendance_install_record import (
            mark_attendance_script_current,
            read_attendance_install_snapshot,
        )

        run, gws = (
            resolved
            if resolved is not None
            else self._resolve_attendance_goedu_gws_or_fail()
        )
        if record_snapshot is None:
            record_snapshot = read_attendance_install_snapshot(record_path)
        record = record_snapshot.record
        updater = self._deps.attendance_script_updater
        if updater is None:
            from attendance_script_update import inspect_or_update_attendance_script

            updater = inspect_or_update_attendance_script
        script_runner = self._deps.attendance_script_runner
        if script_runner is None:
            # Apps Script의 +push는 임시 폴더 위치를 꼭 넘겨야 한다. 자식 작업까지
            # 제한 시간 안에 끝내는 출결 전용 실행기를 사용한다.
            base = self._gws_base_environ()
            environment = gws_env.gws_environ(
                base,
                gws_config_dir=self._gws_config_dir(base),
            )

            def script_runner(args, cwd):
                try:
                    return engine.attendance_remote_runner(
                        args, cwd, environment=environment
                    )
                except subprocess.CalledProcessError as error:
                    output = error.stderr or error.output or ""
                    try:
                        process_win.write_process_log(
                            paths.logs_dir(self._config_dir),
                            list(error.cmd)
                            if isinstance(error.cmd, (list, tuple))
                            else list(args),
                            int(error.returncode),
                            str(output),
                        )
                    except OSError:
                        pass
                    raise
        assets_dir = bundle_paths.bundle_root() / "assets"
        result = updater(
            record,
            assets_dir=assets_dir,
            apply=apply,
            runner=script_runner,
            gws_executable=gws,
        )
        payload = dict(result) if isinstance(result, dict) else asdict(result)
        if (
            apply
            and payload.get("verified") is True
            and payload.get("state") in {"current", "updated"}
        ):
            # 결과 글자만 믿지 않는다. 현재 프로그램에 실제로 든 코드 지문, 원격
            # 확인 결과, 처음 읽은 세 연결 ID가 모두 같을 때만 준비 증명을 남긴다.
            expected_sha256 = engine.current_attendance_script_bundle_sha256()
            same_ids = all(
                payload.get(key) == record.get(key)
                for key in ("spreadsheet_id", "script_id", "deployment_id")
            )
            if (
                not same_ids
                or payload.get("target_bundle_sha256") != expected_sha256
                or payload.get("current_bundle_sha256") != expected_sha256
            ):
                raise ScreenSafeError(
                    "확인한 출결 기능이 지금 프로그램에 든 파일과 달라서 준비 완료로 바꾸지 않았어요."
                )
            # 업데이트를 시작할 때 읽은 기록이 지금도 정확히 같을 때만 증명을 쓰고
            # 옛판 표식을 함께 지운다. 다른 창의 새 기록은 건드리지 않는다.
            mark_attendance_script_current(
                record_path, record_snapshot, expected_sha256
            )
        return payload

    def _require_current_attendance_script(self, record=None) -> None:
        """예전 공식 출결 기능을 되찾은 직후에는 Chat 쓰기를 먼저 막는다."""

        record_path = paths.attendance_install_record_path(self._config_dir)
        if record is None and not record_path.exists():
            return
        from attendance_install_record import (
            attendance_script_is_attested,
            load_attendance_install_record,
        )

        if record is None:
            record = load_attendance_install_record(record_path)
        if (
            record.get("script_update_required") is True
            or not attendance_script_is_attested(
                record, engine.current_attendance_script_bundle_sha256()
            )
        ):
            raise ScreenSafeError(
                "기존 자료는 그대로 두었지만 출결 기능 확인 또는 업데이트가 먼저 필요해요. "
                "출결 탭 위쪽의 한 줄 안내에 보이는 버튼을 눌러 주세요."
            )

    def _require_current_remote_attendance_script(
        self,
        record_snapshot,
        resolved,
    ) -> None:
        """Chat 직전에 HEAD·고정 배포판·현재 프로그램 파일을 다시 읽어 맞춘다."""

        payload = self._attendance_script_update(
            apply=False,
            record_snapshot=record_snapshot,
            resolved=resolved,
        )
        record = record_snapshot.record
        expected_sha256 = engine.current_attendance_script_bundle_sha256()
        same_ids = all(
            payload.get(key) == record.get(key)
            for key in ("spreadsheet_id", "script_id", "deployment_id")
        )
        if not (
            payload.get("state") == "current"
            and payload.get("verified") is True
            and same_ids
            and payload.get("current_bundle_sha256") == expected_sha256
            and payload.get("target_bundle_sha256") == expected_sha256
        ):
            raise ScreenSafeError(
                "현재 Google의 출결 기능을 안전하게 다시 확인하지 못해 "
                "Chat 작업을 시작하지 않았어요. "
                "출결 탭 위쪽의 한 줄 안내에 보이는 버튼을 눌러 주세요."
            )

    def _run_attendance_chat_action(self, action):
        """긴 작업은 별도 잠금으로 직렬화하고 설치 기록 잠금은 짧게만 쓴다."""

        from attendance_install_record import (
            attendance_install_record_lock,
            read_attendance_install_snapshot,
        )

        record_path = paths.attendance_install_record_path(self._config_dir)
        timeout = self._deps.attendance_remote_work_timeout_seconds
        lock_options = {}
        if timeout is not None:
            lock_options["timeout_seconds"] = float(timeout)
        # 출결 새 준비·연결 교체·Chat 작업은 같은 원격 작업 잠금을 쓴다. 반면
        # 설치 기록 파일은 처음 snapshot과 마지막 대조 순간에만 잠근다.
        with engine.attendance_remote_work_lock(self._config_dir, **lock_options):
            resolved = (
                self._resolve_attendance_goedu_gws_or_fail()
                if record_path.exists()
                else None
            )
            with attendance_install_record_lock(record_path):
                record_snapshot = (
                    read_attendance_install_snapshot(record_path)
                    if record_path.exists()
                    else None
                )

            if record_snapshot is not None:
                if resolved is None:
                    # 파일이 없다고 본 직후 다른 과정이 새 연결을 놓은 드문 경우다.
                    # 그 새 연결을 이 버튼이 우연히 이어 쓰지 않고 다시 눌러 확인시킨다.
                    raise ScreenSafeError(
                        "출결 연결이 방금 바뀌었어요. 현재 출결 상태를 다시 확인해 주세요."
                    )
                run, gws = resolved
                self._require_current_attendance_script(record_snapshot.record)
                self._require_current_remote_attendance_script(
                    record_snapshot, (run, gws)
                )
            else:
                run, gws = self._attendance_remote_run(), None

            result = action(
                run,
                gws,
                None if record_snapshot is None else record_snapshot.record,
            )

            if record_snapshot is not None:
                with attendance_install_record_lock(record_path):
                    changed = not record_path.exists()
                    if not changed:
                        current = read_attendance_install_snapshot(record_path)
                        changed = (
                            current.raw != record_snapshot.raw
                            or current.sha256 != record_snapshot.sha256
                        )
                if changed:
                    raise ScreenSafeError(
                        "Chat 작업 중 다른 창에서 출결 연결이 바뀌었어요. "
                        "새 연결에는 결과를 쓰지 않았습니다. 현재 출결 상태를 다시 확인해 주세요."
                    )
            return result

    @guarded
    def attendance_script_update_status(self):
        # 이 길은 Apps Script와 배포 상태만 읽는다. Sheet나 설치 기록은 쓰지 않는다.
        return self._attendance_script_update(apply=False)

    @guarded
    def attendance_script_update_apply(self):
        # 화면의 별도 확인창을 통과해 여기로 왔을 때만 쓰기 동작을 허용한다.
        if not self._attendance_script_update_lock.acquire(blocking=False):
            raise ScreenSafeError("출결 기능을 이미 업데이트하고 있어요. 잠시만 기다려 주세요.")
        try:
            # 다른 대시보드 창과 새 학년도 출결 준비도 같은 기록과 Google Script를
            # 만질 수 있다. 공용 잠금 안에서 계정부터 다시 확인하고 한 번씩 실행한다.
            with engine.attendance_setup_lock(self._config_dir):
                return self._attendance_script_update(apply=True)
        finally:
            self._attendance_script_update_lock.release()

    @guarded
    def attendance_chat_status(self):
        from dashboard import central_chat
        # 상태 조회는 화면에 보여 줄 값만 읽고, Google 시트는 바꾸지 않는다.
        if paths.attendance_install_record_path(self._config_dir).exists():
            run, gws = self._resolve_attendance_goedu_gws_or_fail()
        else:
            run, gws = self._run(), None
        return central_chat.chat_status(
            self._config_dir,
            run,
            gws_executable=gws,
        )

    @guarded
    def attendance_chat_connect(self):
        from dashboard import central_chat
        auth_url = self._run_attendance_chat_action(
            lambda run, gws, record: central_chat.start_auth(
                self._config_dir,
                run,
                gws_executable=gws,
                attendance_record=record,
            )
        )
        return self._open_external_url(auth_url)

    @guarded
    def attendance_chat_spaces(self):
        from dashboard import central_chat
        return self._run_attendance_chat_action(
            lambda run, gws, record: central_chat.list_spaces(
                self._config_dir,
                run,
                gws_executable=gws,
                attendance_record=record,
            )
        )

    @guarded
    def attendance_chat_set_space(self, space_name, display_name):
        from dashboard import central_chat
        return self._run_attendance_chat_action(
            lambda run, gws, record: central_chat.set_class_space(
                self._config_dir,
                str(space_name),
                str(display_name),
                run,
                gws_executable=gws,
                attendance_record=record,
            )
        )

    @guarded
    def attendance_chat_create_space(self, display_name=""):
        from dashboard import central_chat
        return self._run_attendance_chat_action(
            lambda run, gws, record: central_chat.create_class_space(
                self._config_dir,
                str(display_name),
                run,
                gws_executable=gws,
                attendance_record=record,
            )
        )

    @guarded
    def computer_status(self):
        return engine.computer_readiness(self._run())

    @guarded
    def google_status(self):
        base, config_dir, _bundled, selection = self._oauth_context()
        credential_override = bool(
            str(base.get("GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE") or "").strip()
        )
        if gws_env.unsafe_account_storage_overrides(base):
            # 파일 위치를 고치기 전에는 gws --version이나 auth status조차 실행하지
            # 않는다. 공용/다른 계정의 토큰을 우연히 읽는 일을 먼저 막는다.
            return {
                "gws_runtime_ready": False,
                "oauth_client_ready": bool(selection.ready),
                "oauth_client_conflict": selection.source == "conflict",
                "credential_override_present": credential_override,
                "account_storage_override_unsafe": True,
                "login_state": "error",
                "error_code": gws_env.ACCOUNT_STORAGE_ERROR_CODE,
                "logged_in": False,
                "account_allowed": False,
                "user": "",
            }

        run = self._run()
        runtime_error = ""
        try:
            gws = engine.resolve_gws(run)
        except engine.tool_runtime.GwsRuntimeError as error:
            gws = ""
            runtime_error = error.code
        auth = (
            engine.gws_auth_status(run, gws)
            if gws
            else {
                "logged_in": False,
                "account_allowed": False,
                "user": "",
                "login_state": "not_checked",
                "error_code": "",
            }
        )
        error_code = runtime_error or selection.error_code or auth.get("error_code", "")
        return {
            "gws_runtime_ready": bool(gws),
            "oauth_client_ready": bool(selection.ready),
            "oauth_client_conflict": selection.source == "conflict",
            "credential_override_present": credential_override,
            "account_storage_override_unsafe": False,
            "login_state": auth.get("login_state", "not_checked"),
            "error_code": error_code,
            "logged_in": bool(auth["logged_in"]),
            "account_allowed": bool(auth.get("account_allowed")),
            "user": auth["user"],
        }

    @guarded
    def gws_update_status(self):
        if self._unsafe_gws_account_storage():
            self._gws_update_offer = None
            self._gws_update_offer_key = ""
            self._gws_update_last_status = {
                "success": False,
                "code": gws_env.ACCOUNT_STORAGE_ERROR_CODE,
                "detail": gws_env.ACCOUNT_STORAGE_ERROR_MESSAGE,
                "checked_on": "",
                "offer": None,
                "runtime_ready": False,
                "can_continue": False,
                "repair_required": True,
                "current_version": "",
                "current_source": "",
                "runtime_error_code": gws_env.ACCOUNT_STORAGE_ERROR_CODE,
            }
            return dict(self._gws_update_last_status)
        status, exact_offer = engine.read_gws_update_status(
            version.APP_VERSION,
            self._run(),
            component_root=self._deps.gws_component_root,
            checker=self._deps.gws_update_checker,
            resolver=self._deps.gws_runtime_resolver,
        )
        self._gws_update_offer = exact_offer
        self._gws_update_offer_key = self._screen_offer_key(status.get("offer"))
        self._gws_update_last_status = dict(status)
        return status

    @guarded
    def read_profile(self):
        return engine.read_profile_values(self._config_dir)

    @guarded
    def read_grid(self):
        return engine.read_timetable_grid(self._config_dir)

    @guarded
    def list_calendars(self):
        run, gws = self._resolve_gws_or_fail()
        return engine.list_calendars(run, gws)

    @guarded
    def list_tasklists(self):
        run, gws = self._resolve_gws_or_fail()
        return engine.list_tasklists(run, gws)

    @guarded
    def verify_gemini_key(self, key, model=None):
        saved = load_settings(paths.settings_path(self._config_dir))
        status, detail = engine.verify_gemini_key(
            key, model or saved.gemini_model, transport=self._deps.gemini_transport
        )
        return {"status": status, "detail": detail}

    @guarded
    def probe_hotkey(self, text):
        return {
            "status": engine.probe_hotkey(
                text,
                register=self._deps.hotkey_register,
                unregister=self._deps.hotkey_unregister,
                modifier_probe=self._deps.hotkey_modifier_probe,
            )
        }

    @guarded
    def recent_captures(self, limit=20):
        return capture_store.read_captures(paths.bridge_state_dir(self._config_dir), int(limit))

    @guarded
    def capture_history_page(self, page=1, page_size=10):
        return capture_store.read_capture_page(
            paths.bridge_state_dir(self._config_dir), int(page), int(page_size)
        )

    @guarded
    def capture_progress(self):
        return capture_store.read_progress(paths.bridge_state_dir(self._config_dir))

    # ----- 행동 -----

    def _success(self, ok, detail):
        return {"success": bool(ok), "detail": detail}

    @staticmethod
    def _screen_offer_key(offer) -> str:
        if not isinstance(offer, dict):
            return ""
        try:
            return json.dumps(
                offer,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            return ""

    def _gws_update_failure(self, code: str, detail: str) -> dict:
        previous = self._gws_update_last_status or {}
        return {
            "success": False,
            "code": code,
            "detail": detail,
            "runtime_ready": bool(previous.get("runtime_ready")),
            "can_continue": bool(previous.get("can_continue")),
            "repair_required": bool(previous.get("repair_required")),
            "current_version": str(previous.get("current_version") or ""),
            "current_source": str(previous.get("current_source") or ""),
            "runtime_error_code": str(previous.get("runtime_error_code") or ""),
        }

    @guarded
    def install_gws_update(self, offer):
        self._require_safe_gws_account_storage()
        if not self._gws_update_install_lock.acquire(blocking=False):
            return self._gws_update_failure(
                "COMPONENT_UPDATE_BUSY",
                "다른 Google 도구 갱신이 끝난 뒤 다시 눌러 주세요.",
            )
        try:
            shown_key = self._screen_offer_key(offer)
            if (
                self._gws_update_offer is None
                or not shown_key
                or shown_key != self._gws_update_offer_key
            ):
                return self._gws_update_failure(
                    "UPDATE_OFFER_CHANGED",
                    "처음 확인한 승인 정보와 달라 적용하지 않았어요. 다시 점검해 주세요.",
                )
            result = engine.apply_gws_update(
                self._gws_update_offer,
                self._run(),
                component_root=self._deps.gws_component_root,
                installer=self._deps.gws_update_installer,
                resolver=self._deps.gws_runtime_resolver,
            )
            self._gws_update_last_status = {
                **(self._gws_update_last_status or {}),
                **result,
            }
            if result.get("success"):
                self._gws_update_offer = None
                self._gws_update_offer_key = ""
                self._gws_update_last_status["offer"] = None
                # 실행 파일이 바뀌었을 수 있다 — 완료 확인 폴링의 gws 경로 캐시를 비운다.
                self._attendance_gws_cache = None
            return result
        finally:
            self._gws_update_install_lock.release()

    @guarded
    def gws_login_start(self):
        run, gws = self._resolve_gws_or_fail()
        # 로그인 시작 전에 gws가 남긴 반쪽 잔재를 먼저 치우고 다시 판정한다.
        self._discard_broken_gws_config_client(self._gws_config_dir())
        base, config_dir, _bundled, selection = self._oauth_context()
        if not selection.ready:
            if selection.error_code == "OAUTH_CLIENT_CONFLICT":
                raise ScreenSafeError(
                    "기존 Google 로그인 설정과 Teacher Manager의 로그인 설정이 서로 달라요. "
                    "로그인 설정을 확인해 주세요."
                )
            if selection.error_code == "OAUTH_CLIENT_MISSING":
                raise ScreenSafeError(
                    "이 확인용 Teacher Manager에는 Google 로그인 준비 파일이 없어요."
                )
            raise ScreenSafeError("Google 로그인 준비 파일을 안전하게 읽지 못했어요.")
        child_env = gws_env.login_environ(
            base,
            selection,
            gws_config_dir=config_dir,
        )
        self._login.start(
            engine.login_command(gws),
            popen=self._deps.popen_factory,
            env=child_env,
            auth_url_opener=self._open_external_url,
        )
        return self._login.snapshot()

    def _discard_broken_gws_config_client(self, config_dir) -> None:
        """gws가 로그인 실패로 남긴 반쪽짜리 client_secret.json만 치운다.
        올바른 기존 로그인 준비 파일과, gws auth login이 동봉 client를
        받아 적은 파일(빈 project_id)은 절대 건드리지 않는다."""
        path = Path(config_dir) / gws_env.UPSTREAM_CLIENT_FILE_NAME
        try:
            if not path.is_file() or gws_env.is_valid_desktop_client_file(path):
                return
            if gws_env.is_gws_login_echo_of_client(path, self._bundled_client_path()):
                return
            path.unlink()
        except OSError:
            pass

    def _bundled_client_path(self):
        """Release 동봉 OAuth client 파일 위치. 없으면 None."""
        if self._deps.bundled_oauth_client_path is False:
            return None
        if self._deps.bundled_oauth_client_path is not None:
            return Path(self._deps.bundled_oauth_client_path)
        candidate = bundle_paths.bundle_root() / "assets" / gws_env.CLIENT_FILE_NAME
        return candidate if candidate.is_file() else None

    def _oauth_context(self):
        """화면 상태와 로그인 시작이 똑같은 OAuth 준비 판정을 함께 쓴다."""
        base = self._gws_base_environ()
        config_dir = self._gws_config_dir(base)
        bundled = self._bundled_client_path()
        selection = gws_env.select_desktop_oauth_client(base, config_dir, bundled)
        return base, config_dir, bundled, selection

    @guarded
    def gws_login_status(self):
        return engine.annotate_login_snapshot(self._login.snapshot())

    @guarded
    def gws_login_cancel(self):
        return {"cancelled": self._login.cancel()}

    @guarded
    def gws_repair_oauth_client(self):
        """사용자가 정리를 누르면 gws가 남긴 깨진 준비 파일만 치운다.
        올바른 기존 준비 파일은 건드리지 않는다. 화면은 뒤이어 다시 점검한다."""
        self._discard_broken_gws_config_client(self._gws_config_dir())
        return {"cleared": True}

    def _resolve_gws_or_fail(self):
        self._require_safe_gws_account_storage()
        run = self._run()
        gws = engine.resolve_gws(run)
        if not gws:
            raise ScreenSafeError("Google 연결 도구가 아직 없어요. 설정에서 준비해 주세요.")
        return run, gws

    def _resolve_goedu_gws_or_fail(self):
        run, gws = self._resolve_gws_or_fail()
        engine.require_goedu_gws_session(run, gws)
        return run, gws

    def _resolve_attendance_goedu_gws_or_fail(self):
        """출결 자료는 처음 준비한 학교 계정으로만 읽거나 바꾼다."""

        self._require_safe_gws_account_storage()
        run = self._attendance_remote_run()
        gws = engine.resolve_gws(run)
        if not gws:
            raise ScreenSafeError("Google 연결 도구가 아직 없어요. 설정에서 준비해 주세요.")
        current = engine.require_goedu_gws_session(run, gws)
        saved = engine._read_setup_status(self._config_dir)
        owner = str(saved.get("account", "") or "").strip()
        if owner and owner.casefold() != current.casefold():
            raise ScreenSafeError(engine.ATTENDANCE_ACCOUNT_MESSAGE)
        return run, gws

    @guarded
    def gws_logout(self):
        run, gws = self._resolve_gws_or_fail()
        return self._success(*engine.gws_logout(run, gws))

    @guarded
    def ensure_calendar_named(self, name):
        name = str(name or "").strip()
        if not name:
            raise ValueError("캘린더 이름을 적어 주세요")
        run, gws = self._resolve_gws_or_fail()
        made_id = engine.ensure_calendar(run, gws, name)
        if not made_id:
            raise ScreenSafeError("캘린더를 만들지 못했어요. 이름을 확인하고 잠시 뒤 다시 시도해 주세요.")
        return {"id": made_id, "name": name}

    @guarded
    def ensure_tasklist_named(self, name):
        name = str(name or "").strip()
        if not name:
            raise ValueError("할일 목록 이름을 적어 주세요")
        run, gws = self._resolve_gws_or_fail()
        made_id = engine.ensure_tasklist(run, gws, name)
        if not made_id:
            raise ScreenSafeError("할 일 목록을 만들지 못했어요. 이름을 확인하고 잠시 뒤 다시 시도해 주세요.")
        return {"id": made_id, "name": name}

    @guarded
    def apply_all(self, profile, grid, bridge_updates):
        results = engine.apply_all(
            self._config_dir, dict(profile), list(grid), dict(bridge_updates), deps=self._deps.apply_deps
        )
        return [
            {"key": r.key, "label": r.label, "status": r.status, "detail": r.detail} for r in results
        ]

    @guarded
    def save_profile_grid(self, profile, grid, require_links=True):
        engine.write_profile_values(self._config_dir, dict(profile))
        engine.write_timetable_grid(self._config_dir, list(grid))
        parsed, detail = engine.run_parser(self._config_dir, require_links=bool(require_links))
        return {"parsed": parsed, "detail": detail}

    @guarded
    def get_messenger_settings(self):
        saved = load_settings(paths.settings_path(self._config_dir))
        checker = self._deps.autostart_checker or engine.autostart_enabled
        return {
            "gemini_api_key": saved.gemini_api_key,
            "gemini_model": saved.gemini_model,
            "hotkey": saved.hotkey,
            "autostart": bool(checker()),
            "brity_download_dir": saved.brity_download_dir,
        }

    @guarded
    def check_attachment_folder(self, path_text):
        return engine.attachment_folder_status(str(path_text or ""))

    @guarded
    def choose_attachment_folder(self, current_path):
        picker = self._deps.folder_picker or engine.choose_attachment_folder
        selected = picker(str(current_path or ""))
        return {"path": selected, "cancelled": not bool(selected)}

    @guarded
    def save_messenger(self, updates):
        return engine.save_messenger_settings(
            self._config_dir,
            dict(updates),
            register=self._deps.hotkey_register,
            unregister=self._deps.hotkey_unregister,
            modifier_probe=self._deps.hotkey_modifier_probe,
            restart=self._deps.helper_restart,
            autostart_checker=self._deps.autostart_checker,
            autostart_enable=self._deps.autostart_enable,
            autostart_disable=self._deps.autostart_disable,
        )

    @guarded
    def hotkey_recording_start(self):
        pause = self._deps.helper_hotkey_pause or engine.pause_helper_hotkey
        return bool(pause())

    @guarded
    def hotkey_recording_end(self):
        resume = self._deps.helper_hotkey_resume or engine.resume_helper_hotkey
        return bool(resume())

    @guarded
    def reset_attendance(self):
        return engine.reset_attendance_record(self._config_dir)

    @guarded
    def open_logs(self):
        logs = paths.settings_path(self._config_dir).parent / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        opener = self._deps.dir_opener or os.startfile  # noqa: S606 - Windows 전용 기본
        opener(str(logs))
        return True

    @guarded
    def open_url(self, url):
        return self._open_external_url(url)
