"""설치된 TeacherManagerTools.exe가 쓰는 안전한 GWS 바깥 통로."""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import stat
import sys
from contextlib import redirect_stdout
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence
from urllib.parse import urlsplit

from . import bundle_paths, gws_env, paths, process_supervision, process_win, tool_runtime
from .google_account import (
    GOEDU_ACCOUNT_REQUIRED_MESSAGE,
    extract_email,
    require_goedu_email,
)


def _oauth_error_message(code: str) -> str:
    if code == "OAUTH_CLIENT_CONFLICT":
        return (
            "기존 Google 로그인 설정과 Teacher Manager의 로그인 설정이 서로 달라요. "
            "로그인 설정을 확인한 뒤 다시 시도해 주세요."
        )
    if code == "OAUTH_CLIENT_MISSING":
        return (
            "이 확인용 Teacher Manager에는 Google 로그인 준비 파일이 없어요. "
            "공개 설치판에서 다시 시도해 주세요."
        )
    return "Google 로그인 준비 파일을 안전하게 읽지 못했어요. 설정을 확인해 주세요."


def _gws_command_can_run_without_account_check(args: Sequence[str]) -> bool:
    """계정을 고치거나 사용법만 보는 명령은 학교 계정 검사 전에 허용한다."""

    values = [str(value) for value in args]
    if not values:
        return True
    if any(value in {"-h", "--help"} for value in values):
        return True
    if values[0].casefold() in {"help", "version", "--version", "-v"}:
        return True
    if values[0].casefold() != "auth":
        return False
    if len(values) == 1:
        return True
    return values[1].casefold() in {"status", "login", "logout"}


def run_gws(
    args: list[str],
    *,
    resolve_executable=tool_runtime.resolve_gws_executable,
    run_passthrough=process_win.run_passthrough,
    environ: Mapping[str, str] | None = None,
    gws_config_dir: Path | None = None,
    bundled_client_path: Path | None = None,
) -> int:
    """검증된 GWS 전체 경로를 실행하고 자식 종료번호를 그대로 돌려준다."""
    base = dict(os.environ if environ is None else environ)
    if gws_env.unsafe_account_storage_overrides(base):
        # 실행 파일 확인보다 먼저 멈춘다. 공용/다른 Windows 계정의 로그인
        # 저장소가 지정된 상태에서는 도움말을 포함한 어떤 gws 명령도 띄우지 않는다.
        print(gws_env.ACCOUNT_STORAGE_ERROR_MESSAGE, file=sys.stderr)
        return 2
    try:
        executable = Path(resolve_executable())
    except Exception:  # noqa: BLE001 - 내부 경로나 예외 전문을 화면에 내보내지 않는다
        print(
            "Google Workspace 도구를 실행할 수 없어요. Teacher Manager를 다시 설치해 주세요.",
            file=sys.stderr,
        )
        return 127

    config_dir = Path(gws_config_dir or gws_env.default_gws_config_dir(base))
    is_login = len(args) >= 2 and args[0] == "auth" and args[1] == "login"
    if is_login:
        if bundled_client_path is None:
            candidate = bundle_paths.bundle_root() / "assets" / gws_env.CLIENT_FILE_NAME
            bundled_client_path = candidate if candidate.is_file() else None
        selection = gws_env.select_desktop_oauth_client(
            base,
            config_dir,
            bundled_client_path,
        )
        if not selection.ready:
            print(_oauth_error_message(selection.error_code), file=sys.stderr)
            return 2
        child_env = gws_env.login_environ(
            base,
            selection,
            gws_config_dir=config_dir,
        )
    else:
        child_env = gws_env.gws_environ(base, gws_config_dir=config_dir)
    if not _gws_command_can_run_without_account_check(args):
        code, output = process_win.run_captured(
            [str(executable), "auth", "status"],
            env=child_env,
            timeout=30,
        )
        try:
            if code != 0:
                raise RuntimeError(GOEDU_ACCOUNT_REQUIRED_MESSAGE)
            require_goedu_email(extract_email(output))
        except RuntimeError:
            print(GOEDU_ACCOUNT_REQUIRED_MESSAGE, file=sys.stderr)
            return 2
    return int(run_passthrough([str(executable), *args], env=child_env))


def _command_parser(name: str, description: str) -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog=f"TeacherManagerTools.exe {name}",
        description=description,
        allow_abbrev=False,
    )


def _parse(parser: argparse.ArgumentParser, argv: Sequence[str]):
    try:
        return parser.parse_args(list(argv))
    except (argparse.ArgumentError, SystemExit):
        return None


