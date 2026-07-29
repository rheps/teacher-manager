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
import sys
import webbrowser
from dataclasses import asdict, dataclass
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brity_bridge import capture_store, paths
from brity_bridge.settings import load_settings
from dashboard import engine
from dashboard import version

SETUP_STATE_NAME = "setup-state.json"
_FRESH_STATE = {"version": 1, "completed": False, "step": 1, "draft": {"profile": {}, "grid": [], "bridge": {}}}


def _ok(data):
    return {"ok": True, "data": data}


def _fail(error):
    return {"ok": False, "error": str(error) or "알 수 없는 오류가 났어요"}


def guarded(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return _ok(method(self, *args, **kwargs))
        except Exception as error:  # noqa: BLE001 - JS에는 한국어 한 문장만 보낸다
            return _fail(error)

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
    dir_opener: object = None
    popen_factory: object = None
    folder_picker: object = None


class Api:
    def __init__(self, config_dir, deps: BridgeDeps | None = None):
        self._config_dir = Path(config_dir)
        self._deps = deps or BridgeDeps()
        self._login = engine.LoginSession()

    # ----- setup-state -----

    def _state_path(self) -> Path:
        return self._config_dir / SETUP_STATE_NAME

    def _fresh_state(self) -> dict:
        return json.loads(json.dumps(_FRESH_STATE))

    def _write_state(self, state: dict) -> None:
        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._state_path().write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")

    def _load_state(self) -> dict:
        path = self._state_path()
        if path.exists():
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(state, dict):
                    merged = self._fresh_state()
                    merged.update(state)
                    return merged
            except ValueError:
                pass
            return self._fresh_state()
        if paths.profile_path(self._config_dir).exists():
            # 기존 사용자(웹 UI 이전 설치): 마법사로 끌고 가지 않는다.
            state = self._fresh_state()
            state["completed"] = True
            state["step"] = 7
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
    def ai_skills_install(self, keys):
        return engine.ai_skills_install(keys)

    @guarded
    def save_setup_state(self, state):
        if not isinstance(state, dict):
            raise ValueError("설정 진행 상태 모양이 올바르지 않아요")
        merged = self._fresh_state()
        merged.update(state)
        self._write_state(merged)
        return True

    @guarded
    def finish_setup(self):
        state = self._fresh_state()
        state["completed"] = True
        state["step"] = 7
        self._write_state(state)  # draft(키 포함)를 비운 채 기록
        return True

    @guarded
    def restart_setup(self):
        self._write_state(self._fresh_state())
        return True

    # ----- 조회·검증 -----

    def _run(self):
        return self._deps.run_command or engine._default_run_command

    @guarded
    def home_checks(self):
        results = engine.home_checks(self._config_dir, deps=self._deps.home_check_deps)
        return [asdict(r) for r in results]

    @guarded
    def attendance_status(self):
        return asdict(engine.read_attendance_status(self._config_dir, self._run()))

    @guarded
    def ensure_attendance(self):
        deps = self._deps.attendance_deps or engine.AttendanceDeps(run_command=self._run())
        return asdict(engine.ensure_attendance(self._config_dir, deps=deps))

    @guarded
    def start_new_attendance(self):
        deps = self._deps.attendance_deps or engine.AttendanceDeps(run_command=self._run())
        return asdict(engine.start_new_attendance(self._config_dir, deps=deps))

    @guarded
    def attendance_chat_status(self):
        from dashboard import central_chat
        # 상태 조회는 화면에 보여 줄 값만 읽고, Google 시트는 바꾸지 않는다.
        return central_chat.chat_status(self._config_dir)

    @guarded
    def attendance_chat_connect(self):
        from dashboard import central_chat
        auth_url = central_chat.start_auth(self._config_dir)
        opener = self._deps.url_opener or webbrowser.open
        opener(auth_url)
        return {"opened": True}

    @guarded
    def attendance_chat_spaces(self):
        from dashboard import central_chat
        return central_chat.list_spaces(self._config_dir)

    @guarded
    def attendance_chat_set_space(self, space_name, display_name):
        from dashboard import central_chat
        return central_chat.set_class_space(self._config_dir, str(space_name), str(display_name))

    @guarded
    def computer_status(self):
        return engine.computer_readiness(self._run())

    @guarded
    def google_status(self):
        run = self._run()
        gws = engine.resolve_gws(run)
        auth = engine.gws_auth_status(run, gws) if gws else {"logged_in": False, "user": "", "raw": ""}
        return {
            "node": engine.check_version(run, "node"),
            "npm": engine.check_version(run, "npm"),
            "gws": gws,
            "logged_in": bool(auth["logged_in"]),
            "user": auth["user"],
        }

    @guarded
    def read_profile(self):
        return engine.read_profile_values(self._config_dir)

    @guarded
    def read_grid(self):
        return engine.read_timetable_grid(self._config_dir)

    @guarded
    def list_calendars(self):
        return engine.list_calendars(self._run(), engine.resolve_gws(self._run()))

    @guarded
    def list_tasklists(self):
        return engine.list_tasklists(self._run(), engine.resolve_gws(self._run()))

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
    def capture_progress(self):
        return capture_store.read_progress(paths.bridge_state_dir(self._config_dir))

    # ----- 행동 -----

    def _success(self, ok, detail):
        return {"success": bool(ok), "detail": detail}

    @guarded
    def install_node(self):
        return self._success(*engine.install_node(self._run()))

    @guarded
    def install_gws(self):
        return self._success(*engine.install_gws(self._run()))

    @guarded
    def gws_login_start(self):
        run = self._run()
        gws = engine.resolve_gws(run)
        if not gws:
            raise RuntimeError("gws 도구가 아직 없어요. 먼저 설치해 주세요")
        self._login.start(engine.login_command(gws), popen=self._deps.popen_factory)
        return self._login.snapshot()

    @guarded
    def gws_login_status(self):
        return engine.annotate_login_snapshot(self._login.snapshot())

    @guarded
    def gws_login_cancel(self):
        return {"cancelled": self._login.cancel()}

    def _resolve_gws_or_fail(self):
        run = self._run()
        gws = engine.resolve_gws(run)
        if not gws:
            raise RuntimeError("gws 도구가 아직 없어요. 먼저 설치해 주세요")
        return run, gws

    @guarded
    def gws_logout(self):
        run, gws = self._resolve_gws_or_fail()
        return self._success(*engine.gws_logout(run, gws))

    @guarded
    def ensure_calendar_named(self, name):
        run, gws = self._resolve_gws_or_fail()
        name = str(name or "").strip()
        if not name:
            raise ValueError("캘린더 이름을 적어 주세요")
        made_id = engine.ensure_calendar(run, gws, name)
        if not made_id:
            raise RuntimeError(f"'{name}' 캘린더를 만들지 못했어요. 잠시 뒤 다시 시도해 주세요")
        return {"id": made_id, "name": name}

    @guarded
    def ensure_tasklist_named(self, name):
        run, gws = self._resolve_gws_or_fail()
        name = str(name or "").strip()
        if not name:
            raise ValueError("할일 목록 이름을 적어 주세요")
        made_id = engine.ensure_tasklist(run, gws, name)
        if not made_id:
            raise RuntimeError(f"'{name}' 할일 목록을 만들지 못했어요. 잠시 뒤 다시 시도해 주세요")
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
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ValueError("https 주소만 열 수 있어요")
        opener = self._deps.url_opener or webbrowser.open
        opener(url)
        return True
