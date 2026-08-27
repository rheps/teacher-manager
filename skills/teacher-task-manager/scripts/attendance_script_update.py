"""기존 출결 Apps Script를 확인하고 같은 배포를 안전하게 갱신한다.

이 모듈은 호출하는 쪽에서 넘긴 ``runner``만 사용한다. 따라서 판정만 하는 동안에는
Google 자료를 바꾸지 않으며, 실제 명령 실행기를 저절로 찾아 실행하지도 않는다.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence


SCRIPTS_DIR = Path(__file__).resolve().parent
EXPECTED_FILE_TYPES = {"Code": "SERVER_JS", "appsscript": "JSON"}
# 2026-08-13에 공개 저장소 rheps/teacher-manager의 각 tag/commit에서
# ``skills/teacher-task-manager/assets/Code.gs``와 ``appsscript.json``을 raw로
# 읽고, 아래 ``canonical_bundle_sha256`` 규칙을 별도 PowerShell/.NET 계산으로
# 대조했다. raw 파일을 다시 확인하지 못한 더 오래된 설치본은 일부러 넣지 않는다.
# v2.4~v2.8 다섯 판은 공개 태그에서 같은 방법으로 다시 읽어 더했다. 이 값이 빠져
# 있는 동안 v2.4·v2.5로 설치한 출결이 사용자 수정본으로 잘못 잡혀, 최신판으로
# 바꿀 단추가 사라졌다.
TRUSTED_PUBLIC_BUNDLE_PROVENANCE = {
    (
        "1ecfbdcf9d5903e6" "a58a2e38c8893a67"
        "b7a1fee07003ca3f" "4bd9fe11db9bb35c"
    ): (
        ("v2.8", "38953806f799a13bfcc8" "e99e3f667f4dfd20213e"),
        ("v2.7", "245f582c4d2836ee6cba" "83b41231f26623255410"),
        ("v2.6", "465b843e901069c0aba0" "81dda6a3beaaf1dd3ceb"),
    ),
    (
        "c47bbf6dcf4b5602" "53fd1b2cf8759848"
        "db68c7e4ace8beca" "03c082bcdd2e9491"
    ): (
        ("v3.0", "b3a2ccdafa7360daaebd" "52756d15a5a9da8ddd03"),
        ("v2.9", "3b88d94a83fad8ba7cdd" "000f2d7ff70b9acfa035"),
    ),
    (
        "fa723393bad86d80" "b09742c38888618c"
        "c35a7b37939a7cbd" "5f3affae1367ec93"
    ): (
        ("v3.2", "abd43d90b4db50d59efb" "45869600970dd914f6cd"),
        ("v3.1", "7df58eba8455811df125" "76a5826708559d50611f"),
    ),
    (
        "726c78ba658c88dc" "5045ddb4ea7f87b9"
        "9d01aa6338a314cf" "6cbabf27c037c065"
    ): (
        ("v2.5", "fdd3e8f29882de316f87" "1683166d5f39c96a6397"),
        ("v2.4", "04c0d95cea3360aa9503" "5a37dbff18c2edcbb43b"),
    ),
    (
        "40661e5bf63a8133" "fe6b6a19709327ff"
        "5063b0a3624925c0" "8306e09335841bae"
    ): (
        ("v2.3", "18732fd2b919ca8f037b" "d4c0e2862f1b954d1d8d"),
    ),
    (
        "99db84f2c93e73a9" "045f865ef4cdd60f"
        "4444b688bec052c7" "419b4e3f1ab7566f"
    ): (
        ("v2.2", "dffc1ce85af6ece581dd" "2966ac09c7805242903d"),
        ("v2.1", "d0edaf6da3503d128705" "dc93feab5915bc6bb3aa"),
    ),
    (
        "b1b45e67c5f6f12e" "fdbc229ca134b9ce"
        "1e928684c7d91083" "608650994d7ad9e1"
    ): (
        ("v2.0", "9402f9abe6a0923e54a4" "4f44da46348930e7a92a"),
        ("v1.9", "f7e34b6808d346cc9c48" "f2fd6c0d1f125e3eef98"),
    ),
    (
        "246aebaca5bdb9ac" "95c0bbf6916c1f18"
        "bd28023c73cbe48e" "d549b34a942db8e6"
    ): (
        ("v1.8", "5311c6b1f4d8290b0886" "a51f51ea097cb1902fc0"),
    ),
    (
        "e130cc0e7f580075" "a2ce7363e355e0ef"
        "077a46b8a60643f1" "65adba39e7e2eb59"
    ): (
        ("v1.7.14", "0635efc7f281ecca77d7b" "d87a6b6714158c509cb"),
    ),
    (
        "dbb569e4b0da0df7" "7674d3b99d62413b"
        "a3d296515c30820c" "d1f212c5c7ab7ba0"
    ): (
        ("v1.7.12", "2ca4b8b41e7d3de317a8" "9594e54c972498671bef"),
    ),
}
TRUSTED_PUBLIC_BUNDLE_SHA256 = frozenset(TRUSTED_PUBLIC_BUNDLE_PROVENANCE)

# 공개 전 표준테스트PC에 Teacher Manager가 올린 것으로 원문까지 회수해 확인한
# 중간판이다. 공개 v2.3(d7fd551)의 appsscript.json과 같고, Code.gs에서는 마지막
# AI 계정 확인·사유 정리 고침 두 묶음만 빠졌다. 이 정확한 한 판만 같은 시트에서
# 복구하며, 다른 미등록 지문은 계속 사용자 수정본으로 보호한다.
TRUSTED_PRERELEASE_BUNDLE_PROVENANCE = {
    (
        "593edd0c752548e8" "36d0828ec946bd00"
        "7edbd4a815086a0a" "de2b0ccdb19cc36c"
    ): (
        ("2.3-standard-test-pc", "d7fd551-before-final-ai-fixes"),
    ),
}
TRUSTED_PRERELEASE_BUNDLE_SHA256 = frozenset(
    TRUSTED_PRERELEASE_BUNDLE_PROVENANCE
)


def _is_trusted_teacher_manager_bundle(bundle_sha256: str) -> bool:
    return (
        bundle_sha256 in TRUSTED_PUBLIC_BUNDLE_SHA256
        or bundle_sha256 in TRUSTED_PRERELEASE_BUNDLE_SHA256
    )


@dataclass(frozen=True)
class AttendanceScriptUpdateResult:
    """확인 또는 갱신 결과.

    ``verified``는 원격 상태를 필요한 읽기 명령으로 모두 대조했다는 뜻이다.
    ``customized``와 ``hold``는 자동으로 덮어쓰지 않으므로 항상 False다.
    """

    state: str
    verified: bool
    spreadsheet_id: str = ""
    script_id: str = ""
    deployment_id: str = ""
    current_bundle_sha256: str = ""
    target_bundle_sha256: str = ""
    deployment_version_number: int = 0
    backup_version_number: int = 0
    updated_version_number: int = 0
    detail: str = ""


@dataclass(frozen=True)
class _Bundle:
    sha256: str
    has_extra_files: bool


class _Hold(Exception):
    """자료가 모호하거나 빠져 있어 안전하게 계속할 수 없음."""


class _PermissionRequired(_Hold):
    """원격 쓰기 직전 현재 계정 또는 승인 지문이 달라져 중단함."""


_PERMISSION_REQUIRED_DETAIL = (
    "출결 기능 업데이트에 필요한 Google 권한을 다시 승인해 주세요. "
    "기존 자료는 그대로입니다."
)


def _public_failure_detail(error: Exception) -> str:
    if isinstance(error, _Hold):
        detail = str(error).strip()
        if detail:
            return detail
    return "출결 기능을 확인하지 못했어요. 학생 자료는 그대로입니다."


def _need(condition: Any, detail: str = "") -> None:
    if not condition:
        raise _Hold(detail)


def _guard_remote_mutation(mutation_guard) -> None:
    try:
        allowed = callable(mutation_guard) and mutation_guard() is True
    except Exception as error:  # noqa: BLE001 - 계정·토큰·외부 오류는 버린다.
        raise _PermissionRequired(_PERMISSION_REQUIRED_DETAIL) from error
    if not allowed:
        raise _PermissionRequired(_PERMISSION_REQUIRED_DETAIL)


def _clean_id(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _positive_int(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    raise _Hold("번호를 확인할 수 없어요.")


def _normalized_source(source: str) -> str:
    return source.replace("\r\n", "\n").replace("\r", "\n")


def _validated_files(files: Any) -> list[dict[str, str]]:
    _need(isinstance(files, list) and len(files) >= 2, "스크립트 파일이 빠졌어요.")
    result: list[dict[str, str]] = []
    names: set[str] = set()
    for item in files:
        _need(isinstance(item, dict), "스크립트 파일 정보가 완전하지 않아요.")
        name, file_type, source = item.get("name"), item.get("type"), item.get("source")
        _need(
            isinstance(name, str)
            and name != ""
            and name not in names
            and isinstance(file_type, str)
            and file_type != ""
            and isinstance(source, str),
            "스크립트 파일 정보가 완전하지 않아요.",
        )
        names.add(name)
        result.append({"name": name, "type": file_type, "source": source})

    for name, expected_type in EXPECTED_FILE_TYPES.items():
        matches = [item for item in result if item["name"] == name]
        _need(len(matches) == 1, f"{name} 파일을 확인할 수 없어요.")
        _need(
            matches[0]["type"] == expected_type and matches[0]["source"] != "",
            f"{name} 파일 내용이 완전하지 않아요.",
        )
    return result


def canonical_bundle_sha256(files: Sequence[Mapping[str, Any]]) -> str:
    """기존 설치 코드와 같은 이름·종류·내용 묶음 지문을 계산한다.

    각 파일은 ``이름\0종류\0LF로 맞춘 내용``이며 파일 사이에도 NUL 한 글자를
    둔다. 정확한 두 정식 파일에서는 예전 설치판이 저장한 값과 같고, 추가 파일이
    있으면 그 파일까지 지문에 들어간다.
    """

    checked = _validated_files(list(files))
    pieces = []
    for item in sorted(checked, key=lambda value: value["name"].encode("utf-8")):
        pieces.append(
            item["name"]
            + "\0"
            + item["type"]
            + "\0"
            + _normalized_source(item["source"])
        )
    return hashlib.sha256("\0".join(pieces).encode("utf-8")).hexdigest()


def _compact(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _find_json_container(output: str, start: int = 0):
    decoder = json.JSONDecoder()
    for position in (index for index in range(start, len(output)) if output[index] in "[{"):
        try:
            value, end = decoder.raw_decode(output, position)
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list)):
            return value, end
    return None


def _run_one_json(runner, args: Sequence[str], cwd: Path | None = None):
    """runner를 정확히 한 번 부르고 두 허용 반환형을 한곳에서 처리한다."""

    _need(callable(runner), "명령 실행 방법이 없어요.")
    raw = runner(list(args), SCRIPTS_DIR if cwd is None else Path(cwd))
    if isinstance(raw, tuple):
        _need(len(raw) == 2, "명령 결과 형식이 달라요.")
        code, output = raw
        _need(
            isinstance(code, int) and not isinstance(code, bool),
            "명령 결과 번호를 확인할 수 없어요.",
        )
        _need(
            code == 0,
            "출결 기능을 확인하지 못했어요. 학생 자료는 그대로입니다.",
        )
    else:
        output = raw
    _need(isinstance(output, str), "명령 결과가 글자가 아니에요.")
    found = _find_json_container(output)
    _need(found is not None, "명령 결과를 확인할 수 없어요.")
    value, end = found
    _need(_find_json_container(output, end) is None, "명령 결과가 하나로 정해지지 않아요.")
    return value


def _call(
    runner,
    gws: str,
    parts: Sequence[str],
    params: Mapping[str, Any],
    body: Mapping[str, Any] | None = None,
    *,
    cwd: Path | None = None,
):
    args = [gws, *parts, "--params", _compact(params)]
    if body is not None:
        args += ["--json", _compact(body)]
    args += ["--format", "json"]
    return _run_one_json(runner, args, cwd)


def _project(runner, gws: str, script: str):
    return _call(runner, gws, ["script", "projects", "get"], {"scriptId": script})


def _content(runner, gws: str, script: str, version: int | None = None):
    params: dict[str, Any] = {"scriptId": script}
    if version is not None:
        params["versionNumber"] = version
    return _call(
        runner,
        gws,
        ["script", "projects", "getContent"],
        params,
    )


def _deployment(runner, gws: str, script: str, deployment: str):
    return _call(
        runner,
        gws,
        ["script", "projects", "deployments", "get"],
        {"scriptId": script, "deploymentId": deployment},
    )


def _list_versions(runner, gws: str, script: str) -> list[Mapping[str, Any]]:
    collected: list[Mapping[str, Any]] = []
    page_token = ""
    seen_tokens: set[str] = set()
    while True:
        params: dict[str, Any] = {"scriptId": script, "pageSize": 50}
        if page_token:
            params["pageToken"] = page_token
        reply = _call(
            runner,
            gws,
            ["script", "projects", "versions", "list"],
            params,
        )
        _need(isinstance(reply, dict), "만들어진 출결 기능 판을 확인할 수 없어요.")
        versions = reply.get("versions", [])
        _need(isinstance(versions, list), "만들어진 출결 기능 판을 확인할 수 없어요.")
        for item in versions:
            _need(isinstance(item, dict), "만들어진 출결 기능 판을 확인할 수 없어요.")
            collected.append(item)
        next_token = reply.get("nextPageToken", "")
        _need(isinstance(next_token, str), "만들어진 출결 기능 판을 확인할 수 없어요.")
        if not next_token:
            return collected
        _need(next_token not in seen_tokens, "출결 기능 판 목록이 반복되어 멈췄어요.")
        seen_tokens.add(next_token)
        page_token = next_token


def _check_project(reply: Any, sheet: str, script: str) -> None:
    _need(
        isinstance(reply, dict)
        and reply.get("scriptId") == script
        and reply.get("parentId") == sheet,
        "출결표에 묶인 스크립트가 아니에요.",
    )


def _bundle_from_reply(reply: Any, script: str) -> _Bundle:
    _need(
        isinstance(reply, dict)
        and reply.get("scriptId") == script
        and isinstance(reply.get("files"), list),
        "스크립트 내용을 확인할 수 없어요.",
    )
    files = _validated_files(reply["files"])
    return _Bundle(
        canonical_bundle_sha256(files),
        set(item["name"] for item in files) != set(EXPECTED_FILE_TYPES),
    )


def _check_deployment_base(reply: Any, script: str, deployment: str) -> tuple[int, str]:
    _need(isinstance(reply, dict) and reply.get("deploymentId") == deployment)
    config = reply.get("deploymentConfig")
    _need(
        isinstance(config, dict)
        and config.get("scriptId") == script
        and config.get("manifestFileName") == "appsscript",
        "기존 배포 정보를 확인할 수 없어요.",
    )
    version = _positive_int(config.get("versionNumber"))
    description = config.get("description")
    _need(isinstance(description, str), "기존 배포 설명을 확인할 수 없어요.")
    return version, description


def _check_version(reply: Any, description: str) -> int:
    _need(isinstance(reply, dict), "버전 생성 결과를 확인할 수 없어요.")
    number = _positive_int(reply.get("versionNumber"))
    _need(
        reply.get("description") == description
        and isinstance(reply.get("createTime"), str)
        and reply.get("createTime") != "",
        "버전 생성 결과가 요청과 달라요.",
    )
    return number


def _check_updated_deployment(
    reply: Any,
    script: str,
    deployment: str,
    version: int,
    description: str,
) -> None:
    actual_version, actual_description = _check_deployment_base(reply, script, deployment)
    _need(
        actual_version == version and actual_description == description,
        "기존 배포가 새 버전을 가리키지 않아요.",
    )


def _verified_prepared_version(runner, gws: str, script: str, target_sha: str) -> int:
    description = "attendance-update-" + target_sha[:16]
    candidates = []
    for item in _list_versions(runner, gws, script):
        if item.get("description") == description:
            candidates.append((
                _positive_int(item.get("versionNumber")),
                item,
            ))
    for version, _item in sorted(candidates, key=lambda candidate: candidate[0], reverse=True):
        bundle = _bundle_from_reply(_content(runner, gws, script, version), script)
        if not bundle.has_extra_files and bundle.sha256 == target_sha:
            return version
    _need(
        not candidates,
        "같은 이름으로 만든 출결 기능 판의 내용을 확인할 수 없어요.",
    )
    return 0


def _load_target(assets_dir: Path) -> tuple[bytes, bytes, list[dict[str, str]], str]:
    folder = Path(assets_dir)
    code_bytes = (folder / "Code.gs").read_bytes()
    manifest_bytes = (folder / "appsscript.json").read_bytes()
    code = code_bytes.decode("utf-8")
    manifest = manifest_bytes.decode("utf-8")
    files = [
        {"name": "Code", "type": "SERVER_JS", "source": code},
        {"name": "appsscript", "type": "JSON", "source": manifest},
    ]
    return code_bytes, manifest_bytes, files, canonical_bundle_sha256(files)


def target_bundle_sha256(assets_dir: Path) -> str:
    """현재 프로그램에 들어 있는 정식 Code.gs 묶음의 지문을 돌려준다."""

    _code, _manifest, _files, bundle_sha256 = _load_target(Path(assets_dir))
    return bundle_sha256


def _result(
    state: str,
    *,
    verified: bool = False,
    sheet: str = "",
    script: str = "",
    deployment: str = "",
    current_sha: str = "",
    target_sha: str = "",
    deployed_version: int = 0,
    detail: str = "",
) -> AttendanceScriptUpdateResult:
    return AttendanceScriptUpdateResult(
        state=state,
        verified=verified,
        spreadsheet_id=sheet,
        script_id=script,
        deployment_id=deployment,
        current_bundle_sha256=current_sha,
        target_bundle_sha256=target_sha,
        deployment_version_number=deployed_version,
        detail=detail,
    )


def inspect_attendance_script_update(
    spreadsheet_id,
    script_id,
    deployment_id,
    *,
    assets_dir,
    runner=None,
    gws_executable: str | None = None,
) -> AttendanceScriptUpdateResult:
    """프로젝트·HEAD·고정 버전을 읽기만 하여 자동 갱신 가능 여부를 정한다."""

    sheet = _clean_id(spreadsheet_id)
    script = _clean_id(script_id)
    deployment = _clean_id(deployment_id)
    gws = _clean_id(gws_executable)
    if not sheet or not script or not deployment or not gws or not callable(runner):
        return _result(
            "hold",
            sheet=sheet,
            script=script,
            deployment=deployment,
            detail="확인에 필요한 값이 빠졌어요.",
        )

    target_sha = ""
    current_sha = ""
    deployed_version = 0
    try:
        _code_bytes, _manifest_bytes, _target_files, target_sha = _load_target(
            Path(assets_dir)
        )
        _check_project(_project(runner, gws, script), sheet, script)
        head = _bundle_from_reply(_content(runner, gws, script), script)
        current_sha = head.sha256

        deployment_reply = _deployment(runner, gws, script, deployment)
        deployed_version, _description = _check_deployment_base(
            deployment_reply, script, deployment
        )
        fixed = _bundle_from_reply(
            _content(runner, gws, script, deployed_version), script
        )

        if head.sha256 != fixed.sha256 or head.has_extra_files != fixed.has_extra_files:
            head_is_official = (
                head.sha256 == target_sha
                or _is_trusted_teacher_manager_bundle(head.sha256)
            )
            fixed_is_official = _is_trusted_teacher_manager_bundle(fixed.sha256)
            _need(
                head_is_official
                and fixed_is_official
                and not head.has_extra_files
                and not fixed.has_extra_files,
                "현재 편집본과 실제 배포 중인 버전이 달라요.",
            )
            if head.sha256 == target_sha:
                # 업로드 뒤 판 만들기나 배포 연결 전에 앱이 꺼졌어도 같은 시트에서
                # 이어간다. 판이 이미 있으면 그 번호를 쓰고, 없으면 적용 버튼에서
                # 현재 HEAD를 다시 대조한 뒤 새 판 하나만 만든다.
                prepared_version = _verified_prepared_version(
                    runner, gws, script, target_sha
                )
                return replace(
                    _result(
                        "finishing_required",
                        verified=True,
                        sheet=sheet,
                        script=script,
                        deployment=deployment,
                        current_sha=target_sha,
                        target_sha=target_sha,
                        deployed_version=deployed_version,
                        detail=(
                            "새 기능은 준비됐고 같은 시트의 마지막 연결만 남았습니다. "
                            "학생 자료는 그대로입니다."
                        ),
                    ),
                    updated_version_number=prepared_version,
                )
            # 편집본과 배포판이 서로 달라도 둘 다 공개한 정식 파일이면, 중간에
            # 멈춘 예전 업데이트다. 사용자가 누른 업데이트에서 현재 편집본을
            # 백업한 뒤 같은 Script와 같은 배포를 최신판으로 맞출 수 있다.
            return _result(
                "update_available",
                verified=True,
                sheet=sheet,
                script=script,
                deployment=deployment,
                current_sha=head.sha256,
                target_sha=target_sha,
                deployed_version=deployed_version,
            )
        if head.has_extra_files:
            return _result(
                "customized",
                sheet=sheet,
                script=script,
                deployment=deployment,
                current_sha=current_sha,
                target_sha=target_sha,
                deployed_version=deployed_version,
                detail="추가한 스크립트 파일이 있어 자동으로 덮어쓰지 않아요.",
            )
        if current_sha == target_sha:
            state, verified = "current", True
        elif _is_trusted_teacher_manager_bundle(current_sha):
            state, verified = "update_available", True
        else:
            state, verified = "customized", False
        return _result(
            state,
            verified=verified,
            sheet=sheet,
            script=script,
            deployment=deployment,
            current_sha=current_sha,
            target_sha=target_sha,
            deployed_version=deployed_version,
        )
    except Exception as exc:  # 외부 응답은 조금이라도 모호하면 쓰지 않는다.
        return _result(
            "hold",
            sheet=sheet,
            script=script,
            deployment=deployment,
            current_sha=current_sha,
            target_sha=target_sha,
            deployed_version=deployed_version,
            detail=_public_failure_detail(exc),
        )


def inspect_or_update_attendance_script(
    record,
    *,
    assets_dir,
    apply,
    runner,
    gws_executable,
    temp_parent=None,
    mutation_guard=None,
) -> AttendanceScriptUpdateResult:
    """저장된 설치 기록의 세 ID만 엄격히 읽어 확인 또는 갱신으로 보낸다."""

    if not isinstance(record, Mapping) or not isinstance(apply, bool):
        return _result("hold", detail="저장된 출결 연결 정보를 확인할 수 없어요.")

    values: dict[str, str] = {}
    for key in ("spreadsheet_id", "script_id", "deployment_id"):
        value = record.get(key)
        if (
            not isinstance(value, str)
            or value == ""
            or value != value.strip()
        ):
            return _result("hold", detail="저장된 출결 연결 정보가 완전하지 않아요.")
        values[key] = value

    common = {
        "assets_dir": assets_dir,
        "runner": runner,
        "gws_executable": gws_executable,
    }
    if apply is False:
        return inspect_attendance_script_update(
            values["spreadsheet_id"],
            values["script_id"],
            values["deployment_id"],
            **common,
        )
    return apply_attendance_script_update(
        values["spreadsheet_id"],
        values["script_id"],
        values["deployment_id"],
        **common,
        temp_parent=temp_parent,
        mutation_guard=mutation_guard,
    )


def verified_same_attendance_connection(
    result: Mapping[str, Any],
    record: Mapping[str, Any],
    expected_bundle_sha256: str,
) -> bool:
    """원격 확인 결과가 저장된 세 연결과 현재 제품 파일을 그대로 가리키는지 본다."""

    if not isinstance(result, Mapping) or not isinstance(record, Mapping):
        return False
    expected_sha256 = _clean_id(expected_bundle_sha256)
    return (
        result.get("verified") is True
        and result.get("state") in {"current", "updated"}
        and bool(expected_sha256)
        and all(
            isinstance(record.get(key), str)
            and record.get(key) != ""
            and result.get(key) == record.get(key)
            for key in ("spreadsheet_id", "script_id", "deployment_id")
        )
        and result.get("target_bundle_sha256") == expected_sha256
        and result.get("current_bundle_sha256") == expected_sha256
    )


def _require_bundle(reply: Any, script: str, wanted_sha: str) -> None:
    bundle = _bundle_from_reply(reply, script)
    _need(
        not bundle.has_extra_files and bundle.sha256 == wanted_sha,
        "스크립트 내용이 요청한 묶음과 달라요.",
    )


def _create_version(
    runner,
    gws: str,
    script: str,
    description: str,
    mutation_guard,
) -> int:
    _guard_remote_mutation(mutation_guard)
    reply = _call(
        runner,
        gws,
        ["script", "projects", "versions", "create"],
        {"scriptId": script},
        {"description": description},
    )
    return _check_version(reply, description)


def _push_exact_files(
    runner,
    gws: str,
    script: str,
    code_bytes: bytes,
    manifest_bytes: bytes,
    temp_parent: Path | None,
    mutation_guard,
):
    parent = None if temp_parent is None else str(Path(temp_parent))
    with tempfile.TemporaryDirectory(
        prefix="attendance-script-update-", dir=parent
    ) as name:
        folder = Path(name)
        (folder / "Code.gs").write_bytes(code_bytes)
        (folder / "appsscript.json").write_bytes(manifest_bytes)
        _guard_remote_mutation(mutation_guard)
        return _run_one_json(
            runner,
            [
                gws,
                "script",
                "+push",
                "--script",
                script,
                "--dir",
                folder.name,
                "--format",
                "json",
            ],
            folder.parent,
        )


def _safe_head_read(runner, gws: str, script: str) -> None:
    try:
        _content(runner, gws, script)
    except Exception:
        pass


def _updated_result(
    inspected: AttendanceScriptUpdateResult,
    updated_version: int,
) -> AttendanceScriptUpdateResult:
    return replace(
        inspected,
        state="updated",
        verified=True,
        current_bundle_sha256=inspected.target_bundle_sha256,
        deployment_version_number=updated_version,
        updated_version_number=updated_version,
        detail="",
    )


def _stopped_result(
    inspected: AttendanceScriptUpdateResult,
    error: Exception,
    **changes,
) -> AttendanceScriptUpdateResult:
    state = "permission-required" if isinstance(error, _PermissionRequired) else "hold"
    return replace(
        inspected,
        state=state,
        verified=False,
        detail=_public_failure_detail(error),
        **changes,
    )


def _confirm_deployment_after_update(
    runner,
    gws: str,
    script: str,
    deployment: str,
    previous_version: int,
    updated_version: int,
    update_description: str,
    sleeper,
) -> tuple[str, Exception | None]:
    """Google 반영 지연 동안 쓰기는 반복하지 않고 같은 배포만 다시 읽는다."""

    saw_previous = False
    last_error: Exception | None = None
    for delay in (0.0, 1.0, 2.0, 4.0):
        if delay:
            sleeper(delay)
        try:
            version, description = _check_deployment_base(
                _deployment(runner, gws, script, deployment), script, deployment
            )
        except Exception as exc:
            last_error = exc
            continue
        if version == updated_version and description == update_description:
            return "updated", None
        if version == previous_version:
            saw_previous = True
            last_error = _Hold("기존 배포가 새 버전을 가리키지 않아요.")
            continue
        return (
            "hold",
            _Hold("확인하는 사이 기존 배포가 다른 버전으로 바뀌었어요."),
        )
    if saw_previous:
        return "previous", last_error
    return "hold", last_error or _Hold("기존 배포를 다시 확인하지 못했어요.")


def _finish_existing_verified_version(
    inspected: AttendanceScriptUpdateResult,
    runner,
    gws: str,
    sleeper,
    mutation_guard,
) -> AttendanceScriptUpdateResult:
    """준비된 판을 같은 배포에 한 번만 연결하고, 모호하면 읽기만 한다."""

    script = inspected.script_id
    deployment = inspected.deployment_id
    previous_version = inspected.deployment_version_number
    updated_version = inspected.updated_version_number
    update_description = "attendance-update-" + inspected.target_bundle_sha256[:16]
    update_body = {
        "deploymentConfig": {
            "scriptId": script,
            "versionNumber": updated_version,
            "manifestFileName": "appsscript",
            "description": update_description,
        }
    }
    error: Exception | None = None
    try:
        live_version, _live_description = _check_deployment_base(
            _deployment(runner, gws, script, deployment), script, deployment
        )
        _need(
            live_version == previous_version,
            "확인하는 사이 기존 배포가 다른 버전으로 바뀌었어요.",
        )
        _guard_remote_mutation(mutation_guard)
        update_reply = _call(
            runner,
            gws,
            ["script", "projects", "deployments", "update"],
            {"scriptId": script, "deploymentId": deployment},
            update_body,
        )
        _check_updated_deployment(
            update_reply, script, deployment, updated_version, update_description
        )
    except _PermissionRequired as exc:
        return _stopped_result(inspected, exc)
    except Exception as exc:
        error = exc

    confirmation, confirmation_error = _confirm_deployment_after_update(
        runner,
        gws,
        script,
        deployment,
        previous_version,
        updated_version,
        update_description,
        sleeper,
    )
    if confirmation == "updated":
        return _updated_result(inspected, updated_version)
    if confirmation == "previous":
        # Google가 아직 옛 연결을 보여 주면 쓰기를 되풀이하지 않는다. 다음 실행은
        # 이미 만든 정확한 판을 찾아 같은 배포 연결만 안전하게 이어간다.
        return inspected
    if confirmation_error is not None:
        error = confirmation_error
    if error is not None:
        return replace(
            inspected,
            state="hold",
            verified=False,
            detail=_public_failure_detail(error),
        )
    return replace(
        inspected,
        state="hold",
        verified=False,
        detail="기존 배포를 다시 확인하지 못했어요.",
    )


def _create_missing_target_version(
    inspected: AttendanceScriptUpdateResult,
    runner,
    gws: str,
    mutation_guard,
) -> AttendanceScriptUpdateResult:
    """이미 올라간 정식 HEAD를 다시 확인하고 빠진 불변 판 하나만 만든다."""

    script = inspected.script_id
    target_sha = inspected.target_bundle_sha256
    update_description = "attendance-update-" + target_sha[:16]
    try:
        # 상태 확인 뒤 다른 편집이 끼어들었으면 어떤 판도 만들지 않는다.
        _require_bundle(_content(runner, gws, script), script, target_sha)
        updated_version = _create_version(
            runner, gws, script, update_description, mutation_guard
        )
        _require_bundle(
            _content(runner, gws, script, updated_version), script, target_sha
        )
    except Exception as exc:
        # versions.create 답이 사라진 경우 같은 쓰기를 반복하지 않는다. 다음 버튼
        # 실행의 읽기 단계가 실제로 생긴 판을 찾아 이어간다.
        return _stopped_result(inspected, exc)
    return replace(inspected, updated_version_number=updated_version)


def apply_attendance_script_update(
    spreadsheet_id,
    script_id,
    deployment_id,
    *,
    assets_dir,
    runner=None,
    gws_executable: str | None = None,
    temp_parent=None,
    sleeper=None,
    mutation_guard=None,
) -> AttendanceScriptUpdateResult:
    """검증된 옛 공개본만 백업한 뒤 같은 배포 ID를 새 버전으로 바꾼다."""

    actual_sleeper = time.sleep if sleeper is None else sleeper
    if not callable(actual_sleeper):
        return _result("hold", detail="다시 확인할 방법이 없어요.")

    inspected = inspect_attendance_script_update(
        spreadsheet_id,
        script_id,
        deployment_id,
        assets_dir=assets_dir,
        runner=runner,
        gws_executable=gws_executable,
    )
    if inspected.state == "finishing_required" and inspected.verified:
        if inspected.updated_version_number <= 0:
            inspected = _create_missing_target_version(
                inspected,
                runner,
                _clean_id(gws_executable),
                mutation_guard,
            )
            if inspected.state == "hold" or not inspected.verified:
                return inspected
        return _finish_existing_verified_version(
            inspected,
            runner,
            _clean_id(gws_executable),
            actual_sleeper,
            mutation_guard,
        )
    if inspected.state != "update_available" or not inspected.verified:
        return inspected

    script = inspected.script_id
    gws = _clean_id(gws_executable)
    old_sha = inspected.current_bundle_sha256
    target_sha = inspected.target_bundle_sha256
    before_description = "attendance-before-update-" + old_sha[:16]
    update_description = "attendance-update-" + target_sha[:16]

    try:
        code_bytes, manifest_bytes, _target_files, fresh_target_sha = _load_target(
            Path(assets_dir)
        )
        _need(fresh_target_sha == target_sha, "업데이트 파일이 확인 도중 바뀌었어요.")

        backup_version = _create_version(
            runner, gws, script, before_description, mutation_guard
        )
        _require_bundle(
            _content(runner, gws, script, backup_version), script, old_sha
        )
        # 백업을 만드는 사이에 다른 사람이 편집했다면 그 사람의 내용을 덮지 않는다.
        # 실제 업로드 바로 앞에서 현재 편집본을 한 번 더 읽어 옛 정식본 그대로인지 본다.
        _require_bundle(_content(runner, gws, script), script, old_sha)
    except Exception as exc:
        return _stopped_result(inspected, exc)

    try:
        pushed = _push_exact_files(
            runner,
            gws,
            script,
            code_bytes,
            manifest_bytes,
            None if temp_parent is None else Path(temp_parent),
            mutation_guard,
        )
        _require_bundle(pushed, script, target_sha)
    except Exception as exc:
        # 쓰기가 성공했는데 답만 사라졌을 수 있다. 읽기는 한 번 하되 쓰기는 반복하지 않는다.
        _safe_head_read(runner, gws, script)
        return _stopped_result(
            inspected,
            exc,
            backup_version_number=backup_version,
        )

    try:
        _require_bundle(_content(runner, gws, script), script, target_sha)
        updated_version = _create_version(
            runner, gws, script, update_description, mutation_guard
        )
        _need(
            updated_version > backup_version,
            "새 버전 번호가 백업 버전보다 크지 않아요.",
        )
        _require_bundle(
            _content(runner, gws, script, updated_version), script, target_sha
        )
    except Exception as exc:
        return _stopped_result(
            inspected,
            exc,
            backup_version_number=backup_version,
        )

    return _finish_existing_verified_version(
        replace(
            inspected,
            state="finishing_required",
            verified=True,
            backup_version_number=backup_version,
            updated_version_number=updated_version,
        ),
        runner,
        gws,
        actual_sleeper,
        mutation_guard,
    )


__all__ = [
    "AttendanceScriptUpdateResult",
    "TRUSTED_PUBLIC_BUNDLE_PROVENANCE",
    "TRUSTED_PUBLIC_BUNDLE_SHA256",
    "apply_attendance_script_update",
    "canonical_bundle_sha256",
    "inspect_attendance_script_update",
    "inspect_or_update_attendance_script",
    "target_bundle_sha256",
    "verified_same_attendance_connection",
]
