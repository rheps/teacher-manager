from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from brity_bridge import bundle_paths, process_win


CommandRunner = Callable[[Sequence[str], Path], str]

# assets/Code.gs의 시트 이름·헤더 상수와 반드시 같아야 한다.
# (tests/test_attendance_installer.py가 Code.gs 원본과 대조해서 검증한다.)
MONTHLY_CHAT_RESULT_HEADERS = [
    "Google Chat 발송상태",
    "Google Chat 시도시각",
    "Google Chat 결과",
]

MESSAGE_LEDGER_HEADERS = {
    "메신저 개인톡 내용": ["보낼 날짜", "번호", "이름", "쪽지 종류", "쪽지 내용", "들어온 곳", "상태", "연결 표시", "보낸 시각", "결과"],
    "메신저 단체톡 내용": ["보낼 날짜", "안내 종류", "안내 내용", "들어온 곳", "상태", "보낸 시각", "결과"],
    "발송기록": ["발송시각", "종류", "대상", "Chat방", "내용 미리보기", "결과", "오류"],
}


@dataclass(frozen=True)
class AttendanceInstallResult:
    spreadsheet_id: str
    spreadsheet_url: str
    template_doc_id: str
    template_doc_url: str
    script_id: str
    deployment_id: str
    folder_id: str
    task_list_id: str


def resolve_command(args: Sequence[str]) -> list[str]:
    if not args:
        return []
    executable = shutil.which(args[0])
    return [executable or args[0], *args[1:]]


def default_runner(args: Sequence[str], cwd: Path) -> str:
    resolved = resolve_command(args)
    code, output = process_win.run_captured(resolved, cwd=cwd)
    if code != 0:
        raise subprocess.CalledProcessError(code, resolved, output=output, stderr=output)
    return output


def load_profile(profile_json: Path) -> dict:
    return json.loads(profile_json.read_text(encoding="utf-8"))


def load_release_metadata() -> dict[str, str]:
    data = json.loads((bundle_paths.bundle_root() / "release.json").read_text(encoding="utf-8"))
    return {str(key): str(value) for key, value in data.items()}


