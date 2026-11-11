"""설치된 TeacherManagerTools.exe가 쓰는 정해 둔 제품 명령."""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence
from urllib.parse import urlsplit

from . import account_sessions, bundle_paths, paths, process_win


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
    expected = account_sessions.root_config_dir(expected_config_dir or paths.default_config_dir())
    requested = Path(str(raw or ""))
    if not requested.is_absolute() or str(requested.anchor).startswith("\\\\"):
        raise ValueError("개인 설정 폴더는 이 컴퓨터의 전체 경로여야 합니다.")
    expected_key = os.path.normcase(os.path.abspath(str(expected)))
    requested_key = os.path.normcase(os.path.abspath(str(requested)))
    current = account_sessions.active_config_dir(expected)
    current_key = os.path.normcase(str(current))
    if requested_key not in {expected_key, current_key}:
        raise ValueError("Teacher Manager가 사용하는 개인 설정 폴더만 선택할 수 있습니다.")
    _reject_reparse_components(Path(os.path.abspath(str(requested))))
    _reject_reparse_components(expected)
    return expected


@contextmanager
def _configuration_command(raw, expected_config_dir=None):
    root = _config_dir(raw, expected_config_dir)
    expected = account_sessions.token(root)
    with account_sessions.work(root, expected_token=expected) as state:
        yield account_sessions.active_config_dir(root, state)


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
    with _configuration_command(args.config_dir, expected_config_dir) as config:
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
    with _configuration_command(args.config_dir, expected_config_dir) as config:
        if parse_func is None:
            from parse_settings import parse_config_dir

            parse_func = parse_config_dir
        _print_result({"profile": str(parse_func(config))})
        return 0


def _default_handlers() -> dict[str, Callable[[Sequence[str]], int]]:
    return {
        "setup-init": run_setup_init,
        "parse-settings": run_parse_settings,
    }


def main(
    argv: Sequence[str], *,
    command_handlers: Mapping[str, Callable[[Sequence[str]], int]] | None = None,
) -> int:
    """정해 둔 제품 명령만 허용하고 임의 Python·모듈·명령 실행은 제공하지 않는다."""
    values = list(argv)
    if not values:
        print("사용법: TeacherManagerTools.exe <정해진 명령>", file=sys.stderr)
        return 2
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