def _config_dir(raw: str, expected_config_dir: Path | None = None) -> Path:
    expected = Path(expected_config_dir or paths.default_config_dir())
    requested = Path(str(raw or ""))
    if not requested.is_absolute() or str(requested.anchor).startswith("\\\\"):
        raise ValueError("개인 설정 폴더는 이 컴퓨터의 전체 경로여야 합니다.")
    expected_key = os.path.normcase(os.path.abspath(str(expected)))
    requested_key = os.path.normcase(os.path.abspath(str(requested)))
    if requested_key != expected_key:
        raise ValueError("Teacher Manager가 사용하는 개인 설정 폴더만 선택할 수 있습니다.")
    selected = Path(os.path.abspath(str(requested)))
    _reject_reparse_components(selected)
    return selected


def _reject_reparse_components(path: Path) -> None:
    """개인 설정 경로가 다른 폴더로 몰래 꺾이는 링크·junction이면 멈춘다."""
    absolute = Path(os.path.abspath(str(path)))
    candidates = [absolute]
    candidates.extend(absolute.parents)
    flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    for candidate in candidates:
        try:
            info = os.lstat(candidate)
        except FileNotFoundError:
            continue
        attributes = int(getattr(info, "st_file_attributes", 0) or 0)
        if stat.S_ISLNK(info.st_mode) or bool(attributes & flag):
            raise ValueError("개인 설정 폴더에 바로가기나 연결 폴더를 사용할 수 없습니다.")


def _print_result(value) -> None:
    if is_dataclass(value):
        value = asdict(value)
    elif isinstance(value, Path):
        value = str(value)
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _result_state(value) -> str:
    if is_dataclass(value):
        return str(getattr(value, "state", "") or "")
    if isinstance(value, dict):
        return str(value.get("state", "") or "")
    return ""


def _print_result_with_exit(value, success_states: set[str]) -> int:
    _print_result(value)
    return 0 if _result_state(value) in success_states else 2


def _captured_notices(output: str) -> list[str]:
    if not str(output or "").strip():
        return []
    safe = process_win.safe_log_text([], str(output)).partition("결과: ")[2]
    safe = re.sub(
        r"(?i)\b(token|key|secret|password|authorization)(\s*[:=]\s*)([^\s,;}]+)",
        r"\1\2[숨김]",
        safe,
    )
    # Google 파일 번호·토큰처럼 보이는 긴 영문값은 안내에 필요하지 않으므로 숨긴다.
    safe = re.sub(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])", "[긴 값 숨김]", safe)
    return [line.strip()[:300] for line in safe.splitlines() if line.strip()][:20]


def _with_notices(value, output: str):
    if is_dataclass(value):
        payload = asdict(value)
    elif isinstance(value, dict):
        payload = dict(value)
    else:
        payload = {"state": "unknown", "detail": str(value)}
    notices = _captured_notices(output)
    if notices:
        payload["notices"] = notices
    return payload


def _approval_required(detail: str) -> int:
    _print_result({
        "state": "approval_required",
        "changes_applied": False,
        "detail": detail,
    })
    return 2


def _strict_json_dict(path: Path, label: str, *, maximum: int = 1024 * 1024) -> dict:
    try:
        raw = Path(path).read_bytes()
        if len(raw) > maximum:
            raise ValueError("too large")

        def unique_pairs(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate key")
                value[key] = item
            return value

        parsed = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_pairs)
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label}을 안전하게 읽지 못했습니다.") from error
    if not isinstance(parsed, dict):
        raise ValueError(f"{label}의 모양이 올바르지 않습니다.")
    return parsed


def installed_attendance_account(config_dir: Path) -> str:
    payload = _strict_json_dict(
        paths.attendance_setup_status_path(Path(config_dir)),
        "출결 준비 계정 기록",
        maximum=64 * 1024,
    )
    if payload.get("state") != "ready":
        raise ValueError("출결 준비가 완료된 계정 기록을 확인하지 못했습니다.")
    account = str(payload.get("account", "") or "").strip()
    if re.fullmatch(r"[^\s@]+@[^\s@]+", account) is None:
        raise ValueError("출결을 처음 준비한 Google 계정을 확인하지 못했습니다.")
    return account


def current_gws_account(
    *, resolve_executable=tool_runtime.resolve_gws_executable,
    run_command=None,
    supervised_runner=process_supervision.run_supervised_command,
    environ: Mapping[str, str] | None = None,
) -> str:
    """호출자가 적은 계정이 아니라 제품 GWS의 현재 로그인 계정을 직접 읽는다."""
    from brity_bridge.gws_account_status import current_gws_account as read_account

    base = dict(os.environ if environ is None else environ)
    if gws_env.unsafe_account_storage_overrides(base):
        raise gws_env.GwsAccountStorageError(
            gws_env.ACCOUNT_STORAGE_ERROR_MESSAGE
        )
    executable = Path(resolve_executable())
    if not executable.is_absolute():
        raise ValueError("Google 로그인 상태를 안전하게 확인하지 못했습니다.")

    def product_run(args):
        command = list(args)
        child_env = gws_env.gws_environ(
            base,
            gws_config_dir=gws_env.default_gws_config_dir(base),
        )
        if run_command is not None:
            return run_command(command, env=child_env, timeout=30)
        result = supervised_runner(command, env=child_env, timeout=30)
        if result.tree_stopped is not True:
            return process_supervision.TREE_NOT_STOPPED, (
                "Google 로그인 상태 확인을 안전하게 끝내지 못했습니다."
            )
        return int(result.code), str(result.output)

    return read_account(product_run, str(executable))