def validate_sender_url(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        raise ValueError("CENTRAL_CHAT_SENDER_URL must not be blank for production installs.")
    if not value.lower().startswith("https://"):
        raise ValueError("CENTRAL_CHAT_SENDER_URL must start with https://")
    return value


def resolve_central_chat_sender_url(sender_url: str = "") -> str:
    explicit = str(sender_url or "").strip()
    if sender_url and not explicit:
        return validate_sender_url(explicit)
    if explicit:
        return validate_sender_url(explicit)
    env_url = str(os.environ.get("CENTRAL_CHAT_SENDER_URL", "") or "").strip()
    if env_url:
        return validate_sender_url(env_url)
    return validate_sender_url(load_release_metadata()["centralChatSenderUrl"])


def build_central_chat_defaults(
    spreadsheet_id: str,
    sender_url: str = "",
) -> dict[str, str]:
    return {
        "CENTRAL_CHAT_SENDER_URL": resolve_central_chat_sender_url(sender_url),
        "CENTRAL_CHAT_SHEET_ID": f"{spreadsheet_id}:{secrets.token_hex(8)}",
        "CENTRAL_CHAT_SHEET_SECRET": secrets.token_urlsafe(48),
    }


def build_config_rows(
    profile: dict,
    ids: dict,
    task_list_title: str,
    central_chat: dict[str, str],
) -> list[list[str]]:
    teacher = profile.get("teacher", {})
    school = profile.get("school", {})
    homeroom = profile.get("homeroom", {})
    homeroom_task_list_id = str(profile.get("calendars", {}).get("homeroom_tasks_id", "") or "")
    grade = str(homeroom.get("grade", "") or "")
    class_number = str(homeroom.get("class", "") or "")
    class_label = f"{grade}-{class_number}" if grade and class_number else ""
    return [
        ["설정키", "값", "설명", "예시/필수"],
        ["SCHOOL_NAME", str(school.get("name", "") or ""), "학교명입니다.", "예: ○○고등학교"],
        [
            "SCHOOL_YEAR",
            str(profile.get("school_year", "") or school.get("year", "") or "2026"),
            "학년도 표기용 기본값입니다.",
            "예: 2026",
        ],
        ["GRADE", grade, "담임 학년입니다.", "예: 2"],
        ["CLASS_NUMBER", class_number, "담임 반입니다.", "예: 3"],
        ["CLASS_LABEL", class_label, "학반 표시입니다.", "예: 2-3"],
        [
            "TEACHER_NAME",
            str(teacher.get("name", "") or ""),
            "담임 또는 담당 교사 이름입니다.",
            "예: 홍길동",
        ],
        [
            "TEMPLATE_DOC_ID",
            ids["template_doc_id"],
            "설치 도우미가 자동으로 입력한 Google Docs 템플릿 문서 ID입니다.",
            "필수",
        ],
        [
            "DEST_FOLDER_ID",
            ids["folder_id"],
            "생성된 신고서가 저장될 Google Drive 폴더 ID입니다.",
            "자동 입력",
        ],
        ["DEST_FOLDER_NAME", "출결 증빙", "출력 폴더 이름입니다.", "출결 증빙"],
        [
            "TASK_LIST_ID",
            ids["task_list_id"],
            "출결 미제출 확인에 사용할 Google Tasks 목록 ID입니다.",
            "자동 입력",
        ],
        ["TASK_LIST_TITLE", task_list_title, "Tasks 목록 이름입니다.", task_list_title],
        ["HOLIDAY_SHEET_NAME", "휴일", "휴일 시트 이름입니다.", "휴일"],
        ["ROSTER_SHEET_NAME", "학생명단", "학생 드롭다운 원본 시트입니다.", "학생명단"],
        ["STUDENT_DROPDOWN_RANGE", "C2:C200", "학생명단에서 사용할 범위입니다.", "C2:C200"],
        ["TIMEZONE", "Asia/Seoul", "날짜 표시 시간대입니다.", "Asia/Seoul"],
        [
            "MONTH_SHEET_NAMES",
            "3월,4월,5월,6월,7월,8월,9월,10월,11월,12월,1월,2월",
            "자동화 대상 월별 입력 시트 이름입니다.",
            "3월부터 2월까지",
        ],
        ["HOMEROOM_TASK_LIST_ID", homeroom_task_list_id, "조종례시 담임학급 안내사항 Google Tasks 목록 ID입니다.", "담임일 때 자동 입력"],
        ["CENTRAL_CHAT_SENDER_URL", central_chat["CENTRAL_CHAT_SENDER_URL"], "중앙 Google Chat 발송소 주소입니다. 공개 배포판에서 설정됩니다.", "예: https://chat-sender.example.com"],
        ["CENTRAL_CHAT_SHEET_ID", central_chat["CENTRAL_CHAT_SHEET_ID"], "이 시트를 중앙 발송소가 구분하는 번호입니다. 자동 생성됩니다.", "자동"],
        ["CENTRAL_CHAT_SHEET_SECRET", central_chat["CENTRAL_CHAT_SHEET_SECRET"], "이 시트에서 온 요청인지 확인하는 값입니다. 자동 생성됩니다.", "자동"],
        ["CLASS_CHAT_SPACE_ID", "", "학급 단체방 Google Chat 스페이스 ID입니다.", "교육청 메신저 정리·발송 메뉴에서 선택"],
        ["CLASS_CHAT_SPACE_NAME", "", "선생님이 알아볼 학급 Chat 방 이름입니다.", "예: 2학년 3반"],
        ["CHAT_LOG_SHEET_NAME", "발송기록", "교육청 메신저 발송 기록 시트 이름입니다.", "발송기록"],
        ["PERSONAL_MESSAGE_QUEUE_SHEET_NAME", "메신저 개인톡 내용", "개인에게 보낼 쪽지를 모아두는 시트 이름입니다.", "메신저 개인톡 내용"],
        ["CLASS_MESSAGE_QUEUE_SHEET_NAME", "메신저 단체톡 내용", "학급 전체에게 보낼 쪽지를 모아두는 시트 이름입니다.", "메신저 단체톡 내용"],
        [
            "SCRIPT_ID",
            ids["script_id"],
            "이 시트에 연결된 Apps Script 프로젝트 ID입니다. 설치 기록 파일이 없는 컴퓨터에서도 시트만 열면 스크립트를 찾을 수 있습니다.",
            "자동 입력",
        ],
        [
            "DEPLOYMENT_ID",
            ids["deployment_id"],
            "Apps Script API 실행용 배포 ID입니다. 설치 도우미가 자동으로 입력합니다.",
            "자동 입력",
        ],
    ]


class CommandOutputError(RuntimeError):
    """gws가 JSON 대신 오류 문구를 출력했다 — 원인 문구를 삼키지 않고 화면까지 끌고 간다."""

    def __init__(self, args: Sequence[str], output: str):
        super().__init__(f"gws 응답이 JSON이 아니에요: {str(output)[:200]}")
        self.cmd = list(args)
        self.output = str(output)


def run_json(runner: CommandRunner, args: Sequence[str], cwd: Path) -> Any:
    output = runner(args, cwd)
    try:
        # keyring 안내 줄 등 잡음이 섞여도 첫 JSON만 읽는다.
        return process_win.parse_first_json(output)
    except ValueError as error:
        raise CommandOutputError(args, output) from error


def with_dry_run_fallback(response: dict, fallback: dict, dry_run: bool) -> dict:
    if not dry_run:
        return response
    return {**response, **fallback}


def tasklist_items(response: Any) -> list[dict]:
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)]
    if isinstance(response, dict):
        items = response.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        values = response.get("value")
        if isinstance(values, list):
            return [item for item in values if isinstance(item, dict)]
    return []


