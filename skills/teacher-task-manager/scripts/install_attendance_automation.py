from __future__ import annotations

import argparse
import json
import os
import re
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

import apps_script_version
import attendance_script_update
import attendance_workbook_identity
from brity_bridge import bundle_paths, gws_env, process_win, tool_runtime


CommandRunner = Callable[[Sequence[str], Path], str]

_PENDING_DEPLOYMENT_DESCRIPTION = "pending_deployment_description"
_PENDING_DEPLOYMENT_VERSION = "pending_deployment_version_number"
_DEPLOYMENT_DESCRIPTION_PREFIX = "teacher-manager-attendance-install-"
_DRIVE_INTENT_PROPERTY = "teacherManagerInstallIntent"
_CONSOLIDATION_FINGERPRINT_PROPERTY = "teacherManagerConsolidationFingerprint"
_PENDING_TEMPLATE_INTENT = "pending_template_doc_intent"
_PENDING_SHEET_INTENT = "pending_spreadsheet_intent"
_PENDING_FOLDER_INTENT = "pending_folder_intent"
_PENDING_TASK_TITLE = "pending_task_list_title"
_PENDING_SCRIPT_TITLE = "pending_script_project_title"
_PENDING_SCRIPT_VERSION_DESCRIPTION = "pending_script_version_description"
# Apps Script 제목은 시트 승인 창(동의 화면)에 앱 이름으로 그대로 뜬다.
# Google 검수(2026-08-11)에 맞춰 마법사·Chat 연결 동의 화면과 같은 이름으로 시작한다.
_SCRIPT_TITLE_PREFIX = "Big-Silver Teacher Manager 출결 자동화 [설치표식 "
# 이 접두로 만들던 시절에 끊긴 설치 기록을 이어받을 때만 쓴다.
_LEGACY_SCRIPT_TITLE_PREFIXES = ("출결 신고서 자동화 [설치표식 ",)
_VERSION_DESCRIPTION_PREFIX = "teacher-manager-attendance-version-"
ATTENDANCE_CREATION_FIRST_SETUP = "first-setup"
ATTENDANCE_CREATION_SPLIT_REPAIR = "split-repair"
ATTENDANCE_CREATION_NEW_SCHOOL_YEAR = "new-school-year"
ATTENDANCE_CREATION_REASONS = frozenset(
    {
        ATTENDANCE_CREATION_FIRST_SETUP,
        ATTENDANCE_CREATION_SPLIT_REPAIR,
        ATTENDANCE_CREATION_NEW_SCHOOL_YEAR,
    }
)


def _pending_script_title_suffix(title: str) -> str:
    for prefix in (_SCRIPT_TITLE_PREFIX, *_LEGACY_SCRIPT_TITLE_PREFIXES):
        if title.startswith(prefix) and title.endswith("]"):
            return title[len(prefix):-1]
    return ""


class DeploymentRecoveryPendingError(RuntimeError):
    """배포 만들기 응답이 끊겨, 읽기 확인만 반복해야 하는 상태."""


class CreationRecoveryPendingError(RuntimeError):
    """생성 응답을 잃어 새로 만들지 않고 읽기 확인만 해야 하는 상태."""

# 월 탭의 Google Chat 제목 네 개는 Code.gs가 직접 쓴다(ensureMonthlyChatResultColumns_).
# 파이썬 쪽 사본은 attendance_chat_marker.CHAT_RESULT_HEADERS 한 벌만 둔다 —
# 두 벌을 두었더니 한쪽만 세 개인 채로 남아 재발송 표식 자리가 사라졌다(2026-07-25).

# assets/Code.gs의 시트 이름·헤더 상수와 반드시 같아야 한다.
# (tests/test_attendance_installer.py가 Code.gs 원본과 대조해서 검증한다.)
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
    workbook_name: str = ""
    # 깨끗한 새 컴퓨터에서 예전에 설치한 공식 시트를 다시 찾았지만, 그 시트의
    # Apps Script가 현재 필수 판보다 오래된 경우다. 시트는 그대로 연결하되,
    # 사용자가 별도 업데이트 단추를 누르기 전에는 준비 완료로 보지 않는다.
    script_update_required: bool = False
    # 지금 프로그램에 들어 있는 Code.gs와 원격 배포판이 정확히 같다고 확인한
    # 경우에만 남긴다. 프로그램이 바뀌면 지문도 달라져 다시 읽기 확인을 거친다.
    script_bundle_sha256: str = ""
    workbook_role: str = attendance_workbook_identity.ATTENDANCE_ROLE_VALUE


def default_runner(args: Sequence[str], cwd: Path) -> str:
    # gws 열쇠를 파일에 두게 고정한다. 앱과 스크립트가 서로 다른 곳을 보면
    # 한쪽 로그인이 다른 쪽에서 안 보이고, 자격 증명 관리자 읽기가 실패하면
    # gws가 토큰을 스스로 지운다. 이 명령에만 넘기고 파이썬 환경은 그대로 둔다.
    code, output = process_win.run_captured(
        list(args), cwd=cwd, env=gws_env.gws_environ()
    )
    if code != 0:
        raise subprocess.CalledProcessError(code, list(args), output=output, stderr=output)
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


def local_gemini_api_key(config_dir: Path | None = None) -> str:
    """이 컴퓨터에 저장된 Gemini 키를 읽는다. 못 읽으면 빈 값으로 본다.

    시트 안 Apps Script는 이 컴퓨터의 settings.json을 읽지 못한다. 설치할 때 설정 탭에
    실어 보내지 않으면 선생님이 시트에서 같은 키를 또 붙여넣게 된다.
    """
    from brity_bridge import paths as bridge_paths
    from brity_bridge import settings as bridge_settings

    try:
        root = Path(config_dir) if config_dir else bridge_paths.default_config_dir()
        loaded = bridge_settings.load_settings(bridge_paths.settings_path(root))
        return str(loaded.gemini_api_key or "").strip()
    except Exception:
        return ""