def _checked_goedu_account(account_resolver: Callable[[], str] | None = None) -> str:
    """공개 하위 명령이 Google 자료를 건드리기 직전에 학교 계정을 확인한다."""

    resolver = account_resolver or current_gws_account
    try:
        return require_goedu_email(resolver())
    except gws_env.GwsAccountStorageError as error:
        print(str(error), file=sys.stderr)
        return ""
    except Exception:  # noqa: BLE001 - 계정·내부 경로·명령 원문을 화면에 내보내지 않는다
        print(GOEDU_ACCOUNT_REQUIRED_MESSAGE, file=sys.stderr)
        return ""


def bundled_central_chat_sender_url() -> str:
    """외부 입력이 아닌 설치본 release.json의 고정 중앙 발송소 주소만 읽는다."""
    try:
        raw = (bundle_paths.bundle_root() / "release.json").read_bytes()
        if len(raw) > 64 * 1024:
            raise ValueError("release data too large")
        payload = json.loads(raw.decode("utf-8"))
        value = payload.get("centralChatSenderUrl") if isinstance(payload, dict) else ""
        parsed = urlsplit(value)
        if (
            not isinstance(value, str)
            or parsed.scheme.casefold() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("unsafe central sender URL")
        return value
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("설치본의 중앙 발송소 주소를 안전하게 확인하지 못했습니다.") from error


def run_setup_init(
    argv: Sequence[str], *, init_func: Callable | None = None,
    expected_config_dir: Path | None = None,
) -> int:
    parser = _command_parser("setup-init", "개인 설정 폴더와 빈 견본을 준비합니다.")
    parser.add_argument("--config-dir", required=True)
    args = _parse(parser, argv)
    if args is None:
        return 2
    config = _config_dir(args.config_dir, expected_config_dir)
    if init_func is None:
        from parse_settings import init_config_dir

        init_func = init_config_dir
    created = init_func(config)
    _print_result({"config_dir": str(config), "created": [str(Path(item).name) for item in created]})
    return 0


def run_parse_settings(
    argv: Sequence[str], *, parse_func: Callable | None = None,
    expected_config_dir: Path | None = None,
) -> int:
    parser = _command_parser("parse-settings", "설정 CSV와 시간표를 안전한 설정 파일로 바꿉니다.")
    parser.add_argument("--config-dir", required=True)
    args = _parse(parser, argv)
    if args is None:
        return 2
    config = _config_dir(args.config_dir, expected_config_dir)
    if parse_func is None:
        from parse_settings import parse_config_dir

        parse_func = parse_config_dir
    _print_result({"profile": str(parse_func(config))})
    return 0


def run_attendance_install(
    argv: Sequence[str], *, dry_run_func: Callable | None = None,
    ensure_func: Callable | None = None,
    expected_config_dir: Path | None = None,
    central_url_loader: Callable[[], str] = bundled_central_chat_sender_url,
    gws_resolver: Callable = tool_runtime.resolve_gws_executable,
    account_resolver: Callable[[], str] | None = None,
) -> int:
    parser = _command_parser("attendance-install", "사용자가 승인한 출결 자동화를 준비합니다.")
    parser.add_argument("--config-dir", required=True)
    parser.add_argument("--apply", action="store_true")
    args = _parse(parser, argv)
    if args is None:
        return 2
    config = _config_dir(args.config_dir, expected_config_dir)
    if args.apply:
        if not _checked_goedu_account(account_resolver):
            return 2
        captured = io.StringIO()
        if ensure_func is None:
            from dashboard import engine
            from install_attendance_automation import install_attendance_automation

            fixed_sender_url = central_url_loader()

            def fixed_installer(profile_json, **kwargs):
                # 화면의 출결 준비와 똑같은 잠금·계정·진행저장·재시도 절차를 쓰되,
                # 중앙 주소만 설치본의 고정값으로 덮어 외부 환경값을 받지 않는다.
                kwargs["central_chat_sender_url"] = fixed_sender_url
                return install_attendance_automation(profile_json, **kwargs)

            deps = engine.AttendanceDeps(attendance_installer=fixed_installer)
            with redirect_stdout(captured):
                result = engine.ensure_attendance(config, deps)
        else:
            with redirect_stdout(captured):
                result = ensure_func(config)
        return _print_result_with_exit(_with_notices(result, captured.getvalue()), {"ready"})

    profile = config / "profile.generated.json"
    if not profile.is_file():
        _print_result({
            "state": "profile-required",
            "mode": "dry_run",
            "changes_applied": False,
            "detail": "개인 설정 파일이 없어 확인 실행을 시작하지 않았습니다.",
        })
        return 2
    profile_data = _strict_json_dict(profile, "개인 설정 파일")
    calendars = profile_data.get("calendars")
    task_list_id = str((calendars or {}).get("homeroom_tasks_id", "") or "") if isinstance(calendars, dict) else ""
    if not _checked_goedu_account(account_resolver):
        return 2
    if dry_run_func is None:
        from install_attendance_automation import (
            install_attendance_automation,
            local_gemini_api_key,
        )

        dry_run_func = install_attendance_automation
        gemini_api_key = local_gemini_api_key(config)
    else:
        gemini_api_key = ""
    captured = io.StringIO()
    with redirect_stdout(captured):
        preview = dry_run_func(
            profile,
            dry_run=True,
            attendance_task_list_title="조종례시 담임학급 안내사항",
            attendance_task_list_id=task_list_id,
            central_chat_sender_url=central_url_loader(),
            gemini_api_key=gemini_api_key,
            gws_executable=gws_resolver(),
        )
    payload = {
        "state": "ready_for_apply",
        "mode": "dry_run",
        "changes_applied": False,
        "preview": asdict(preview) if is_dataclass(preview) else preview,
    }
    notices = _captured_notices(captured.getvalue())
    if notices:
        payload["notices"] = notices
    _print_result(payload)
    return 0


def run_connect_attendance(
    argv: Sequence[str], *, connector: Callable | None = None,
    expected_config_dir: Path | None = None,
    account_resolver: Callable[[], str] = current_gws_account,
    lock_factory: Callable | None = None,
) -> int:
    parser = _command_parser("connect-attendance", "이미 쓰는 출결 시트를 읽어 로컬 기록에 연결합니다.")
    parser.add_argument("--config-dir", required=True)
    parser.add_argument("--spreadsheet-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = _parse(parser, argv)
    if args is None:
        return 2
    if not args.apply:
        return _approval_required("로컬 출결 연결 기록을 바꾸려면 내용을 확인한 뒤 --apply를 붙여 주세요.")
    config = _config_dir(args.config_dir, expected_config_dir)
    if connector is None:
        from connect_existing_attendance_sheet import connect_existing_attendance_sheet

        connector = connect_existing_attendance_sheet
    if lock_factory is None:
        from dashboard.engine import attendance_setup_lock

        lock_factory = attendance_setup_lock
    # 적용 버튼을 누른 직후 같은 잠금 안에서 소유자·설정·자료를 전부 다시 읽은 뒤
    # 마지막 단계에서만 로컬 연결 기록을 바꾼다.
    with lock_factory(config):
        account = _checked_goedu_account(account_resolver)
        if not account:
            return 2
        result = connector(config, args.spreadsheet_id, account=account)
    return _print_result_with_exit(result, {"connected"})




def _default_handlers() -> dict[str, Callable[[Sequence[str]], int]]:
    return {
        "setup-init": run_setup_init,
        "parse-settings": run_parse_settings,
        "attendance-install": run_attendance_install,
        "connect-attendance": run_connect_attendance,
    }


def main(
    argv: Sequence[str], *, run_gws_func=run_gws,
    command_handlers: Mapping[str, Callable[[Sequence[str]], int]] | None = None,
) -> int:
    """정해 둔 제품 명령만 허용하고 임의 Python·모듈·명령 실행은 제공하지 않는다."""
    values = list(argv)
    if not values:
        print("사용법: TeacherManagerTools.exe <정해진 명령>", file=sys.stderr)
        return 2
    if values[0] == "gws":
        return int(run_gws_func(values[1:]))
    handlers = dict(_default_handlers() if command_handlers is None else command_handlers)
    handler = handlers.get(values[0])
    if handler is None:
        print("허용되지 않은 명령입니다. Teacher Manager 화면의 안내를 따라 주세요.", file=sys.stderr)
        return 2
    try:
        return int(handler(values[1:]))
    except Exception as error:  # 내부 경로·계정·키를 그대로 출력하지 않는다.
        safe = process_win.safe_log_text([], str(error))
        detail = (safe.partition("결과: ")[2] or safe).strip()[-300:]
        print(detail or "요청한 작업을 안전하게 마치지 못했습니다.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