def find_task_list_by_title(response: Any, title: str) -> dict | None:
    expected = str(title or "").strip()
    if not expected:
        return None
    for item in tasklist_items(response):
        if str(item.get("title", "") or "").strip() == expected and item.get("id"):
            return {"id": item["id"], "title": str(item.get("title", expected) or expected)}
    return None


def spreadsheet_titles(response: Any) -> set[str]:
    titles: set[str] = set()
    if not isinstance(response, dict):
        return titles
    for sheet in response.get("sheets", []):
        if not isinstance(sheet, dict):
            continue
        properties = sheet.get("properties")
        if not isinstance(properties, dict):
            continue
        title = str(properties.get("title", "") or "").strip()
        if title:
            titles.add(title)
    return titles


def validate_appsscript_manifest(manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if "chat" in manifest:
        raise ValueError("appsscript.json must not declare this Sheet as a Chat app.")


def write_install_record(profile_json: Path, result: AttendanceInstallResult) -> Path:
    record_path = profile_json.parent / "attendance-install.generated.json"
    record = {
        "spreadsheet_id": result.spreadsheet_id,
        "spreadsheet_url": result.spreadsheet_url,
        "template_doc_id": result.template_doc_id,
        "template_doc_url": result.template_doc_url,
        "script_id": result.script_id,
        "deployment_id": result.deployment_id,
        "folder_id": result.folder_id,
        "task_list_id": result.task_list_id,
    }
    descriptor, temp_name = tempfile.mkstemp(
        prefix=".attendance-install-", suffix=".tmp", dir=str(record_path.parent)
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            file.write(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
            file.flush()
            os.fsync(file.fileno())
        temp_path.replace(record_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return record_path


def missing_profile_message(profile_json: Path) -> str:
    return (
        f"개인 설정 파일이 없습니다: {profile_json}\n"
        "먼저 teacher-profile.csv와 weekly-timetable.xlsx를 채우고 설정 파서를 실행해 "
        "profile.generated.json을 만든 뒤 다시 실행해 주세요."
    )


def copy_assets_to_workdir(asset_root: Path, workdir: Path) -> Path:
    validate_appsscript_manifest(asset_root / "appsscript.json")
    shutil.copy2(asset_root / "attendance-workbook.xlsx", workdir / "attendance-workbook.xlsx")
    shutil.copy2(asset_root / "absence-report-template.docx", workdir / "absence-report-template.docx")
    script_dir = workdir / "script-src"
    script_dir.mkdir()
    shutil.copy2(asset_root / "Code.gs", script_dir / "Code.gs")
    shutil.copy2(asset_root / "appsscript.json", script_dir / "appsscript.json")
    return script_dir


def install_attendance_automation(
    profile_json: Path,
    runner: CommandRunner = default_runner,
    dry_run: bool = False,
    attendance_task_list_title: str = "출결 미제출 확인",
    attendance_task_list_id: str = "",
    central_chat_sender_url: str = "",
    resume: dict | None = None,
    progress: Callable[[dict], None] | None = None,
) -> AttendanceInstallResult:
    profile_json = Path(profile_json)
    asset_root = bundle_paths.bundle_root() / "assets"
    profile = load_profile(profile_json)

    # 지난 시도에서 이미 만든 Google 자료 ID — 있으면 생성 명령을 건너뛴다.
    created_ids: dict[str, str] = {
        str(key): str(value) for key, value in (resume or {}).items() if value
    }

    def report_progress() -> None:
        if progress is not None:
            progress(dict(created_ids))

    with tempfile.TemporaryDirectory(prefix="teacher-attendance-") as temp_name:
        workdir = Path(temp_name)
        copy_assets_to_workdir(asset_root, workdir)
        dry = ["--dry-run"] if dry_run else []

        if not created_ids.get("template_doc_id"):
            template = run_json(
                runner,
                [
                    "gws",
                    "drive",
                    "files",
                    "create",
                    *dry,
                    "--json",
                    json.dumps(
                        {
                            "name": "결석신고서 템플릿",
                            "mimeType": "application/vnd.google-apps.document",
                        },
                        ensure_ascii=False,
                    ),
                    "--upload",
                    ".\\absence-report-template.docx",
                    "--upload-content-type",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "--format",
                    "json",
                ],
                workdir,
            )
            template = with_dry_run_fallback(
                template,
                {
                    "id": "dry-run-template-doc-id",
                    "webViewLink": "https://docs.google.com/document/d/dry-run-template-doc-id/edit",
                },
                dry_run,
            )
            created_ids["template_doc_id"] = template["id"]
            created_ids["template_doc_url"] = template.get(
                "webViewLink", f"https://docs.google.com/document/d/{template['id']}/edit"
            )
            report_progress()
        elif not created_ids.get("template_doc_url"):
            created_ids["template_doc_url"] = (
                f"https://docs.google.com/document/d/{created_ids['template_doc_id']}/edit"
            )
        if not created_ids.get("spreadsheet_id"):
            sheet = run_json(
                runner,
                [
                    "gws",
                    "drive",
                    "files",
                    "create",
                    *dry,
                    "--json",
                    json.dumps(
                        {
                            "name": "출결신고서 자동화",
                            "mimeType": "application/vnd.google-apps.spreadsheet",
                        },
                        ensure_ascii=False,
                    ),
                    "--upload",
                    ".\\attendance-workbook.xlsx",
                    "--upload-content-type",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "--format",
                    "json",
                ],
                workdir,
            )
            sheet = with_dry_run_fallback(
                sheet,
                {
                    "id": "dry-run-spreadsheet-id",
                    "webViewLink": "https://docs.google.com/spreadsheets/d/dry-run-spreadsheet-id/edit",
                },
                dry_run,
            )
            created_ids["spreadsheet_id"] = sheet["id"]
            created_ids["spreadsheet_url"] = sheet.get(
                "webViewLink", f"https://docs.google.com/spreadsheets/d/{sheet['id']}/edit"
            )
            report_progress()
        elif not created_ids.get("spreadsheet_url"):
            created_ids["spreadsheet_url"] = (
                f"https://docs.google.com/spreadsheets/d/{created_ids['spreadsheet_id']}/edit"
            )
        if not created_ids.get("folder_id"):
            folder = run_json(
                runner,
                [
                    "gws",
                    "drive",
                    "files",
                    "create",
                    *dry,
                    "--json",
                    json.dumps(
                        {
                            "name": "출결 증빙",
                            "mimeType": "application/vnd.google-apps.folder",
                        },
                        ensure_ascii=False,
                    ),
                    "--format",
                    "json",
                ],
                workdir,
            )
            folder = with_dry_run_fallback(
                folder,
                {
                    "id": "dry-run-folder-id",
                    "webViewLink": "https://drive.google.com/drive/folders/dry-run-folder-id",
                },
                dry_run,
            )
            created_ids["folder_id"] = folder["id"]
            report_progress()
        task_list_id = str(attendance_task_list_id or "").strip()
        task_list_title = str(attendance_task_list_title or "출결 미제출 확인").strip() or "출결 미제출 확인"
        if created_ids.get("task_list_id"):
            task_list = {"id": created_ids["task_list_id"], "title": task_list_title}
        elif task_list_id:
            task_list = {"id": task_list_id, "title": task_list_title}
        else:
            task_list = None
            if not dry_run:
                existing_task_lists = run_json(
                    runner,
                    [
                        "gws",
                        "tasks",
                        "tasklists",
                        "list",
                        "--format",
                        "json",
                    ],
                    workdir,
                )
                task_list = find_task_list_by_title(existing_task_lists, task_list_title)
        if not task_list:
            task_list = run_json(
                runner,
                [
                    "gws",
                    "tasks",
                    "tasklists",
                    "insert",
                    *dry,
                    "--json",
                    json.dumps({"title": task_list_title}, ensure_ascii=False),
                    "--format",
                    "json",
                ],
                workdir,
            )
            task_list = with_dry_run_fallback(
                task_list,
                {"id": "dry-run-task-list-id", "title": task_list_title},
                dry_run,
            )
        if created_ids.get("task_list_id") != str(task_list["id"]):
            created_ids["task_list_id"] = str(task_list["id"])
            report_progress()
        if not created_ids.get("script_id"):
            script = run_json(
                runner,
                [
                    "gws",
                    "script",
                    "projects",
                    "create",
                    *dry,
                    "--json",
                    json.dumps(
                        {"title": "출결 신고서 자동화", "parentId": created_ids["spreadsheet_id"]},
                        ensure_ascii=False,
                    ),
                    "--format",
                    "json",
                ],
                workdir,
            )
            script = with_dry_run_fallback(
                script,
                {"scriptId": "dry-run-script-id"},
                dry_run,
            )
            created_ids["script_id"] = script["scriptId"]
            report_progress()
        if not created_ids.get("deployment_id"):
            run_json(
                runner,
                [
                    "gws",
                    "script",
                    "+push",
                    *dry,
                    "--script",
                    created_ids["script_id"],
                    "--dir",
                    ".\\script-src",
                    "--format",
                    "json",
                ],
                workdir,
            )
            version = run_json(
                runner,
                [
                    "gws",
                    "script",
                    "projects",
                    "versions",
                    "create",
                    *dry,
                    "--params",
                    json.dumps({"scriptId": created_ids["script_id"]}, ensure_ascii=False),
                    "--json",
                    json.dumps({"description": "출결 자동화 안내장 API"}, ensure_ascii=False),
                    "--format",
                    "json",
                ],
                workdir,
            )
            version = with_dry_run_fallback(
                version,
                {"versionNumber": 1},
                dry_run,
            )
            deployment = run_json(
                runner,
                [
                    "gws",
                    "script",
                    "projects",
                    "deployments",
                    "create",
                    *dry,
                    "--params",
                    json.dumps({"scriptId": created_ids["script_id"]}, ensure_ascii=False),
                    "--json",
                    json.dumps(
                        {
                            "versionNumber": version["versionNumber"],
                            "description": "출결 자동화 안내장 API",
                        },
                        ensure_ascii=False,
                    ),
                    "--format",
                    "json",
                ],
                workdir,
            )
            deployment = with_dry_run_fallback(
                deployment,
                {"deploymentId": "dry-run-deployment-id"},
                dry_run,
            )
            created_ids["deployment_id"] = deployment["deploymentId"]
            report_progress()

        ids = {
            "template_doc_id": created_ids["template_doc_id"],
            "folder_id": created_ids["folder_id"],
            "task_list_id": created_ids["task_list_id"],
            "script_id": created_ids["script_id"],
            "deployment_id": created_ids["deployment_id"],
        }
        central_chat = build_central_chat_defaults(created_ids["spreadsheet_id"], central_chat_sender_url)
        config_rows = build_config_rows(profile, ids, str(task_list["title"]), central_chat)
        values_body = {
            "majorDimension": "ROWS",
            "values": config_rows,
        }
        # xlsx 견본에 박힌 옛 설정 행이 남아 중복 키가 생기지 않게, 먼저 비우고 쓴다.
        run_json(
            runner,
            [
                "gws",
                "sheets",
                "spreadsheets",
                "values",
                "clear",
                *dry,
                "--params",
                json.dumps(
                    {"spreadsheetId": created_ids["spreadsheet_id"], "range": "설정!A1:D200"},
                    ensure_ascii=False,
                ),
                "--format",
                "json",
            ],
            workdir,
        )
        run_json(
            runner,
            [
                "gws",
                "sheets",
                "spreadsheets",
                "values",
                "update",
                *dry,
                "--params",
                json.dumps(
                    {
                        "spreadsheetId": created_ids["spreadsheet_id"],
                        "range": f"설정!A1:D{len(config_rows)}",
                        "valueInputOption": "RAW",
                    },
                    ensure_ascii=False,
                ),
                "--json",
                json.dumps(values_body, ensure_ascii=False),
                "--format",
                "json",
            ],
            workdir,
        )

        # 쪽지 대장·발송기록 시트는 설치 단계에서 Sheets API로 직접 만든다.
        # 앞 시도에서 탭 생성 뒤 멈췄을 수 있으므로 현재 제목을 읽고 없는 탭만 만든다.
        sheet_info = run_json(
            runner,
            [
                "gws",
                "sheets",
                "spreadsheets",
                "get",
                *dry,
                "--params",
                json.dumps({"spreadsheetId": created_ids["spreadsheet_id"]}, ensure_ascii=False),
                "--format",
                "json",
            ],
            workdir,
        )
        current_titles = spreadsheet_titles(sheet_info)
        missing_titles = [
            title for title in MESSAGE_LEDGER_HEADERS if title not in current_titles
        ]
        if missing_titles:
            run_json(
                runner,
                [
                    "gws",
                    "sheets",
                    "spreadsheets",
                    "batchUpdate",
                    *dry,
                    "--params",
                    json.dumps({"spreadsheetId": created_ids["spreadsheet_id"]}, ensure_ascii=False),
                    "--json",
                    json.dumps(
                        {
                            "requests": [
                                {"addSheet": {"properties": {"title": title}}}
                                for title in missing_titles
                            ]
                        },
                        ensure_ascii=False,
                    ),
                    "--format",
                    "json",
                ],
                workdir,
            )
        run_json(
            runner,
            [
                "gws",
                "sheets",
                "spreadsheets",
                "values",
                "batchUpdate",
                *dry,
                "--params",
                json.dumps({"spreadsheetId": created_ids["spreadsheet_id"]}, ensure_ascii=False),
                "--json",
                json.dumps(
                    {
                        "valueInputOption": "RAW",
                        "data": [
                            {"range": f"'{title}'!A1", "values": [headers]}
                            for title, headers in MESSAGE_LEDGER_HEADERS.items()
                        ],
                    },
                    ensure_ascii=False,
                ),
                "--format",
                "json",
            ],
            workdir,
        )

        # 서식·드롭다운·시트 순서 정리는 Apps Script 실행으로 마무리한다(최선 노력).
        # scripts.run이 계정 정책으로 막혀도 시트는 이미 위에서 만들어졌고,
        # 서식은 첫 사용(발송·동기화) 때 ensure 함수들이 자동 적용한다.
        try:
            run_json(
                runner,
                [
                    "gws",
                    "script",
                    "scripts",
                    "run",
                    *dry,
                    "--params",
                    json.dumps({"scriptId": created_ids["deployment_id"]}, ensure_ascii=False),
                    "--json",
                    json.dumps({"function": "apiSetupAttendanceWorkbook"}, ensure_ascii=False),
                    "--format",
                    "json",
                ],
                workdir,
            )
        except (subprocess.CalledProcessError, RuntimeError, json.JSONDecodeError):
            print(
                "서식/드롭다운 자동 적용(apiSetupAttendanceWorkbook)이 계정 정책으로 막혔습니다. "
                "시트는 모두 만들어져 있어 바로 쓸 수 있고, 서식은 첫 사용 때 자동 적용됩니다. "
                "지금 바로 정리하려면 Google Sheet에서 출결 업무 자동화 -> 고급 설정 -> "
                "기본 시트/설정 점검을 한 번 실행해 주세요."
            )

        result = AttendanceInstallResult(
            spreadsheet_id=created_ids["spreadsheet_id"],
            spreadsheet_url=created_ids["spreadsheet_url"],
            template_doc_id=created_ids["template_doc_id"],
            template_doc_url=created_ids["template_doc_url"],
            script_id=created_ids["script_id"],
            deployment_id=created_ids["deployment_id"],
            folder_id=created_ids["folder_id"],
            task_list_id=created_ids["task_list_id"],
        )
        if not dry_run:
            write_install_record(profile_json, result)
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-json", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--attendance-task-list-title", default="출결 미제출 확인")
    parser.add_argument("--attendance-task-list-id", default="")
    parser.add_argument(
        "--central-chat-sender-url",
        default=os.environ.get("CENTRAL_CHAT_SENDER_URL", ""),
        help="개발자가 배포한 중앙 Google Chat 발송소 주소입니다.",
    )
    args = parser.parse_args()
    profile_json = Path(args.profile_json)
    if not profile_json.exists():
        print(missing_profile_message(profile_json))
        return 2
    result = install_attendance_automation(
        profile_json,
        dry_run=args.dry_run,
        attendance_task_list_title=args.attendance_task_list_title,
        attendance_task_list_id=args.attendance_task_list_id,
        central_chat_sender_url=args.central_chat_sender_url,
    )
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    if args.dry_run:
        print("DRY RUN complete. No Google files were created.")
        print(
            "Dry-run settings keys: CLASS_CHAT_SPACE_ID, CENTRAL_CHAT_SENDER_URL, CENTRAL_CHAT_SHEET_ID, CENTRAL_CHAT_SHEET_SECRET, CHAT_LOG_SHEET_NAME, SCRIPT_ID"
        )
    else:
        print("출결 자동화 설치가 끝났습니다.")
        print(f"Google Sheet: {result.spreadsheet_url}")
        print(f"Google Docs template: {result.template_doc_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