def build_config_rows(
    profile: dict,
    ids: dict,
    task_list_title: str,
    central_chat: dict[str, str],
    gemini_api_key: str = "",
) -> list[list[str]]:
    teacher = profile.get("teacher", {})
    school = profile.get("school", {})
    homeroom = profile.get("homeroom", {})
    homeroom_task_list_id = str(profile.get("calendars", {}).get("homeroom_tasks_id", "") or "")
    grade = str(homeroom.get("grade", "") or "")
    class_number = str(homeroom.get("class", "") or "")
    class_label = f"{grade}-{class_number}" if grade and class_number else ""
    school_year_value = (
        str(profile.get("school_year", "") or "").strip()
        or str(school.get("year", "") or "").strip()
        or current_school_year()
    )
    return [
        ["설정키", "값", "설명", "예시/필수"],
        ["SCHOOL_NAME", str(school.get("name", "") or ""), "학교명입니다.", "예: ○○고등학교"],
        [
            "SCHOOL_YEAR",
            school_year_value,
            "이 출석부의 학년도입니다. 티처 매니저 내 정보의 학년도와 맞아야 합니다.",
            "자동 입력",
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
        [
            "GEMINI_API_KEY",
            str(gemini_api_key or "").strip(),
            "AI 출결 입력이 쓰는 Gemini API 키입니다. 티처 매니저 연결 화면에 넣은 값이 자동으로 들어옵니다.",
            "자동 입력",
        ],
        # Code.gs의 ATTENDANCE_AI_ALLOWED_SETTING/VALUE와 이름·값이 같아야 한다.
        # 이 값이 있어야 새로 만든 시트에서 1행 AI 입력을 켤 수 있다(사본 이름 규칙의 예외).
        [
            "ATTENDANCE_AI_ALLOWED",
            "예",
            "이 시트에서 1행 AI 출결 입력을 켤 수 있는지입니다. 티처 매니저가 시트를 만들 때 자동으로 넣습니다.",
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


def _pending_deployment_identity(created_ids: dict[str, str]) -> tuple[str, int] | None:
    description = str(created_ids.get(_PENDING_DEPLOYMENT_DESCRIPTION, "") or "")
    version_text = str(created_ids.get(_PENDING_DEPLOYMENT_VERSION, "") or "")
    if not description and not version_text:
        return None
    token = description.removeprefix(_DEPLOYMENT_DESCRIPTION_PREFIX)
    valid_token = (
        description.startswith(_DEPLOYMENT_DESCRIPTION_PREFIX)
        and len(token) == 32
        and all(char in "0123456789abcdef" for char in token)
    )
    try:
        version_number = int(version_text)
    except (TypeError, ValueError):
        version_number = 0
    if not valid_token or version_number <= 0 or str(version_number) != version_text:
        raise DeploymentRecoveryPendingError(
            "앞선 Apps Script 배포 진행 기록을 안전하게 확인할 수 없어요. "
            "기존 자료는 건드리지 않았습니다."
        )
    return description, version_number


def _list_script_deployments(
    runner: CommandRunner,
    workdir: Path,
    script_id: str,
    gws_executable: str,
) -> list[dict]:
    deployments: list[dict] = []
    page_token = ""
    seen_tokens: set[str] = set()
    while True:
        params: dict[str, Any] = {"scriptId": script_id, "pageSize": 50}
        if page_token:
            params["pageToken"] = page_token
        reply = run_json(
            runner,
            [
                gws_executable,
                "script",
                "projects",
                "deployments",
                "list",
                "--params",
                json.dumps(params, ensure_ascii=False),
                "--format",
                "json",
            ],
            workdir,
        )
        if not isinstance(reply, dict):
            raise DeploymentRecoveryPendingError(
                "앞선 Apps Script 배포 목록을 안전하게 읽지 못했어요. "
                "새 배포는 만들지 않았습니다."
            )
        page = reply.get("deployments", [])
        if not isinstance(page, list) or any(not isinstance(item, dict) for item in page):
            raise DeploymentRecoveryPendingError(
                "앞선 Apps Script 배포 목록을 안전하게 읽지 못했어요. "
                "새 배포는 만들지 않았습니다."
            )
        deployments.extend(page)
        next_token = reply.get("nextPageToken", "")
        if not next_token:
            return deployments
        if not isinstance(next_token, str) or next_token in seen_tokens:
            raise DeploymentRecoveryPendingError(
                "앞선 Apps Script 배포 목록을 끝까지 읽지 못했어요. "
                "새 배포는 만들지 않았습니다."
            )
        seen_tokens.add(next_token)
        page_token = next_token


def _recover_pending_deployment(
    runner: CommandRunner,
    workdir: Path,
    script_id: str,
    description: str,
    version_number: int,
    gws_executable: str,
) -> str:
    matches: list[str] = []
    for deployment in _list_script_deployments(
        runner, workdir, script_id, gws_executable
    ):
        config = deployment.get("deploymentConfig")
        deployment_id = deployment.get("deploymentId")
        if (
            isinstance(config, dict)
            and isinstance(deployment_id, str)
            and deployment_id.strip()
            and config.get("scriptId") == script_id
            and config.get("versionNumber") == version_number
            and config.get("manifestFileName") == "appsscript"
            and config.get("description") == description
        ):
            matches.append(deployment_id.strip())
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise DeploymentRecoveryPendingError(
            "앞선 Apps Script 배포와 정확히 같은 결과가 둘 이상 보여 자동으로 고르지 않았어요. "
            "새 배포도 만들지 않았습니다."
        )
    raise DeploymentRecoveryPendingError(
        "앞선 Apps Script 배포 결과를 아직 확인하지 못했어요. "
        "중복 배포를 막기 위해 새로 만들지 않았습니다. 잠시 뒤 다시 시도해 주세요."
    )


def _intent_token(value: str, prefix: str, label: str) -> str:
    text = str(value or "")
    token = text.removeprefix(prefix)
    if (
        not text.startswith(prefix)
        or len(token) != 32
        or any(char not in "0123456789abcdef" for char in token)
    ):
        raise CreationRecoveryPendingError(
            f"앞선 {label} 만들기 기록을 안전하게 확인할 수 없어요. "
            "기존 자료는 건드리지 않았습니다."
        )
    return text


def _new_drive_intent(kind: str) -> str:
    return f"{kind}:{secrets.token_hex(16)}"


def _checked_drive_intent(value: str, kind: str, label: str) -> str:
    return _intent_token(value, f"{kind}:", label)


def _drive_files_all(
    runner: CommandRunner,
    workdir: Path,
    params: dict[str, Any],
    gws_executable: str,
) -> list[dict]:
    files: list[dict] = []
    page_token = ""
    seen_tokens: set[str] = set()
    while True:
        page_params = dict(params)
        if page_token:
            page_params["pageToken"] = page_token
        reply = run_json(
            runner,
            [
                gws_executable,
                "drive",
                "files",
                "list",
                "--params",
                json.dumps(page_params, ensure_ascii=False),
                "--format",
                "json",
            ],
            workdir,
        )
        if not isinstance(reply, dict) or reply.get("incompleteSearch") is True:
            raise CreationRecoveryPendingError(
                "Google Drive 목록을 끝까지 확인하지 못했어요. "
                "중복 자료를 막기 위해 새로 만들지 않았습니다."
            )
        page = reply.get("files", [])
        if not isinstance(page, list) or any(not isinstance(item, dict) for item in page):
            raise CreationRecoveryPendingError(
                "Google Drive 목록 응답을 안전하게 읽지 못했어요. "
                "중복 자료를 막기 위해 새로 만들지 않았습니다."
            )
        files.extend(page)
        next_token = reply.get("nextPageToken", "")
        if not next_token:
            return files
        if not isinstance(next_token, str) or next_token in seen_tokens:
            raise CreationRecoveryPendingError(
                "Google Drive 목록을 끝까지 확인하지 못했어요. "
                "중복 자료를 막기 위해 새로 만들지 않았습니다."
            )
        seen_tokens.add(next_token)
        page_token = next_token


def _recover_drive_resource(
    runner: CommandRunner,
    workdir: Path,
    *,
    intent: str,
    name: str,
    mime_type: str,
    label: str,
    gws_executable: str,
) -> dict:
    query = (
        f"appProperties has {{ key='{_DRIVE_INTENT_PROPERTY}' and value='{intent}' }} and "
        f"name = '{name}' and mimeType = '{mime_type}' and "
        "trashed = false and 'me' in owners"
    )
    candidates = _drive_files_all(
        runner,
        workdir,
        {
            "q": query,
            "fields": (
                "nextPageToken,incompleteSearch,"
                "files(id,name,mimeType,ownedByMe,appProperties,webViewLink,parents)"
            ),
            "pageSize": 1000,
            "supportsAllDrives": True,
        },
        gws_executable,
    )
    exact = [
        item
        for item in candidates
        if str(item.get("id", "") or "").strip()
        and item.get("name") == name
        and item.get("mimeType") == mime_type
        and item.get("ownedByMe") is True
        and isinstance(item.get("appProperties"), dict)
        and item["appProperties"].get(_DRIVE_INTENT_PROPERTY) == intent
    ]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise CreationRecoveryPendingError(
            f"앞선 {label} 만들기 결과가 여러 개 보여 자동으로 고르지 않았어요. "
            "새 자료도 만들지 않았습니다."
        )
    raise CreationRecoveryPendingError(
        f"앞선 {label} 만들기 결과를 아직 확인하지 못했어요. "
        "중복 자료를 막기 위해 새로 만들지 않았습니다. 잠시 뒤 다시 시도해 주세요."
    )


def _task_lists_all(
    runner: CommandRunner,
    workdir: Path,
    gws_executable: str,
) -> list[dict]:
    items: list[dict] = []
    page_token = ""
    seen_tokens: set[str] = set()
    while True:
        params: dict[str, Any] = {"maxResults": 1000}
        if page_token:
            params["pageToken"] = page_token
        reply = run_json(
            runner,
            [
                gws_executable,
                "tasks",
                "tasklists",
                "list",
                "--params",
                json.dumps(params, ensure_ascii=False),
                "--format",
                "json",
            ],
            workdir,
        )
        if not isinstance(reply, (dict, list)):
            raise CreationRecoveryPendingError(
                "Google Tasks 목록을 안전하게 읽지 못했어요. "
                "새 목록은 만들지 않았습니다."
            )
        if isinstance(reply, dict):
            if "items" in reply and not isinstance(reply.get("items"), list):
                raise CreationRecoveryPendingError(
                    "Google Tasks 목록을 안전하게 읽지 못했어요. "
                    "새 목록은 만들지 않았습니다."
                )
            if "value" in reply and not isinstance(reply.get("value"), list):
                raise CreationRecoveryPendingError(
                    "Google Tasks 목록을 안전하게 읽지 못했어요. "
                    "새 목록은 만들지 않았습니다."
                )
        page = tasklist_items(reply)
        items.extend(page)
        next_token = reply.get("nextPageToken", "") if isinstance(reply, dict) else ""
        if not next_token:
            return items
        if not isinstance(next_token, str) or next_token in seen_tokens:
            raise CreationRecoveryPendingError(
                "Google Tasks 목록을 끝까지 읽지 못했어요. "
                "새 목록은 만들지 않았습니다."
            )
        seen_tokens.add(next_token)
        page_token = next_token


def _exact_task_lists(items: Sequence[dict], title: str) -> list[dict]:
    return [
        {"id": str(item.get("id") or "").strip(), "title": title}
        for item in items
        if str(item.get("id") or "").strip()
        and str(item.get("title") or "").strip() == title
    ]


def _script_head_files(
    runner: CommandRunner,
    workdir: Path,
    script_id: str,
    gws_executable: str,
) -> list[dict]:
    reply = run_json(
        runner,
        [
            gws_executable,
            "script",
            "projects",
            "getContent",
            "--params",
            json.dumps({"scriptId": script_id}, ensure_ascii=False),
            "--format",
            "json",
        ],
        workdir,
    )
    files = reply.get("files") if isinstance(reply, dict) else None
    if not isinstance(files, list) or any(not isinstance(item, dict) for item in files):
        raise CreationRecoveryPendingError(
            "되찾은 Apps Script의 내용을 안전하게 읽지 못했어요. "
            "기존 코드는 덮어쓰지 않았습니다."
        )
    return files


def _new_project_is_still_empty(files: Sequence[dict]) -> bool:
    return (
        len(files) == 1
        and files[0].get("name") == "appsscript"
        and files[0].get("type") == "JSON"
        and isinstance(files[0].get("source"), str)
    )


def _recover_script_project(
    runner: CommandRunner,
    workdir: Path,
    *,
    title: str,
    spreadsheet_id: str,
    gws_executable: str,
) -> str:
    query = (
        f"name = '{title}' and mimeType = 'application/vnd.google-apps.script' and "
        "trashed = false and 'me' in owners"
    )
    drive_candidates = _drive_files_all(
        runner,
        workdir,
        {
            "q": query,
            "fields": "nextPageToken,incompleteSearch,files(id,name,mimeType,ownedByMe)",
            "pageSize": 1000,
        },
        gws_executable,
    )
    exact: list[str] = []
    for item in drive_candidates:
        script_id = str(item.get("id", "") or "").strip()
        if (
            not script_id
            or item.get("name") != title
            or item.get("mimeType") != "application/vnd.google-apps.script"
            or item.get("ownedByMe") is not True
        ):
            continue
        project = run_json(
            runner,
            [
                gws_executable,
                "script",
                "projects",
                "get",
                "--params",
                json.dumps({"scriptId": script_id}, ensure_ascii=False),
                "--format",
                "json",
            ],
            workdir,
        )
        creator = project.get("creator") if isinstance(project, dict) else None
        if (
            isinstance(project, dict)
            and project.get("scriptId") == script_id
            and project.get("title") == title
            and project.get("parentId") == spreadsheet_id
            and isinstance(creator, dict)
            and str(creator.get("email", "") or "").strip()
        ):
            exact.append(script_id)
    if len(exact) > 1:
        raise CreationRecoveryPendingError(
            "앞선 Apps Script 프로젝트가 여러 개 보여 자동으로 고르지 않았어요. "
            "새 프로젝트도 만들지 않았습니다."
        )
    if not exact:
        raise CreationRecoveryPendingError(
            "앞선 Apps Script 프로젝트 만들기 결과를 아직 확인하지 못했어요. "
            "중복 프로젝트를 막기 위해 새로 만들지 않았습니다."
        )
    script_id = exact[0]
    if not _new_project_is_still_empty(
        _script_head_files(runner, workdir, script_id, gws_executable)
    ):
        raise CreationRecoveryPendingError(
            "되찾은 Apps Script에 사용자가 고친 코드가 있어 자동으로 덮어쓰지 않았어요."
        )
    return script_id


def _script_versions_all(
    runner: CommandRunner,
    workdir: Path,
    script_id: str,
    gws_executable: str,
) -> list[dict]:
    versions: list[dict] = []
    page_token = ""
    seen_tokens: set[str] = set()
    while True:
        params: dict[str, Any] = {"scriptId": script_id, "pageSize": 50}
        if page_token:
            params["pageToken"] = page_token
        reply = run_json(
            runner,
            [
                gws_executable,
                "script",
                "projects",
                "versions",
                "list",
                "--params",
                json.dumps(params, ensure_ascii=False),
                "--format",
                "json",
            ],
            workdir,
        )
        if not isinstance(reply, dict):
            raise CreationRecoveryPendingError(
                "Apps Script 버전 목록을 안전하게 읽지 못했어요. "
                "새 버전은 만들지 않았습니다."
            )
        page = reply.get("versions", [])
        if not isinstance(page, list) or any(not isinstance(item, dict) for item in page):
            raise CreationRecoveryPendingError(
                "Apps Script 버전 목록을 안전하게 읽지 못했어요. "
                "새 버전은 만들지 않았습니다."
            )
        versions.extend(page)
        next_token = reply.get("nextPageToken", "")
        if not next_token:
            return versions
        if not isinstance(next_token, str) or next_token in seen_tokens:
            raise CreationRecoveryPendingError(
                "Apps Script 버전 목록을 끝까지 읽지 못했어요. "
                "새 버전은 만들지 않았습니다."
            )
        seen_tokens.add(next_token)
        page_token = next_token


def _recover_script_version(
    runner: CommandRunner,
    workdir: Path,
    *,
    script_id: str,
    description: str,
    expected_bundle_sha256: str,
    gws_executable: str,
) -> int:
    matches: list[int] = []
    for version in _script_versions_all(runner, workdir, script_id, gws_executable):
        number = version.get("versionNumber")
        if (
            version.get("scriptId") != script_id
            or version.get("description") != description
            or not isinstance(number, int)
            or isinstance(number, bool)
            or number <= 0
            or not str(version.get("createTime", "") or "").strip()
        ):
            continue
        reply = run_json(
            runner,
            [
                gws_executable,
                "script",
                "projects",
                "getContent",
                "--params",
                json.dumps(
                    {"scriptId": script_id, "versionNumber": number},
                    ensure_ascii=False,
                ),
                "--format",
                "json",
            ],
            workdir,
        )
        files = reply.get("files") if isinstance(reply, dict) else None
        try:
            bundle_sha256 = attendance_script_update.canonical_bundle_sha256(files or [])
        except Exception:
            continue
        if bundle_sha256 == expected_bundle_sha256:
            matches.append(number)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise CreationRecoveryPendingError(
            "앞선 Apps Script 버전과 정확히 같은 결과가 여러 개 보여 자동으로 고르지 않았어요. "
            "새 버전도 만들지 않았습니다."
        )
    raise CreationRecoveryPendingError(
        "앞선 Apps Script 버전 만들기 결과를 아직 확인하지 못했어요. "
        "중복 버전을 막기 위해 새로 만들지 않았습니다."
    )


# 옛 고정 이름 — 학년도 도입(2026-07-31) 전에 만든 시트가 이 이름을 갖고 있다.
# 새로 만드는 시트는 attendance_workbook_name()이 학년도 이름을 만든다. 이름이 해마다
# 다르므로, 기록을 잃고 이름으로 되찾을 때 작년 것을 조용히 붙잡는 일이 없다.
ATTENDANCE_LEGACY_SHEET_NAME = "출결신고서 자동화"


def current_school_year(today=None) -> str:
    return attendance_workbook_identity.current_school_year(today)


def attendance_workbook_name(profile: dict, today=None) -> str:
    return attendance_workbook_identity.attendance_workbook_name(profile, today)


SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"


class ExistingAttendanceSheetError(RuntimeError):
    """내 드라이브에 이미 출결 시트가 있어 새로 만들지 않고 멈춘다."""


class LegacyAttendanceConsolidationRequired(ExistingAttendanceSheetError):
    """로컬 기록은 없지만 Drive에 한 번 정리해야 할 예전 출결표가 있다."""

    def __init__(self, candidates: Sequence[dict]):
        self.candidates = tuple(
            dict(item) for item in candidates if isinstance(item, dict)
        )
        super().__init__(_legacy_consolidation_message(self.candidates))


def _sheet_lines(files: Sequence[dict]) -> list[str]:
    lines = []
    for item in files:
        link = str(item.get("webViewLink") or "").strip()
        if not link:
            link = f"https://docs.google.com/spreadsheets/d/{item.get('id')}/edit"
        lines.append(f"- {item.get('id')} {link}")
    return lines


def _too_many_sheets_message(files: Sequence[dict], name: str) -> str:
    return "\n".join(
        [
            "내 드라이브에 "
            + name
            + " 시트가 여러 개 있습니다. 어느 것을 쓰실지 알 수 없어 멈췄습니다.",
            "",
        ]
        + _sheet_lines(files)
        + [
            "",
            "쓰시는 시트 하나만 남기고 나머지 이름을 바꾸거나 휴지통으로 옮긴 뒤 "
            "다시 실행해 주세요.",
        ]
    )


def _cannot_reuse_message(sheet: dict, missing: Sequence[str], name: str) -> str:
    return "\n".join(
        [
            "쓰시던 " + name + " 시트를 찾았지만 연결값이 비어 있어 "
            "이어서 쓸 수 없습니다.",
            "",
        ]
        + _sheet_lines([sheet])
        + [
            "",
            "`설정` 탭에서 비어 있는 값: " + ", ".join(missing),
            "",
            "새로 만들면 시트가 두 개가 되고 프로그램이 빈 시트를 보게 되므로 "
            "만들지 않았습니다.",
        ]
    )


def _read_existing_settings(
    runner: CommandRunner,
    workdir: Path,
    spreadsheet_id: str,
    gws_executable: str,
) -> dict[str, str]:
    rows = _read_existing_setting_rows(
        runner, workdir, spreadsheet_id, gws_executable
    )
    settings: dict[str, str] = {}
    for row in rows:
        if len(row) >= 2:
            key = str(row[0]).strip()
            if key and key not in settings:
                settings[key] = str(row[1]).strip()
    return settings


def _read_existing_setting_rows(
    runner: CommandRunner,
    workdir: Path,
    spreadsheet_id: str,
    gws_executable: str,
) -> list[list[str]]:
    reply = run_json(
        runner,
        [
            gws_executable,
            "sheets",
            "spreadsheets",
            "values",
            "get",
            "--params",
            json.dumps(
                {"spreadsheetId": spreadsheet_id, "range": "설정!A1:D200"},
                ensure_ascii=False,
            ),
            "--format",
            "json",
        ],
        workdir,
    )
    rows = reply.get("values") if isinstance(reply, dict) else None
    normalized: list[list[str]] = []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, list):
                normalized.append(
                    [str(value if value is not None else "") for value in row[:4]]
                )
    return normalized


def _require_new_enough_script(
    runner: CommandRunner,
    workdir: Path,
    sheet: dict,
    script_id: str,
    deployment_id: str,
    name: str,
    gws_executable: str,
) -> tuple[str, bool, str]:
    """쓰던 Apps Script의 Sheet 연결·편집본·실제 배포판을 함께 확인한다."""

    from attendance_script_update import inspect_attendance_script_update

    inspection = inspect_attendance_script_update(
        str(sheet.get("id") or "").strip(),
        script_id,
        deployment_id,
        assets_dir=bundle_paths.bundle_root() / "assets",
        runner=runner,
        gws_executable=gws_executable,
    )
    if inspection.verified and inspection.state == "current":
        return "", False, inspection.target_bundle_sha256
    if inspection.verified and inspection.state == "update_available":
        return "", True, ""

    # 중단 안내에는 가능하면 사용자가 보던 판 번호도 남긴다. 이 추가 조회는
    # 읽기뿐이며, 위의 Sheet 부모·HEAD·고정 배포판 대조를 대신하지 않는다.
    found = None
    try:
        reply = run_json(
            runner,
            [
                gws_executable,
                "script",
                "projects",
                "getContent",
                "--params",
                json.dumps({"scriptId": script_id}, ensure_ascii=False),
                "--format",
                "json",
            ],
            workdir,
        )
        for item in reply.get("files") or []:
            if isinstance(item, dict):
                found = apps_script_version.app_version_in_source(item.get("source"))
                if found:
                    break
    except Exception:  # noqa: BLE001 - 안내용 판 번호를 못 읽어도 안전 중단은 유지한다
        found = None
    minimum = apps_script_version.minimum_apps_script_version(
        bundle_paths.bundle_root()
    )
    raise ExistingAttendanceSheetError(
        "\n".join(
            [
                "쓰시던 "
                + name
                + " 시트를 찾았지만, 그 시트에 붙은 Apps Script를 안전하게 "
                "이어 쓸 수 없습니다.",
                (
                    f"화면에서 읽힌 판은 {found}이고 현재 필요한 판은 {minimum}입니다."
                    if found
                    else f"현재 필요한 판은 {minimum}입니다."
                ),
                "",
            ]
            + _sheet_lines([sheet])
            + [
                "",
                "공식 배포 코드인지, 이 Sheet에 묶인 코드인지, 현재 편집본과 실제 "
                "배포판이 같은지를 모두 확인하지 못해 자동으로 덮어쓰지 않았습니다.",
                "사용자 수정 코드가 있다면 그대로 보호됩니다.",
            ]
        )
    )


def reuse_existing_attendance_sheet(
    runner: CommandRunner,
    workdir: Path,
    sheet: dict,
    name: str,
    gws_executable: str,
) -> AttendanceInstallResult:
    """쓰던 시트의 `설정` 탭을 읽어 연결값만 돌려준다. 시트에는 쓰지 않는다.

    `설정`을 덮어쓰면 거기 든 Google Chat 발송 등록이 끊어지므로 읽기만 한다.
    """

    spreadsheet_id = str(sheet.get("id") or "").strip()
    settings = _read_existing_settings(
        runner, workdir, spreadsheet_id, gws_executable
    )
    wanted = {
        "template_doc_id": "TEMPLATE_DOC_ID",
        "folder_id": "DEST_FOLDER_ID",
        "task_list_id": "TASK_LIST_ID",
        "script_id": "SCRIPT_ID",
        "deployment_id": "DEPLOYMENT_ID",
    }
    missing = sorted(key for key in wanted.values() if not settings.get(key))
    if missing:
        raise ExistingAttendanceSheetError(_cannot_reuse_message(sheet, missing, name))
    _found_version, script_update_required, script_bundle_sha256 = _require_new_enough_script(
        runner,
        workdir,
        sheet,
        settings[wanted["script_id"]],
        settings[wanted["deployment_id"]],
        name,
        gws_executable,
    )
    link = str(sheet.get("webViewLink") or "").strip()
    return AttendanceInstallResult(
        spreadsheet_id=spreadsheet_id,
        spreadsheet_url=link
        or f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
        template_doc_id=settings[wanted["template_doc_id"]],
        template_doc_url=(
            "https://docs.google.com/document/d/"
            + settings[wanted["template_doc_id"]]
            + "/edit"
        ),
        script_id=settings[wanted["script_id"]],
        deployment_id=settings[wanted["deployment_id"]],
        folder_id=settings[wanted["folder_id"]],
        task_list_id=settings[wanted["task_list_id"]],
        workbook_name=str(sheet.get("name", "") or name),
        script_update_required=script_update_required,
        script_bundle_sha256=script_bundle_sha256,
    )


def find_existing_attendance_sheets(
    runner: CommandRunner,
    workdir: Path,
    dry_run: bool,
    name: str,
    gws_executable: str,
) -> list[dict]:
    """내 드라이브에서 같은 이름의 출결 시트를 찾는다. 읽기만 한다."""

    if dry_run:
        return []
    query = (
        f"name = '{name}' and "
        f"mimeType = '{SPREADSHEET_MIME}' and "
        "trashed = false and 'me' in owners"
    )
    files = _drive_files_all(
        runner,
        workdir,
        {
            "q": query,
            "fields": (
                "nextPageToken,incompleteSearch,"
                "files(id,name,mimeType,ownedByMe,webViewLink)"
            ),
            "pageSize": 1000,
            "supportsAllDrives": True,
        },
        gws_executable,
    )
    for item in files:
        if not (
            str(item.get("name", "")).strip() == name
            and item.get("mimeType") == SPREADSHEET_MIME
            and item.get("ownedByMe") is True
            and str(item.get("id", "")).strip()
        ):
            raise CreationRecoveryPendingError(
                "기존 출결 시트 목록 응답을 안전하게 확인할 수 없어요. "
                "새 시트는 만들지 않았습니다."
            )
    return files


def find_canonical_attendance_sheets(
    runner: CommandRunner,
    workdir: Path,
    dry_run: bool,
    school_year: str,
    gws_executable: str,
) -> list[dict]:
    """이름과 상관없이 정식 표식·학년도가 같은 내 출결 파일을 찾는다."""

    if dry_run:
        return []
    role_key = attendance_workbook_identity.ATTENDANCE_ROLE_PROPERTY
    role_value = attendance_workbook_identity.ATTENDANCE_ROLE_VALUE
    year_key = attendance_workbook_identity.ATTENDANCE_SCHOOL_YEAR_PROPERTY
    query = (
        f"appProperties has {{ key='{role_key}' and value='{role_value}' }} and "
        f"appProperties has {{ key='{year_key}' and value='{school_year}' }} and "
        f"mimeType = '{SPREADSHEET_MIME}' and trashed = false and 'me' in owners"
    )
    files = _drive_files_all(
        runner,
        workdir,
        {
            "q": query,
            "fields": (
                "nextPageToken,incompleteSearch,"
                "files(id,name,mimeType,ownedByMe,appProperties,webViewLink)"
            ),
            "pageSize": 1000,
            "supportsAllDrives": True,
        },
        gws_executable,
    )
    for item in files:
        properties = item.get("appProperties")
        if not (
            str(item.get("id", "") or "").strip()
            and item.get("mimeType") == SPREADSHEET_MIME
            and item.get("ownedByMe") is True
            and isinstance(properties, dict)
            and properties.get(role_key) == role_value
            and properties.get(year_key) == school_year
        ):
            raise CreationRecoveryPendingError(
                "정식 출결 시트 목록 응답을 안전하게 확인할 수 없어요. "
                "새 시트는 만들지 않았습니다."
            )
    return files


def find_legacy_attendance_sheets(
    runner: CommandRunner,
    workdir: Path,
    dry_run: bool,
    names: Sequence[str],
    gws_executable: str,
) -> list[dict]:
    """두 갈래 정리 대상으로만 쓰는 예전 이름의 출결 파일을 찾는다."""

    found: list[dict] = []
    seen_ids: set[str] = set()
    for name in dict.fromkeys(str(value or "").strip() for value in names):
        if not name:
            continue
        for item in find_existing_attendance_sheets(
            runner, workdir, dry_run, name, gws_executable
        ):
            item_id = str(item.get("id", "") or "").strip()
            if item_id and item_id not in seen_ids:
                seen_ids.add(item_id)
                found.append(item)
    return found


def _legacy_consolidation_message(files: Sequence[dict]) -> str:
    return "\n".join(
        [
            "예전 이름의 출결 시트를 찾았어요.",
            "새 시트를 자동으로 만들면 다시 두 갈래가 되므로 만들지 않았습니다.",
            "Teacher Manager에서 `출결 시트 하나로 정리`를 눌러 한 파일로 이어 주세요.",
            "",
        ]
        + _sheet_lines(files)
    )


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
    from attendance_install_record import (
        SCRIPT_ATTESTATION_FIELD,
        build_script_attestation,
    )

    record_path = profile_json.parent / "attendance-install.generated.json"
    profile = load_profile(profile_json)
    school = profile.get("school") or {}
    homeroom = profile.get("homeroom") or {}
    record = {
        "spreadsheet_id": result.spreadsheet_id,
        "spreadsheet_url": result.spreadsheet_url,
        "template_doc_id": result.template_doc_id,
        "template_doc_url": result.template_doc_url,
        "script_id": result.script_id,
        "deployment_id": result.deployment_id,
        "folder_id": result.folder_id,
        "task_list_id": result.task_list_id,
        "school_year": str(school.get("year", "") or "").strip() or current_school_year(),
        "homeroom_grade": str(homeroom.get("grade", "") or "").strip(),
        "homeroom_class": str(homeroom.get("class", "") or "").strip(),
        "workbook_name": result.workbook_name,
        "workbook_role": str(
            getattr(result, "workbook_role", "")
            or attendance_workbook_identity.ATTENDANCE_ROLE_VALUE
        ),
    }
    if result.script_update_required:
        record["script_update_required"] = True
    script_bundle_sha256 = str(
        getattr(result, "script_bundle_sha256", "") or ""
    )
    if script_bundle_sha256:
        record[SCRIPT_ATTESTATION_FIELD] = build_script_attestation(
            record, script_bundle_sha256
        )
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


def _require_attendance_creation_reason(reason: str) -> str:
    checked = str(reason or "").strip()
    if checked not in ATTENDANCE_CREATION_REASONS:
        raise ValueError(
            "새 출결 시트를 만들 수 없는 요청입니다. 처음 설정, 출결 시트 하나로 "
            "정리, 새 학년도 시작에서만 만들 수 있습니다."
        )
    return checked


def _consolidation_candidate_name(workbook_name: str, fingerprint: str) -> str:
    checked_name = str(workbook_name or "").strip()
    checked_fingerprint = str(fingerprint or "").strip().lower()
    if not checked_name:
        raise ValueError("정리 후보 출석부의 정식 이름이 없습니다.")
    if not re.fullmatch(r"[0-9a-f]{64}", checked_fingerprint):
        raise ValueError("정리 미리보기 지문을 확인하지 못했습니다.")
    return f"{checked_name} (정리 중 {checked_fingerprint[:12]})"


def _verify_consolidation_candidate_identity(
    runner: CommandRunner,
    workdir: Path,
    *,
    candidate_spreadsheet_id: str,
    source_spreadsheet_id: str,
    expected_name: str,
    fingerprint: str,
    gws_executable: str,
) -> dict:
    candidate_id = str(candidate_spreadsheet_id or "").strip()
    source_id = str(source_spreadsheet_id or "").strip()
    if not candidate_id or not source_id or candidate_id == source_id:
        raise CreationRecoveryPendingError(
            "정리 후보와 현재 출석부를 서로 다른 파일로 확인하지 못했어요."
        )
    checked = run_json(
        runner,
        [
            gws_executable,
            "drive",
            "files",
            "get",
            "--params",
            json.dumps(
                {
                    "fileId": candidate_id,
                    "fields": (
                        "id,name,mimeType,ownedByMe,trashed,"
                        "appProperties,webViewLink"
                    ),
                },
                ensure_ascii=False,
            ),
            "--format",
            "json",
        ],
        workdir,
    )
    properties = checked.get("appProperties") if isinstance(checked, dict) else None
    if not (
        isinstance(checked, dict)
        and checked.get("id") == candidate_id
        and checked.get("name") == expected_name
        and checked.get("mimeType") == SPREADSHEET_MIME
        and checked.get("ownedByMe") is True
        and checked.get("trashed") is False
        and isinstance(properties, dict)
        and properties.get(_CONSOLIDATION_FINGERPRINT_PROPERTY) == fingerprint
    ):
        raise CreationRecoveryPendingError(
            "정리 후보 파일의 이름, 소유자, 종류 또는 정리 표식을 확인하지 못했어요. "
            "현재 출석부와 후보를 바꾸거나 지우지 않았습니다."
        )
    return checked


def create_canonical_attendance_workbook(
    runner: CommandRunner,
    workdir: Path,
    *,
    profile: dict,
    workbook_name: str,
    intent: str,
    creation_reason: str,
    source_spreadsheet_id: str = "",
    consolidation_fingerprint: str = "",
    existing_sheet: dict | None = None,
    dry_run: bool,
    gws_executable: str,
) -> dict:
    """승인된 세 경우에만 정식 출결 Google Sheet를 만드는 유일한 문."""

    creation_reason = _require_attendance_creation_reason(creation_reason)
    dry = ["--dry-run"] if dry_run else []
    source_id = str(source_spreadsheet_id or "").strip()
    if creation_reason == ATTENDANCE_CREATION_SPLIT_REPAIR and not source_id:
        raise ValueError("두 갈래 정리 원본 출결 시트 번호가 없습니다.")
    candidate_name = (
        _consolidation_candidate_name(workbook_name, consolidation_fingerprint)
        if creation_reason == ATTENDANCE_CREATION_SPLIT_REPAIR
        else workbook_name
    )
    if existing_sheet is not None:
        sheet = dict(existing_sheet)
    else:
        create_args = [
            gws_executable,
            "drive",
            "files",
            "create",
            *dry,
            "--json",
            json.dumps(
                {
                    "name": candidate_name,
                    "mimeType": SPREADSHEET_MIME,
                    "appProperties": {
                        _DRIVE_INTENT_PROPERTY: intent,
                        **(
                            {
                                _CONSOLIDATION_FINGERPRINT_PROPERTY: (
                                    consolidation_fingerprint
                                )
                            }
                            if creation_reason == ATTENDANCE_CREATION_SPLIT_REPAIR
                            else {}
                        ),
                        **attendance_workbook_identity.attendance_workbook_app_properties(
                            profile
                        ),
                    },
                },
                ensure_ascii=False,
            ),
        ]
        create_args.extend(
            [
                "--upload",
                ".\\attendance-workbook.xlsx",
                "--upload-content-type",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ]
        )
        create_args.extend(["--format", "json"])
        sheet = run_json(
            runner,
            create_args,
            workdir,
        )
        sheet = with_dry_run_fallback(
            sheet,
            {
                "id": "dry-run-spreadsheet-id",
                "webViewLink": (
                    "https://docs.google.com/spreadsheets/d/dry-run-spreadsheet-id/edit"
                ),
            },
            dry_run,
        )
    return sheet

    destination_id = str(sheet.get("id", "") or "").strip()
    if not destination_id:
        raise CreationRecoveryPendingError(
            "정리 후보 시트 번호를 확인하지 못해 원본 탭을 복사하지 않았어요."
        )

    def read_sheet_properties(spreadsheet_id: str) -> list[dict]:
        response = run_json(
            runner,
            [
                gws_executable,
                "sheets",
                "spreadsheets",
                "get",
                "--params",
                json.dumps({"spreadsheetId": spreadsheet_id}, ensure_ascii=False),
                "--format",
                "json",
            ],
            workdir,
        )
        raw = response.get("sheets") if isinstance(response, dict) else None
        if not isinstance(raw, list) or not raw:
            raise CreationRecoveryPendingError(
                "출결 시트 탭 목록을 안전하게 확인하지 못했어요."
            )
        properties: list[dict] = []
        for item in raw:
            value = item.get("properties") if isinstance(item, dict) else None
            sheet_id = value.get("sheetId") if isinstance(value, dict) else None
            title = str(value.get("title", "") or "").strip() if isinstance(value, dict) else ""
            if not isinstance(sheet_id, int) or isinstance(sheet_id, bool) or not title:
                raise CreationRecoveryPendingError(
                    "출결 시트 탭 목록을 안전하게 확인하지 못했어요."
                )
            properties.append(dict(value))
        if len({item["title"] for item in properties}) != len(properties):
            raise CreationRecoveryPendingError(
                "같은 이름의 출결 탭이 여러 개 보여 자동으로 복사하지 않았어요."
            )
        return sorted(properties, key=lambda item: int(item.get("index", 0)))

    def copied_shape(properties: dict) -> dict:
        return {
            "title": properties.get("title"),
            "hidden": properties.get("hidden") is True,
            "sheetType": str(properties.get("sheetType", "GRID") or "GRID"),
            "gridProperties": dict(properties.get("gridProperties") or {}),
        }

    def copied_content_shape(properties: dict) -> dict:
        """제목을 빼고, 복사된 탭이 원본과 같은 탭인지 확인한다."""

        shape = copied_shape(properties)
        shape.pop("title", None)
        return shape

    def rename_destination_sheet(sheet_id: int, title: str) -> None:
        run_json(
            runner,
            [
                gws_executable,
                "sheets",
                "spreadsheets",
                "batchUpdate",
                "--params",
                json.dumps({"spreadsheetId": destination_id}, ensure_ascii=False),
                "--json",
                json.dumps(
                    {
                        "requests": [
                            {
                                "updateSheetProperties": {
                                    "properties": {
                                        "sheetId": sheet_id,
                                        "title": title,
                                    },
                                    "fields": "title",
                                }
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                "--format",
                "json",
            ],
            workdir,
        )

    source_sheets = read_sheet_properties(source_id)
    source_titles = [item["title"] for item in source_sheets]
    safe_intent = "".join(character for character in intent if character.isalnum())[-24:]
    reserved_title = f"__TM_EMPTY_{safe_intent}__"
    if reserved_title in source_titles:
        raise CreationRecoveryPendingError(
            "원본에 설치용 빈 탭 표식과 같은 이름이 있어 자동으로 복사하지 않았어요."
        )

    destination_sheets = read_sheet_properties(destination_id)
    reserved = [
        item for item in destination_sheets if item["title"] == reserved_title
    ]
    if len(reserved) > 1:
        raise CreationRecoveryPendingError(
            "정리 후보의 설치용 빈 탭이 여러 개라 자동으로 이어가지 않았어요."
        )
    if not reserved:
        present_titles = [item["title"] for item in destination_sheets]
        already_complete = (
            present_titles == source_titles
            and [copied_shape(item) for item in destination_sheets]
            == [copied_shape(item) for item in source_sheets]
        )
        if already_complete:
            return sheet
        # 생성 직후의 탭 한 개만 이 이름으로 바꾼다. 이 표식이 생기기 전에는
        # 원본 탭 복사를 시작하지 않으므로, 재시도에서 지워도 되는 탭이 분명하다.
        if len(destination_sheets) != 1:
            raise CreationRecoveryPendingError(
                "정리 후보의 빈 탭을 분명하게 찾지 못해 자동으로 이어가지 않았어요."
            )
        initial = destination_sheets[0]
        rename_destination_sheet(initial["sheetId"], reserved_title)
        destination_sheets = read_sheet_properties(destination_id)
        reserved = [
            item for item in destination_sheets if item["title"] == reserved_title
        ]
        if len(reserved) != 1:
            raise CreationRecoveryPendingError(
                "정리 후보의 설치용 빈 탭 표식을 다시 확인하지 못했어요."
            )

    # Google Sheets의 copyTo는 다른 파일로 옮긴 탭을 `Copy of 원래제목`으로
    # 먼저 만든다. 이전 코드는 제목이 그대로 복사된다고 잘못 가정해 후보 파일에
    # 이 임시 제목들을 남겼다. 현재 후보에 그런 탭이 있으면 모양을 전부 확인한
    # 뒤에만 원래 제목으로 되돌린다. 이 과정은 기존 원본에는 손대지 않는다.
    destination_sheets = read_sheet_properties(destination_id)
    pending_renames: list[tuple[int, str]] = []
    for source_sheet in source_sheets:
        source_title = source_sheet["title"]
        copied_title = f"Copy of {source_title}"
        exact_matches = [
            item for item in destination_sheets if item["title"] == source_title
        ]
        copied_matches = [
            item for item in destination_sheets if item["title"] == copied_title
        ]
        if len(exact_matches) > 1 or len(copied_matches) > 1:
            raise CreationRecoveryPendingError(
                f"정리 후보에 `{source_title}` 탭이 여러 개라 자동으로 이어가지 않았어요."
            )
        if exact_matches and copied_matches:
            raise CreationRecoveryPendingError(
                f"정리 후보에 `{source_title}` 탭과 복사 중인 탭이 함께 있어 자동으로 이어가지 않았어요."
            )
        if not exact_matches and copied_matches:
            copied_match = copied_matches[0]
            if copied_content_shape(copied_match) != copied_content_shape(source_sheet):
                raise CreationRecoveryPendingError(
                    f"정리 후보의 `{source_title}` 복사 탭 모양이 원본과 달라 연결을 바꾸지 않았어요."
                )
            pending_renames.append((copied_match["sheetId"], source_title))

    for copied_sheet_id, source_title in pending_renames:
        rename_destination_sheet(copied_sheet_id, source_title)

    for source_sheet in source_sheets:
        destination_sheets = read_sheet_properties(destination_id)
        matches = [
            item
            for item in destination_sheets
            if item["title"] == source_sheet["title"]
        ]
        if len(matches) > 1:
            raise CreationRecoveryPendingError(
                f"정리 후보에 `{source_sheet['title']}` 탭이 여러 개라 자동으로 이어가지 않았어요."
            )
        if matches:
            if copied_shape(matches[0]) != copied_shape(source_sheet):
                raise CreationRecoveryPendingError(
                    f"정리 후보의 `{source_sheet['title']}` 탭 모양이 원본과 달라 연결을 바꾸지 않았어요."
                )
            continue
        copied = run_json(
            runner,
            [
                gws_executable,
                "sheets",
                "spreadsheets",
                "sheets",
                "copyTo",
                "--params",
                json.dumps(
                    {
                        "spreadsheetId": source_id,
                        "sheetId": source_sheet["sheetId"],
                    },
                    ensure_ascii=False,
                ),
                "--json",
                json.dumps(
                    {"destinationSpreadsheetId": destination_id},
                    ensure_ascii=False,
                ),
                "--format",
                "json",
            ],
            workdir,
        )
        copied_sheet_id = copied.get("sheetId") if isinstance(copied, dict) else None
        if not isinstance(copied_sheet_id, int) or isinstance(copied_sheet_id, bool):
            raise CreationRecoveryPendingError(
                f"정리 후보에 복사한 `{source_sheet['title']}` 탭 번호를 확인하지 못했어요. 다시 누르면 이어서 확인합니다."
            )
        rename_destination_sheet(copied_sheet_id, source_sheet["title"])

    destination_sheets = read_sheet_properties(destination_id)
    allowed_titles = set(source_titles) | {reserved_title}
    unexpected = [
        item["title"] for item in destination_sheets
        if item["title"] not in allowed_titles
    ]
    if unexpected:
        raise CreationRecoveryPendingError(
            "정리 후보에 원본에 없는 탭이 있어 자동으로 지우지 않았어요: "
            + ", ".join(unexpected)
        )
    reserved = [
        item for item in destination_sheets if item["title"] == reserved_title
    ]
    if len(reserved) != 1:
        raise CreationRecoveryPendingError(
            "정리 후보의 설치용 빈 탭을 마지막에 한 개로 확인하지 못했어요."
        )
    run_json(
        runner,
        [
            gws_executable,
            "sheets",
            "spreadsheets",
            "batchUpdate",
            "--params",
            json.dumps({"spreadsheetId": destination_id}, ensure_ascii=False),
            "--json",
            json.dumps(
                {
                    "requests": [
                        {"deleteSheet": {"sheetId": item["sheetId"]}}
                        for item in reserved
                    ]
                },
                ensure_ascii=False,
            ),
            "--format",
            "json",
        ],
        workdir,
    )
    copied_sheets = read_sheet_properties(destination_id)
    copied_titles = [item["title"] for item in copied_sheets]
    if copied_titles != source_titles:
        raise CreationRecoveryPendingError(
            "정리 후보의 탭 이름과 순서가 원본과 달라 연결을 바꾸지 않았어요."
        )
    if [copied_shape(item) for item in copied_sheets] != [
        copied_shape(item) for item in source_sheets
    ]:
        raise CreationRecoveryPendingError(
            "정리 후보의 탭 모양이 원본과 달라 연결을 바꾸지 않았어요."
        )
    return sheet


def merge_attendance_config_rows(
    existing_rows: Sequence[Sequence[Any]],
    desired_rows: Sequence[Sequence[Any]],
    *,
    overwrite_keys: set[str] | frozenset[str],
) -> list[list[str]]:
    """사용자 행은 보존하고 연결에 필요한 설정값만 새 후보 값으로 바꾼다."""

    def normalized(row: Sequence[Any]) -> list[str]:
        values = [str(value if value is not None else "") for value in row[:4]]
        return values + [""] * (4 - len(values))

    desired = [normalized(row) for row in desired_rows]
    if not desired or desired[0][0].strip() != "설정키":
        raise ValueError("새 출결 설정의 제목 줄을 확인하지 못했어요.")
    desired_by_key: dict[str, list[str]] = {}
    for row in desired[1:]:
        key = row[0].strip()
        if not key or key in desired_by_key:
            raise ValueError("새 출결 설정에 빈 키나 중복 키가 있어 적용하지 않았어요.")
        desired_by_key[key] = row

    existing = [normalized(row) for row in existing_rows]
    header = existing[0] if existing and existing[0][0].strip() == "설정키" else desired[0]
    body = existing[1:] if existing and existing[0][0].strip() == "설정키" else existing
    merged: list[list[str]] = [header]
    seen: set[str] = set()
    for row in body:
        key = row[0].strip()
        if key:
            if key in seen:
                raise ValueError(f"출결 설정에 같은 키가 두 번 있습니다: {key}")
            seen.add(key)
        if key in overwrite_keys and key in desired_by_key:
            merged.append(list(desired_by_key[key]))
        else:
            merged.append(row)
    for row in desired[1:]:
        key = row[0].strip()
        if key not in seen:
            merged.append(list(row))
    return merged


def _restore_candidate_student_dropdowns(
    runner: CommandRunner,
    workdir: Path,
    *,
    spreadsheet_id: str,
    sheet_info: dict,
    config_rows: Sequence[Sequence[Any]],
    gws_executable: str,
) -> None:
    """새 정식 후보의 월별 학생 선택 규칙을 새 학생명단에 다시 연결한다."""

    settings = {
        str(row[0]).strip(): str(row[1]).strip()
        for row in config_rows
        if len(row) >= 2 and str(row[0]).strip()
    }
    roster_title = settings.get("ROSTER_SHEET_NAME", "학생명단") or "학생명단"
    dropdown_range = (
        settings.get("STUDENT_DROPDOWN_RANGE", "C2:C200") or "C2:C200"
    )
    range_match = re.fullmatch(
        r"\$?([A-Z]+)\$?(\d+):\$?([A-Z]+)\$?(\d+)",
        dropdown_range,
        flags=re.IGNORECASE,
    )
    if not range_match:
        raise CreationRecoveryPendingError(
            "새 정식 출석부 후보의 학생 선택 범위를 확인하지 못했어요."
        )
    start_col, start_row, end_col, end_row = range_match.groups()
    if start_col.upper() == "A" and end_col.upper() == "A":
        start_col = end_col = "C"
    formula = (
        f"='{roster_title}'!${start_col.upper()}${start_row}:"
        f"${end_col.upper()}${end_row}"
    )

    properties = [
        dict(item.get("properties") or {})
        for item in (sheet_info.get("sheets") or [])
        if isinstance(item, dict)
    ]
    titles = {str(item.get("title", "") or "") for item in properties}
    if roster_title not in titles:
        raise CreationRecoveryPendingError(
            "새 정식 출석부 후보에서 학생명단 탭을 확인하지 못했어요."
        )

    configured_months = [
        value.strip()
        for value in settings.get("MONTH_SHEET_NAMES", "").split(",")
        if value.strip()
    ]
    month_properties = [
        item for item in properties if str(item.get("title", "") or "") in configured_months
    ]
    if not month_properties:
        raise CreationRecoveryPendingError(
            "새 정식 출석부 후보에서 월별 출결 탭을 확인하지 못했어요."
        )

    requests = []
    for item in month_properties:
        sheet_id = item.get("sheetId")
        row_count = (item.get("gridProperties") or {}).get("rowCount")
        if (
            not isinstance(sheet_id, int)
            or isinstance(sheet_id, bool)
            or not isinstance(row_count, int)
            or isinstance(row_count, bool)
            or row_count < 3
        ):
            raise CreationRecoveryPendingError(
                "새 정식 출석부 후보의 월별 입력 범위를 확인하지 못했어요."
            )
        requests.append(
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 2,
                        "endRowIndex": row_count,
                        "startColumnIndex": 1,
                        "endColumnIndex": 2,
                    },
                    "rule": {
                        "condition": {
                            "type": "ONE_OF_RANGE",
                            "values": [{"userEnteredValue": formula}],
                        },
                        "strict": False,
                        "showCustomUi": True,
                    },
                }
            }
        )

    run_json(
        runner,
        [
            gws_executable,
            "sheets",
            "spreadsheets",
            "batchUpdate",
            "--params",
            json.dumps({"spreadsheetId": spreadsheet_id}, ensure_ascii=False),
            "--json",
            json.dumps({"requests": requests}, ensure_ascii=False),
            "--format",
            "json",
        ],
        workdir,
    )

    expected_rows = {
        str(item["title"]): int((item.get("gridProperties") or {})["rowCount"]) - 2
        for item in month_properties
    }
    checked = run_json(
        runner,
        [
            gws_executable,
            "sheets",
            "spreadsheets",
            "get",
            "--params",
            json.dumps(
                {
                    "spreadsheetId": spreadsheet_id,
                    "includeGridData": True,
                    "ranges": [
                        f"'{title}'!B3:B{row_count + 2}"
                        for title, row_count in expected_rows.items()
                    ],
                    "fields": (
                        "sheets(properties(title,gridProperties(rowCount)),"
                        "data(startRow,startColumn,rowData(values(dataValidation))))"
                    ),
                },
                ensure_ascii=False,
            ),
            "--format",
            "json",
        ],
        workdir,
    )
    checked_sheets = {
        str((item.get("properties") or {}).get("title", "") or ""): item
        for item in (checked.get("sheets") or [])
        if isinstance(item, dict)
    }
    for title, row_count in expected_rows.items():
        item = checked_sheets.get(title)
        data_blocks = item.get("data") if isinstance(item, dict) else None
        row_data = []
        for block in data_blocks or []:
            if isinstance(block, dict):
                row_data.extend(block.get("rowData") or [])
        if len(row_data) != row_count:
            raise CreationRecoveryPendingError(
                f"새 정식 출석부 후보의 `{title}` 학생 선택 연결을 끝까지 확인하지 못했어요."
            )
        for row in row_data:
            values = row.get("values") if isinstance(row, dict) else None
            validation = (
                (values[0].get("dataValidation") or {})
                if isinstance(values, list) and values and isinstance(values[0], dict)
                else {}
            )
            condition = validation.get("condition") or {}
            formula_values = condition.get("values") or []
            found_formula = (
                str(formula_values[0].get("userEnteredValue", "") or "")
                if formula_values and isinstance(formula_values[0], dict)
                else ""
            )
            if condition.get("type") != "ONE_OF_RANGE" or found_formula != formula:
                raise CreationRecoveryPendingError(
                    f"새 정식 출석부 후보의 `{title}` 학생 선택 연결이 끊겨 있어 현재 출석부를 바꾸지 않았어요."
                )


_AUTHORITATIVE_TAB_RANGES = (
    ("학생명단!A1:D", "학생명단!A:D", 4),
    ("휴일!A1:F", "휴일!A:F", 6),
)


def _read_authoritative_tab_rows(
    runner: CommandRunner,
    workdir: Path,
    *,
    spreadsheet_id: str,
    range_name: str,
    column_count: int,
    gws_executable: str,
) -> tuple[tuple[Any, ...], ...]:
    reply = run_json(
        runner,
        [
            gws_executable,
            "sheets",
            "spreadsheets",
            "values",
            "get",
            "--params",
            json.dumps(
                {
                    "spreadsheetId": spreadsheet_id,
                    "range": range_name,
                    "majorDimension": "ROWS",
                    "valueRenderOption": "UNFORMATTED_VALUE",
                    "dateTimeRenderOption": "SERIAL_NUMBER",
                },
                ensure_ascii=False,
            ),
            "--format",
            "json",
        ],
        workdir,
    )
    values = reply.get("values") if isinstance(reply, dict) else None
    if not isinstance(values, list):
        raise CreationRecoveryPendingError(
            "현재 출석부의 학생명단과 휴일을 안전하게 읽지 못했어요."
        )
    normalized: list[tuple[Any, ...]] = []
    for row in values:
        if not isinstance(row, list) or len(row) > column_count or any(
            isinstance(cell, (dict, list)) for cell in row
        ):
            raise CreationRecoveryPendingError(
                "현재 출석부의 학생명단과 휴일 값 모양을 확인하지 못했어요."
            )
        normalized.append(
            tuple("" if cell is None else cell for cell in row)
            + ("",) * (column_count - len(row))
        )
    if not normalized or not any(cell != "" for cell in normalized[0]):
        raise CreationRecoveryPendingError(
            "현재 출석부의 학생명단과 휴일 제목 줄을 확인하지 못했어요."
        )
    return tuple(normalized)


def _replace_authoritative_tab_rows(
    runner: CommandRunner,
    workdir: Path,
    *,
    spreadsheet_id: str,
    range_name: str,
    clear_range: str,
    rows: tuple[tuple[Any, ...], ...],
    gws_executable: str,
) -> None:
    run_json(
        runner,
        [
            gws_executable,
            "sheets",
            "spreadsheets",
            "values",
            "clear",
            "--params",
            json.dumps(
                {"spreadsheetId": spreadsheet_id, "range": clear_range},
                ensure_ascii=False,
            ),
            "--format",
            "json",
        ],
        workdir,
    )
    range_start, end_column = range_name.rsplit(":", 1)
    exact_range = f"{range_start}:{end_column}{len(rows)}"
    updated = run_json(
        runner,
        [
            gws_executable,
            "sheets",
            "spreadsheets",
            "values",
            "update",
            "--params",
            json.dumps(
                {
                    "spreadsheetId": spreadsheet_id,
                    "range": exact_range,
                    "valueInputOption": "RAW",
                },
                ensure_ascii=False,
            ),
            "--json",
            json.dumps(
                {"majorDimension": "ROWS", "values": [list(row) for row in rows]},
                ensure_ascii=False,
            ),
            "--format",
            "json",
        ],
        workdir,
    )
    if not isinstance(updated, dict) or updated.get("updatedRange") != exact_range:
        raise CreationRecoveryPendingError(
            "새 출석부 후보에 학생명단과 휴일을 쓴 범위를 확인하지 못했어요."
        )
    checked = _read_authoritative_tab_rows(
        runner,
        workdir,
        spreadsheet_id=spreadsheet_id,
        range_name=range_name,
        column_count=len(rows[0]),
        gws_executable=gws_executable,
    )
    if checked != rows:
        raise CreationRecoveryPendingError(
            "새 출석부 후보의 학생명단과 휴일을 다시 읽은 값이 원본과 다릅니다."
        )


def install_attendance_automation(
    profile_json: Path,
    runner: CommandRunner = default_runner,
    dry_run: bool = False,
    attendance_task_list_title: str = "출결 미제출 확인",
    attendance_task_list_id: str = "",
    central_chat_sender_url: str = "",
    resume: dict | None = None,
    progress: Callable[[dict], None] | None = None,
    gemini_api_key: str = "",
    *,
    gws_executable: str,
    creation_reason: str = ATTENDANCE_CREATION_FIRST_SETUP,
    source_spreadsheet_id: str = "",
    consolidation_fingerprint: str = "",
    write_record_on_success: bool = True,
) -> AttendanceInstallResult:
    creation_reason = _require_attendance_creation_reason(creation_reason)
    source_spreadsheet_id = str(source_spreadsheet_id or "").strip()
    if (
        creation_reason == ATTENDANCE_CREATION_SPLIT_REPAIR
        and not source_spreadsheet_id
    ):
        raise ValueError("두 갈래 정리 원본 출결 시트 번호가 없습니다.")
    profile_json = Path(profile_json)
    asset_root = bundle_paths.bundle_root() / "assets"
    profile = load_profile(profile_json)
    workbook_name = attendance_workbook_name(profile)
    candidate_workbook_name = (
        _consolidation_candidate_name(workbook_name, consolidation_fingerprint)
        if creation_reason == ATTENDANCE_CREATION_SPLIT_REPAIR
        else workbook_name
    )
    if creation_reason == ATTENDANCE_CREATION_SPLIT_REPAIR:
        write_record_on_success = False

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
        source_setting_rows: list[list[str]] = []
        source_settings: dict[str, str] = {}
        authoritative_tabs: dict[str, tuple[tuple[Any, ...], ...]] = {}
        if creation_reason == ATTENDANCE_CREATION_SPLIT_REPAIR:
            source_setting_rows = _read_existing_setting_rows(
                runner,
                workdir,
                source_spreadsheet_id,
                gws_executable,
            )
            for row in source_setting_rows:
                if len(row) >= 2:
                    key = str(row[0]).strip()
                    if key and key not in source_settings:
                        source_settings[key] = str(row[1]).strip()
            required_source_values = {
                "template_doc_id": "TEMPLATE_DOC_ID",
                "folder_id": "DEST_FOLDER_ID",
                "task_list_id": "TASK_LIST_ID",
            }
            missing_source_values = [
                key
                for key in required_source_values.values()
                if not source_settings.get(key)
            ]
            if missing_source_values:
                raise ExistingAttendanceSheetError(
                    "정리 원본의 설정 탭에 이어 쓸 연결값이 없습니다: "
                    + ", ".join(missing_source_values)
                )
            for result_key, setting_key in required_source_values.items():
                created_ids.setdefault(result_key, source_settings[setting_key])
            created_ids.setdefault(
                "template_doc_url",
                "https://docs.google.com/document/d/"
                + created_ids["template_doc_id"]
                + "/edit",
            )
            source_task_title = str(
                source_settings.get("TASK_LIST_TITLE", "") or ""
            ).strip()
            if source_task_title:
                attendance_task_list_title = source_task_title
            for range_name, _clear_range, column_count in _AUTHORITATIVE_TAB_RANGES:
                authoritative_tabs[range_name] = _read_authoritative_tab_rows(
                    runner,
                    workdir,
                    spreadsheet_id=source_spreadsheet_id,
                    range_name=range_name,
                    column_count=column_count,
                    gws_executable=gws_executable,
                )

        # 설치 기록이 없는 컴퓨터만 정식 Drive 표식으로 되찾는다. 이름은 사용자가
        # 바꿀 수 있으므로 연결 근거로 쓰지 않는다. 예전 이름만 있으면 새 파일을
        # 만들지 않고 사용자가 확인하는 한 번의 정리 절차로 넘긴다.
        if (
            creation_reason == ATTENDANCE_CREATION_FIRST_SETUP
            and
            not created_ids.get("spreadsheet_id")
            and not created_ids.get(_PENDING_SHEET_INTENT)
        ):
            school = profile.get("school") or {}
            school_year = (
                str(school.get("year", "") or "").strip() or current_school_year()
            )
            existing = find_canonical_attendance_sheets(
                runner, workdir, dry_run, school_year, gws_executable
            )
            if len(existing) > 1:
                raise ExistingAttendanceSheetError(_too_many_sheets_message(existing, workbook_name))
            if len(existing) == 1:
                existing_name = str(existing[0].get("name", "") or "").strip()
                if existing_name != workbook_name:
                    raise LegacyAttendanceConsolidationRequired(existing)
                reused = reuse_existing_attendance_sheet(
                    runner, workdir, existing[0], existing_name, gws_executable
                )
                created_ids.update(
                    {
                        "spreadsheet_id": reused.spreadsheet_id,
                        "spreadsheet_url": reused.spreadsheet_url,
                        "template_doc_id": reused.template_doc_id,
                        "template_doc_url": reused.template_doc_url,
                        "script_id": reused.script_id,
                        "deployment_id": reused.deployment_id,
                        "folder_id": reused.folder_id,
                        "task_list_id": reused.task_list_id,
                    }
                )
                # 여기서는 Google 자료를 새로 만든 것이 없다. 이 값을 "만들다 만
                # 진행 기록"으로 남기면 로컬 기록 저장 실패 뒤 재시도에서 기존
                # Sheet의 설정을 새 설치값으로 덮어쓸 수 있으므로 기록하지 않는다.
                return reused
            legacy = find_legacy_attendance_sheets(
                runner,
                workdir,
                dry_run,
                (
                    ATTENDANCE_LEGACY_SHEET_NAME,
                    attendance_workbook_identity.legacy_year_workbook_name(profile),
                    attendance_workbook_identity.previous_attendance_workbook_name(
                        profile
                    ),
                    workbook_name,
                ),
                gws_executable,
            )
            if legacy:
                raise LegacyAttendanceConsolidationRequired(legacy)

        if not created_ids.get("template_doc_id"):
            intent = str(created_ids.get(_PENDING_TEMPLATE_INTENT, "") or "")
            if intent:
                intent = _checked_drive_intent(intent, "template", "결석신고서 템플릿")
                template = _recover_drive_resource(
                    runner,
                    workdir,
                    intent=intent,
                    name="결석신고서 템플릿",
                    mime_type="application/vnd.google-apps.document",
                    label="결석신고서 템플릿",
                    gws_executable=gws_executable,
                )
            else:
                intent = _new_drive_intent("template")
                created_ids[_PENDING_TEMPLATE_INTENT] = intent
                report_progress()
                template = run_json(
                    runner,
                    [
                        gws_executable,
                        "drive",
                        "files",
                        "create",
                        *dry,
                        "--json",
                        json.dumps(
                            {
                                "name": "결석신고서 템플릿",
                                "mimeType": "application/vnd.google-apps.document",
                                "appProperties": {_DRIVE_INTENT_PROPERTY: intent},
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
            template_id = str(template.get("id", "") or "").strip()
            if not template_id:
                raise CreationRecoveryPendingError(
                    "결석신고서 템플릿 번호를 확인하지 못했어요. 새로 만들지 않습니다."
                )
            created_ids["template_doc_id"] = template_id
            created_ids["template_doc_url"] = template.get(
                "webViewLink", f"https://docs.google.com/document/d/{template_id}/edit"
            )
            created_ids.pop(_PENDING_TEMPLATE_INTENT, None)
            report_progress()
        elif not created_ids.get("template_doc_url"):
            created_ids["template_doc_url"] = (
                f"https://docs.google.com/document/d/{created_ids['template_doc_id']}/edit"
            )
        if not created_ids.get("spreadsheet_id"):
            intent = str(created_ids.get(_PENDING_SHEET_INTENT, "") or "")
            if intent:
                intent = _checked_drive_intent(intent, "sheet", "출결 시트")
                sheet = _recover_drive_resource(
                    runner,
                    workdir,
                    intent=intent,
                    name=candidate_workbook_name,
                    mime_type=SPREADSHEET_MIME,
                    label="출결 시트",
                    gws_executable=gws_executable,
                )
                if creation_reason == ATTENDANCE_CREATION_SPLIT_REPAIR:
                    # 후보 파일 만들기 응답이나 탭 복사 응답이 끊긴 재시도다.
                    # 같은 설치표식 후보 안에서 이미 복사된 탭을 확인하고 빠진 탭만
                    # 이어 간다. 여기서 새 Google Sheet를 다시 만들지는 않는다.
                    sheet = create_canonical_attendance_workbook(
                        runner,
                        workdir,
                        profile=profile,
                        workbook_name=workbook_name,
                        intent=intent,
                        creation_reason=creation_reason,
                        source_spreadsheet_id=source_spreadsheet_id,
                        consolidation_fingerprint=consolidation_fingerprint,
                        existing_sheet=sheet,
                        dry_run=dry_run,
                        gws_executable=gws_executable,
                    )
            else:
                intent = _new_drive_intent("sheet")
                created_ids[_PENDING_SHEET_INTENT] = intent
                report_progress()
                sheet = create_canonical_attendance_workbook(
                    runner,
                    workdir,
                    profile=profile,
                    workbook_name=workbook_name,
                    intent=intent,
                    creation_reason=creation_reason,
                    source_spreadsheet_id=source_spreadsheet_id,
                    consolidation_fingerprint=consolidation_fingerprint,
                    dry_run=dry_run,
                    gws_executable=gws_executable,
                )
            spreadsheet_id = str(sheet.get("id", "") or "").strip()
            if not spreadsheet_id:
                raise CreationRecoveryPendingError(
                    "출결 시트 번호를 확인하지 못했어요. 새 시트는 만들지 않습니다."
                )
            created_ids["spreadsheet_id"] = spreadsheet_id
            created_ids["spreadsheet_url"] = sheet.get(
                "webViewLink", f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
            )
            created_ids.pop(_PENDING_SHEET_INTENT, None)
            report_progress()
        elif not created_ids.get("spreadsheet_url"):
            created_ids["spreadsheet_url"] = (
                f"https://docs.google.com/spreadsheets/d/{created_ids['spreadsheet_id']}/edit"
            )
        if creation_reason == ATTENDANCE_CREATION_SPLIT_REPAIR:
            verified_candidate = _verify_consolidation_candidate_identity(
                runner,
                workdir,
                candidate_spreadsheet_id=created_ids["spreadsheet_id"],
                source_spreadsheet_id=source_spreadsheet_id,
                expected_name=candidate_workbook_name,
                fingerprint=consolidation_fingerprint,
                gws_executable=gws_executable,
            )
            verified_url = str(verified_candidate.get("webViewLink", "") or "").strip()
            if verified_url:
                created_ids["spreadsheet_url"] = verified_url
            for range_name, clear_range, _column_count in _AUTHORITATIVE_TAB_RANGES:
                _replace_authoritative_tab_rows(
                    runner,
                    workdir,
                    spreadsheet_id=created_ids["spreadsheet_id"],
                    range_name=range_name,
                    clear_range=clear_range,
                    rows=authoritative_tabs[range_name],
                    gws_executable=gws_executable,
                )
        if not created_ids.get("folder_id"):
            intent = str(created_ids.get(_PENDING_FOLDER_INTENT, "") or "")
            if intent:
                intent = _checked_drive_intent(intent, "folder", "출결 증빙 폴더")
                folder = _recover_drive_resource(
                    runner,
                    workdir,
                    intent=intent,
                    name="출결 증빙",
                    mime_type="application/vnd.google-apps.folder",
                    label="출결 증빙 폴더",
                    gws_executable=gws_executable,
                )
            else:
                intent = _new_drive_intent("folder")
                created_ids[_PENDING_FOLDER_INTENT] = intent
                report_progress()
                folder = run_json(
                    runner,
                    [
                        gws_executable,
                        "drive",
                        "files",
                        "create",
                        *dry,
                        "--json",
                        json.dumps(
                            {
                                "name": "출결 증빙",
                                "mimeType": "application/vnd.google-apps.folder",
                                "appProperties": {_DRIVE_INTENT_PROPERTY: intent},
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
            folder_id = str(folder.get("id", "") or "").strip()
            if not folder_id:
                raise CreationRecoveryPendingError(
                    "출결 증빙 폴더 번호를 확인하지 못했어요. 새 폴더는 만들지 않습니다."
                )
            created_ids["folder_id"] = folder_id
            created_ids.pop(_PENDING_FOLDER_INTENT, None)
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
                matches = _exact_task_lists(
                    _task_lists_all(runner, workdir, gws_executable),
                    task_list_title,
                )
                pending_task_title = str(
                    created_ids.get(_PENDING_TASK_TITLE, "") or ""
                )
                if pending_task_title and pending_task_title != task_list_title:
                    raise CreationRecoveryPendingError(
                        "앞선 Google Tasks 목록 이름이 지금 설정과 달라 새 목록을 만들지 않았어요."
                    )
                if len(matches) > 1:
                    raise CreationRecoveryPendingError(
                        "같은 이름의 Google Tasks 목록이 여러 개 있어 자동으로 고르지 않았어요. "
                        "새 목록도 만들지 않았습니다."
                    )
                if len(matches) == 1:
                    task_list = matches[0]
                elif pending_task_title:
                    raise CreationRecoveryPendingError(
                        "앞선 Google Tasks 목록 만들기 결과를 아직 확인하지 못했어요. "
                        "중복 목록을 막기 위해 새로 만들지 않았습니다."
                    )
        if not task_list:
            if not dry_run:
                created_ids[_PENDING_TASK_TITLE] = task_list_title
                report_progress()
            task_list = run_json(
                runner,
                [
                    gws_executable,
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
        task_list_result_id = str(task_list.get("id", "") or "").strip()
        if not task_list_result_id:
            raise CreationRecoveryPendingError(
                "Google Tasks 목록 번호를 확인하지 못했어요. 새 목록은 만들지 않습니다."
            )
        if created_ids.get("task_list_id") != task_list_result_id:
            created_ids["task_list_id"] = task_list_result_id
            created_ids.pop(_PENDING_TASK_TITLE, None)
            report_progress()
        if not created_ids.get("script_id"):
            pending_title = str(created_ids.get(_PENDING_SCRIPT_TITLE, "") or "")
            if pending_title:
                suffix = _pending_script_title_suffix(pending_title)
                if not suffix:
                    raise CreationRecoveryPendingError(
                        "앞선 Apps Script 프로젝트 만들기 기록을 안전하게 확인할 수 없어요."
                    )
                _intent_token(suffix, "", "Apps Script 프로젝트")
                script_id = _recover_script_project(
                    runner,
                    workdir,
                    title=pending_title,
                    spreadsheet_id=created_ids["spreadsheet_id"],
                    gws_executable=gws_executable,
                )
            else:
                pending_title = _SCRIPT_TITLE_PREFIX + secrets.token_hex(16) + "]"
                created_ids[_PENDING_SCRIPT_TITLE] = pending_title
                report_progress()
                script = run_json(
                    runner,
                    [
                        gws_executable,
                        "script",
                        "projects",
                        "create",
                        *dry,
                        "--json",
                        json.dumps(
                            {"title": pending_title, "parentId": created_ids["spreadsheet_id"]},
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
                script_id = str(script.get("scriptId", "") or "").strip()
            if not script_id:
                raise CreationRecoveryPendingError(
                    "Apps Script 프로젝트 번호를 확인하지 못했어요. 새 프로젝트는 만들지 않습니다."
                )
            created_ids["script_id"] = script_id
            created_ids.pop(_PENDING_SCRIPT_TITLE, None)
            report_progress()
        if not created_ids.get("deployment_id"):
            pending = _pending_deployment_identity(created_ids)
            if pending is not None:
                description, version_number = pending
                created_ids["deployment_id"] = _recover_pending_deployment(
                    runner,
                    workdir,
                    created_ids["script_id"],
                    description,
                    version_number,
                    gws_executable,
                )
            else:
                expected_bundle_sha256 = attendance_script_update.target_bundle_sha256(
                    asset_root
                )
                pending_version_description = str(
                    created_ids.get(_PENDING_SCRIPT_VERSION_DESCRIPTION, "") or ""
                )
                if pending_version_description:
                    pending_version_description = _intent_token(
                        pending_version_description,
                        _VERSION_DESCRIPTION_PREFIX,
                        "Apps Script 버전",
                    )
                    version_number = _recover_script_version(
                        runner,
                        workdir,
                        script_id=created_ids["script_id"],
                        description=pending_version_description,
                        expected_bundle_sha256=expected_bundle_sha256,
                        gws_executable=gws_executable,
                    )
                else:
                    run_json(
                        runner,
                        [
                            gws_executable,
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
                    pending_version_description = (
                        _VERSION_DESCRIPTION_PREFIX + secrets.token_hex(16)
                    )
                    created_ids[_PENDING_SCRIPT_VERSION_DESCRIPTION] = (
                        pending_version_description
                    )
                    report_progress()
                    version = run_json(
                        runner,
                        [
                            gws_executable,
                            "script",
                            "projects",
                            "versions",
                            "create",
                            *dry,
                            "--params",
                            json.dumps({"scriptId": created_ids["script_id"]}, ensure_ascii=False),
                            "--json",
                            json.dumps(
                                {"description": pending_version_description},
                                ensure_ascii=False,
                            ),
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
                    version_number = version.get("versionNumber") if isinstance(version, dict) else None
                    if (
                        not isinstance(version_number, int)
                        or isinstance(version_number, bool)
                        or version_number <= 0
                    ):
                        raise CreationRecoveryPendingError(
                            "Apps Script 버전 번호를 확인하지 못했어요. 새 버전은 만들지 않습니다."
                        )
                    if not dry_run:
                        version_reply = run_json(
                            runner,
                            [
                                gws_executable,
                                "script",
                                "projects",
                                "getContent",
                                "--params",
                                json.dumps(
                                    {
                                        "scriptId": created_ids["script_id"],
                                        "versionNumber": version_number,
                                    },
                                    ensure_ascii=False,
                                ),
                                "--format",
                                "json",
                            ],
                            workdir,
                        )
                        version_files = (
                            version_reply.get("files")
                            if isinstance(version_reply, dict)
                            else None
                        )
                        try:
                            actual_bundle_sha256 = (
                                attendance_script_update.canonical_bundle_sha256(
                                    version_files or []
                                )
                            )
                        except Exception as error:
                            raise CreationRecoveryPendingError(
                                "만든 Apps Script 버전 내용을 확인하지 못했어요. "
                                "배포를 만들지 않았습니다."
                            ) from error
                        if actual_bundle_sha256 != expected_bundle_sha256:
                            raise CreationRecoveryPendingError(
                                "만든 Apps Script 버전이 현재 정식 코드와 달라 배포하지 않았어요."
                            )
                created_ids.pop(_PENDING_SCRIPT_VERSION_DESCRIPTION, None)
                description = _DEPLOYMENT_DESCRIPTION_PREFIX + secrets.token_hex(16)
                created_ids[_PENDING_DEPLOYMENT_DESCRIPTION] = description
                created_ids[_PENDING_DEPLOYMENT_VERSION] = str(version_number)
                # 이 기록이 안전하게 끝난 뒤에만 생성 명령을 보낸다. 아래 응답이
                # 끊겨도 다음 실행은 같은 설명·버전으로 목록을 읽기만 한다.
                report_progress()
                deployment = run_json(
                    runner,
                    [
                        gws_executable,
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
                                "versionNumber": version_number,
                                "manifestFileName": "appsscript",
                                "description": description,
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
                deployment_id = (
                    str(deployment.get("deploymentId", "") or "").strip()
                    if isinstance(deployment, dict)
                    else ""
                )
                if not deployment_id:
                    raise DeploymentRecoveryPendingError(
                        "Apps Script 배포 응답에서 만든 배포 번호를 확인하지 못했어요. "
                        "중복 배포를 막기 위해 새로 만들지 않습니다."
                    )
                created_ids["deployment_id"] = deployment_id
            created_ids.pop(_PENDING_DEPLOYMENT_DESCRIPTION, None)
            created_ids.pop(_PENDING_DEPLOYMENT_VERSION, None)
            report_progress()

        ids = {
            "template_doc_id": created_ids["template_doc_id"],
            "folder_id": created_ids["folder_id"],
            "task_list_id": created_ids["task_list_id"],
            "script_id": created_ids["script_id"],
            "deployment_id": created_ids["deployment_id"],
        }
        source_sender_url = str(
            source_settings.get("CENTRAL_CHAT_SENDER_URL", "") or ""
        ).strip()
        central_chat = build_central_chat_defaults(
            created_ids["spreadsheet_id"],
            source_sender_url or central_chat_sender_url,
        )
        config_rows = build_config_rows(
            profile,
            ids,
            str(task_list["title"]),
            central_chat,
            gemini_api_key=(
                source_settings.get("GEMINI_API_KEY", "") or gemini_api_key
                if creation_reason == ATTENDANCE_CREATION_SPLIT_REPAIR
                else gemini_api_key
            ),
        )
        if creation_reason == ATTENDANCE_CREATION_SPLIT_REPAIR:
            config_rows = merge_attendance_config_rows(
                source_setting_rows,
                config_rows,
                overwrite_keys=frozenset(
                    {
                        "SCRIPT_ID",
                        "DEPLOYMENT_ID",
                        "CENTRAL_CHAT_SHEET_ID",
                        "GEMINI_API_KEY",
                        "ATTENDANCE_AI_ALLOWED",
                    }
                ),
            )
        values_body = {
            "majorDimension": "ROWS",
            "values": config_rows,
        }
        # xlsx 견본에 박힌 옛 설정 행이 남아 중복 키가 생기지 않게, 먼저 비우고 쓴다.
        run_json(
            runner,
            [
                gws_executable,
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
                gws_executable,
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
                gws_executable,
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
        if creation_reason == ATTENDANCE_CREATION_SPLIT_REPAIR:
            _restore_candidate_student_dropdowns(
                runner,
                workdir,
                spreadsheet_id=created_ids["spreadsheet_id"],
                sheet_info=sheet_info,
                config_rows=config_rows,
                gws_executable=gws_executable,
            )
        missing_titles = [
            title for title in MESSAGE_LEDGER_HEADERS if title not in current_titles
        ]
        if missing_titles:
            run_json(
                runner,
                [
                    gws_executable,
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
                gws_executable,
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
                    gws_executable,
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
                "지금 바로 정리하려면 Google Sheet에서 처음 한 번 설정하기 -> "
                "처음 설정 한 번에 끝내기를 한 번 실행해 주세요."
            )

        # 올리기·버전 만들기·배포 만들기의 성공 답만으로는 실제 원격 코드가
        # 이번 설치본과 같다고 증명할 수 없다. 특히 중간 실패 뒤 재개할 때는
        # script_id/deployment_id가 이미 있어 위 쓰기 단계가 모두 건너뛰어진다.
        # 따라서 HEAD와 실제 배포판을 다시 읽어 둘 다 현재 묶음과 같은 경우에만
        # 로컬 설치 기록에 준비 확인표를 남긴다.
        script_bundle_sha256 = ""
        if not dry_run:
            expected_bundle_sha256 = attendance_script_update.target_bundle_sha256(
                bundle_paths.bundle_root() / "assets"
            )
            checked_script = attendance_script_update.inspect_attendance_script_update(
                created_ids["spreadsheet_id"],
                created_ids["script_id"],
                created_ids["deployment_id"],
                assets_dir=bundle_paths.bundle_root() / "assets",
                runner=runner,
                gws_executable=gws_executable,
            )
            if (
                checked_script.verified is True
                and checked_script.state == "current"
                and checked_script.spreadsheet_id == created_ids["spreadsheet_id"]
                and checked_script.script_id == created_ids["script_id"]
                and checked_script.deployment_id == created_ids["deployment_id"]
                and checked_script.current_bundle_sha256 == expected_bundle_sha256
                and checked_script.target_bundle_sha256 == expected_bundle_sha256
            ):
                script_bundle_sha256 = expected_bundle_sha256

        result = AttendanceInstallResult(
            spreadsheet_id=created_ids["spreadsheet_id"],
            spreadsheet_url=created_ids["spreadsheet_url"],
            template_doc_id=created_ids["template_doc_id"],
            template_doc_url=created_ids["template_doc_url"],
            script_id=created_ids["script_id"],
            deployment_id=created_ids["deployment_id"],
            folder_id=created_ids["folder_id"],
            task_list_id=created_ids["task_list_id"],
            workbook_name=candidate_workbook_name,
            script_bundle_sha256=script_bundle_sha256,
        )
        if not dry_run and write_record_on_success:
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
        gemini_api_key=local_gemini_api_key(),
        gws_executable=tool_runtime.resolve_gws_executable(),
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
