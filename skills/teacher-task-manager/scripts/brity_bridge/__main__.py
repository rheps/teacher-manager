from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brity_bridge import autostart_win, gws_env, paths, process_win, status_log


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

        tray_win.run_tray(config_dir, launch_dashboard=args.launch_dashboard)
        return 0
    if args.command == "setup":
        dashboard_dir = Path(__file__).resolve().parent.parent / "dashboard"
        process_win.popen_hidden(
            [sys.executable, str(dashboard_dir), "--config-dir", str(config_dir)]
        )
        print("설정 대시보드를 열었습니다.")
        return 0
    if args.command == "doctor":
        from brity_bridge import doctor

        results = doctor.run_doctor_checks(config_dir)
        print(doctor.format_report(results))
        return doctor.exit_code(results)
    if args.command == "status":
        last = status_log.read_last_status(paths.bridge_state_dir(config_dir))
        print(json.dumps(last or {"message": "아직 처리한 메시지가 없습니다."}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "enable-autostart":
        autostart_win.enable_autostart()
        print("Windows 시작 시 자동 실행을 켰습니다.")
        return 0
    autostart_win.disable_autostart()
    print("Windows 시작 시 자동 실행을 껐습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
