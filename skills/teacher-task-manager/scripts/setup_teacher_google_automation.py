from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from brity_bridge import process_win


def run(args: list[str]) -> None:
    code, output = process_win.run_captured(args)
    if output:
        print(output, end="" if output.endswith("\n") else "\n")
    if code != 0:
        raise subprocess.CalledProcessError(code, args, output=output)


def missing_profile_message(profile_json: Path) -> str:
    config_dir = profile_json.parent
    return (
        "아직 개인 설정이 완성되지 않았습니다.\n"
        f"개인 설정 파일이 없습니다: {profile_json}\n"
        f"먼저 {config_dir} 폴더의 teacher-profile.csv와 weekly-timetable.xlsx를 채운 뒤,\n"
        "설정 파서를 실행해 profile.generated.json을 만들어야 합니다.\n"
        f'실행: python "{Path(__file__).resolve()}" --config-dir "{config_dir}" --parse'
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", default=str(Path.home() / "TeacherTaskManager"))
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--parse", action="store_true")
    parser.add_argument("--install-attendance", action="store_true")
    parser.add_argument("--attendance-task-list-title", default="출결 미제출 확인")
    parser.add_argument("--attendance-task-list-id", default="")
    parser.add_argument("--central-chat-sender-url", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    skill_root = Path(__file__).resolve().parent.parent
    task_parser = skill_root / "scripts" / "parse_settings.py"
    config_dir = Path(args.config_dir)

    if args.init:
        run([sys.executable, str(task_parser), "--config-dir", str(config_dir), "--init"])

    if args.parse:
        run([sys.executable, str(task_parser), "--config-dir", str(config_dir)])

    if args.install_attendance:
        installer = skill_root / "scripts" / "install_attendance_automation.py"
        profile_json = config_dir / "profile.generated.json"
        if not profile_json.exists():
            print(missing_profile_message(profile_json))
            return 2
        install_args = [sys.executable, str(installer), "--profile-json", str(profile_json)]
        install_args.extend([
            "--attendance-task-list-title",
            args.attendance_task_list_title,
        ])
        if args.attendance_task_list_id:
            install_args.extend(["--attendance-task-list-id", args.attendance_task_list_id])
        if args.central_chat_sender_url:
            install_args.extend(["--central-chat-sender-url", args.central_chat_sender_url])
        if args.dry_run:
            install_args.append("--dry-run")
        try:
            run(install_args)
        except subprocess.CalledProcessError as exc:
            print(
                f"출결 자동화 설치가 중간에 멈췄습니다 (종료 코드 {exc.returncode}). 위의 안내를 확인한 뒤 다시 실행해 주세요."
            )
            return exc.returncode or 2

    print(str(config_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
