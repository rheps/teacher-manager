from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brity_bridge import account_sessions, autostart_win, gws_env, paths, process_win, status_log


def main(argv=None) -> int:
    gws_env.prepare_gws_env()  # gws 로그인이 수시로 풀리지 않게 키 보관 방식을 고정한다
    parser = argparse.ArgumentParser(prog="brity_bridge", description="Brity 메시지 캘린더 연결 도우미")
    parser.add_argument("command", choices=["run", "setup", "doctor", "status", "enable-autostart", "disable-autostart"])
    parser.add_argument("--config-dir", default=str(paths.default_config_dir()))
    parser.add_argument("--launch-dashboard", action="store_true",
                        help="run과 함께 쓰면 트레이 시작 직후 대시보드도 띄운다 (부팅 자동 실행용)")
    args = parser.parse_args(argv)
    config_dir = Path(args.config_dir)

    if args.command == "run":
        if sys.platform != "win32":
            print("이 도우미는 Windows에서만 실행됩니다.")
            return 1
        from brity_bridge import tray_win

        try:
            from brity_bridge import dns_warm, tools_cli

            dns_warm.start_shared(extra_hosts=dns_warm.hosts_from_urls(tools_cli.bundled_central_chat_sender_url()))
        except Exception:  # noqa: BLE001 - 예열 실패가 도우미를 막으면 안 된다
            pass
        tray_win.run_tray(config_dir, launch_dashboard=args.launch_dashboard)
        return 0
    if args.command == "setup":
        dashboard_dir = Path(__file__).resolve().parent.parent / "dashboard"
        process_win.popen_hidden(
            [sys.executable, str(dashboard_dir), "--config-dir", str(config_dir)]
        )
        print("설정 대시보드를 열었습니다.")
        return 0
    if args.command in {"doctor", "status"}:
        try:
            session = account_sessions.read_state(config_dir)
            if session.get("managed") and session.get("phase") != "active":
                raise account_sessions.AccountSessionError("설정에서 현재 Google 연결을 확인해 주세요.")
            expected = (session["account"], session["generation"])
            current = account_sessions.active_config_dir(config_dir, session)
            if args.command == "doctor":
                from brity_bridge import doctor
                results = doctor.run_doctor_checks(current)
                output, code = doctor.format_report(results), doctor.exit_code(results)
            else:
                last = status_log.read_last_status(paths.bridge_state_dir(current))
                output = json.dumps(last or {"message": "아직 처리한 메시지가 없습니다."}, ensure_ascii=False, indent=2)
                code = 0
            if expected != account_sessions.token(config_dir):
                raise account_sessions.AccountSessionError()
            print(output)
            return code
        except account_sessions.AccountSessionError as error:
            print(str(error), file=sys.stderr)
            return 2
    if args.command == "enable-autostart":
        autostart_win.enable_autostart()
        print("Windows 시작 시 자동 실행을 켰습니다.")
        return 0
    autostart_win.disable_autostart()
    print("Windows 시작 시 자동 실행을 껐습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
