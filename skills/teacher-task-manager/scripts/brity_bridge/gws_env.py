"""gws(Google Workspace CLI) 실행 환경 준비.

gws는 로그인 토큰(credentials.enc)을 여는 암호화 키를 기본적으로 Windows
자격 증명 관리자에 보관한다. 그런데 자격 증명 관리자 읽기가 일시적으로
실패하면 gws는 "키가 없다"고 보고 새 키를 만들어 덮어쓰고, 이전 키로 잠긴
토큰 파일을 해독하지 못해 스스로 삭제한다 — 사용자가 한 시간에도 몇 번씩
로그아웃되는 원인이었다(2026-07-21 진단 로그로 확인).

키를 파일(~/.config/gws/.encryption_key)에 보관하면 이 삭제 루프가 아예
없다. gws를 부르는 모든 진입점이 시작할 때 이 함수를 한 번 호출한다.

또한 gws 로그인은 OAuth 클라이언트가 구성돼 있어야 시작된다(`gws auth
setup`은 gcloud가 필요해 학교 PC에서는 사실상 불가). 배포자가
gws-oauth-client.json 파일을 TeacherTaskManager 폴더나 동봉 assets에
놓아 두면 여기서 자동으로 gws에 물린다 — 새 PC 첫 로그인의 유일한
무도구 경로다.
"""
from __future__ import annotations

import os
from pathlib import Path

CLIENT_FILE_NAME = "gws-oauth-client.json"


def default_client_file_candidates() -> list[Path]:
    """OAuth 클라이언트 파일을 찾는 순서 — 사용자 폴더가 동봉본을 이긴다."""
    from brity_bridge import bundle_paths, paths

    return [
        paths.default_config_dir() / CLIENT_FILE_NAME,
        bundle_paths.bundle_root() / "assets" / CLIENT_FILE_NAME,
    ]


def prepare_gws_env(environ=os.environ, client_file_candidates=None) -> None:
    """gws 키 보관을 파일 방식으로 고정하고, 놓여 있는 OAuth 클라이언트를 물린다.

    사용자가 직접 정한 환경 변수 값은 존중한다.
    """
    environ.setdefault("GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND", "file")
    if environ.get("GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"):
        return
    if client_file_candidates is None:
        client_file_candidates = default_client_file_candidates()
    for candidate in client_file_candidates:
        path = Path(candidate)
        if path.is_file():
            environ["GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"] = str(path)
            return
