"""비공개 출결 사본의 bound Apps Script를 확인하고 선택적으로 준비한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from brity_bridge import gws_env, process_win, tool_runtime  # noqa: E402


REMOTE_COMMAND_TIMEOUT_SECONDS = 120.0


def default_runner(args: Sequence[str], cwd: Path) -> str:
    """Apps Script 원격 명령 하나가 끝없이 출결 잠금을 잡지 않게 한다."""

    code, output = process_win.run_captured(
        list(args),
        cwd=cwd,
        timeout=REMOTE_COMMAND_TIMEOUT_SECONDS,
        env=gws_env.gws_environ(),
    )
    if code != 0:
        raise subprocess.CalledProcessError(
            code, list(args), output=output, stderr=output
        )
    return output


@dataclass(frozen=True)
class AttendanceCopyScriptResult:
    state: str
    verified: bool
    copy_spreadsheet_id: str = ""
    copy_script_id: str = ""
    version_number: int = 0
    deployment_id: str = ""
    bundle_sha256: str = ""


class _Hold(Exception):
    pass

def _need(condition: Any) -> None:
    if not condition:
        raise _Hold

def _out(
    state: str, sheet: str = "", script: str = "", version: int = 0,
    deployment: str = "", sha: str = "", verified: bool = False,
) -> AttendanceCopyScriptResult:
    return AttendanceCopyScriptResult(state, verified, sheet, script, version, deployment, sha)

def _id(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""

def _newlines(source: str) -> str:
    return source.replace("\r\n", "\n").replace("\r", "\n")

def _bundle(assets_dir: Path):
    code_bytes = (Path(assets_dir) / "Code.gs").read_bytes()
    manifest_bytes = (Path(assets_dir) / "appsscript.json").read_bytes()
    code = code_bytes.decode("utf-8")
    manifest = manifest_bytes.decode("utf-8")
    _need(code != "" and manifest != "")
    joined = (
        "Code\0SERVER_JS\0" + _newlines(code)
        + "\0appsscript\0JSON\0" + _newlines(manifest)
    )
    sha = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return code_bytes, manifest_bytes, code, manifest, sha

def _description(sha: str) -> str:
    return "attendance-copy-" + sha[:16]

def _compact(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

def _find_json_container(output: str, start: int = 0):
    decoder = json.JSONDecoder()
    for position in (i for i in range(start, len(output)) if output[i] in "[{"):
        try:
            value, end = decoder.raw_decode(output, position)
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list)):
            return value, end
    return None

def _run_one_json(runner, args, cwd=None):
    output = runner(args, SCRIPTS_DIR if cwd is None else Path(cwd))
    _need(isinstance(output, str))
    found = _find_json_container(output)
    _need(found is not None)
    value, end = found
    _need(_find_json_container(output, end) is None)
    return value

def _call(
    runner, gws: str, parts: Sequence[str], params: Mapping[str, Any],
    body: Mapping[str, Any] | None = None,
):
    args = [gws, *parts, "--params", _compact(params)]
    if body is not None:
        args += ["--json", _compact(body)]
    args += ["--format", "json"]
    return _run_one_json(runner, args)

def _project(runner, gws: str, script: str):
    return _call(runner, gws, ["script", "projects", "get"], {"scriptId": script})

def _content(runner, gws: str, script: str, version: int | None = None):
    params: dict[str, Any] = {"scriptId": script}
    if version is not None:
        params["versionNumber"] = version
    return _call(runner, gws, ["script", "projects", "getContent"], params)

def _check_project(reply: Any, sheet: str, script: str) -> None:
    _need(
        isinstance(reply, dict) and reply.get("scriptId") == script
        and reply.get("parentId") == sheet
    )

def _sources(reply: Any, script: str) -> dict[str, str]:
    _need(
        isinstance(reply, dict)
        and reply.get("scriptId") == script
        and isinstance(reply.get("files"), list)
        and len(reply["files"]) == 2
    )
    expected = {"Code": "SERVER_JS", "appsscript": "JSON"}
    result: dict[str, str] = {}
    for item in reply["files"]:
        _need(isinstance(item, dict))
        name, source = item.get("name"), item.get("source")
        _need(
            name in expected
            and name not in result
            and item.get("type") == expected[name]
            and isinstance(source, str)
            and source != ""
        )
        result[name] = source
    _need(set(result) == set(expected))
    return result

def _same_content(reply: Any, script: str, code: str, manifest: str) -> bool:
    try:
        remote = _sources(reply, script)
        return (
            _newlines(remote["Code"]) == _newlines(code)
            and _newlines(remote["appsscript"]) == _newlines(manifest)
        )
    except Exception:
        return False

def _check_version(reply: Any, description: str) -> int:
    _need(isinstance(reply, dict))
    number = reply.get("versionNumber")
    _need(
        isinstance(number, int)
        and not isinstance(number, bool)
        and number > 0
        and reply.get("description") == description
        and isinstance(reply.get("createTime"), str)
        and reply.get("createTime") != ""
    )
    return number

def _check_deployment(
    reply: Any,
    script: str,
    version: int,
    description: str,
    wanted_id: str = "",
) -> str:
    _need(isinstance(reply, dict))
    deployment, config = reply.get("deploymentId"), reply.get("deploymentConfig")
    _need(
        isinstance(deployment, str)
        and deployment != ""
        and (not wanted_id or deployment == wanted_id)
        and isinstance(config, dict)
        and config.get("scriptId") == script
        and isinstance(config.get("versionNumber"), int)
        and not isinstance(config.get("versionNumber"), bool)
        and config.get("versionNumber") == version
        and config.get("manifestFileName") == "appsscript"
        and config.get("description") == description
    )
    return deployment

def _safe_head(runner, gws: str, script: str) -> None:
    try:
        _content(runner, gws, script)
    except Exception:
        pass

def _push(
    runner, gws: str, script: str, code_bytes: bytes,
    manifest_bytes: bytes, temp_parent: Path | None,
):
    parent = None if temp_parent is None else str(Path(temp_parent))
    with tempfile.TemporaryDirectory(prefix="attendance-copy-script-", dir=parent) as name:
        folder = Path(name)
        (folder / "Code.gs").write_bytes(code_bytes)
        (folder / "appsscript.json").write_bytes(manifest_bytes)
        # gws +push는 --dir에 절대 경로를 400 validationError로 거절한다.
        # 임시 폴더의 부모에서 명령을 돌리고 폴더 이름만 넘긴다.
        return _run_one_json(
            runner,
            [
                gws, "script", "+push", "--script", script, "--dir",
                folder.name, "--format", "json",
            ],
            cwd=folder.parent,
        )

def prepare_attendance_copy_script(
    copied_spreadsheet_id,
    copied_script_id,
    *,
    assets_dir,
    apply=False,
    runner=default_runner,
    gws_executable: str | None = None,
    temp_parent=None,
) -> AttendanceCopyScriptResult:
    """사본 ID 두 개를 확인하고 --apply일 때만 스크립트를 쓴다."""

    sheet, script = _id(copied_spreadsheet_id), _id(copied_script_id)
    if not sheet or not script:
        return _out("hold")
    gws = (
        tool_runtime.resolve_gws_executable()
        if gws_executable is None
        else _id(gws_executable)
    )
    if not gws:
        return _out("hold")
    try:
        code_b, manifest_b, code, manifest, sha = _bundle(Path(assets_dir))
        _check_project(_project(runner, gws, script), sheet, script)
        _sources(_content(runner, gws, script), script)
        if apply is not True:
            return _out("ready_for_apply", sheet, script, sha=sha)

        try:
            pushed = _push(
                runner, gws, script, code_b, manifest_b,
                None if temp_parent is None else Path(temp_parent),
            )
            _need(_same_content(pushed, script, code, manifest))
        except Exception:
            _safe_head(runner, gws, script)
            return _out("hold")

        _need(_same_content(_content(runner, gws, script), script, code, manifest))
        description = _description(sha)
        version_reply = _call(
            runner, gws, ["script", "projects", "versions", "create"],
            {"scriptId": script}, {"description": description},
        )
        version = _check_version(version_reply, description)
        _need(
            _same_content(
                _content(runner, gws, script, version), script, code, manifest
            )
        )
        deployment_reply = _call(
            runner, gws, ["script", "projects", "deployments", "create"],
            {"scriptId": script},
            {
                "versionNumber": version,
                "manifestFileName": "appsscript",
                "description": description,
            },
        )
        deployment = _check_deployment(deployment_reply, script, version, description)
        return _out("ready_for_task5", sheet, script, version, deployment, sha, True)
    except Exception:
        return _out("hold")

def verify_prepared_copied_script(
    copied_spreadsheet_id,
    copied_script_id,
    *,
    version_number,
    deployment_id,
    bundle_sha256,
    assets_dir,
    runner=default_runner,
    gws_executable: str | None = None,
) -> AttendanceCopyScriptResult:
    """parent·HEAD·version·deployment를 새 읽기 네 번으로 재확인한다."""

    sheet, script = _id(copied_spreadsheet_id), _id(copied_script_id)
    deployment, supplied_sha = _id(deployment_id), _id(bundle_sha256)
    valid_number = (
        isinstance(version_number, int)
        and not isinstance(version_number, bool)
        and version_number > 0
    )
    if (
        not sheet or not script or not deployment or not valid_number
        or re.fullmatch(r"[0-9a-f]{64}", supplied_sha) is None
    ):
        return _out("hold")
    gws = (
        tool_runtime.resolve_gws_executable()
        if gws_executable is None
        else _id(gws_executable)
    )
    if not gws:
        return _out("hold")
    try:
        _code_b, _manifest_b, code, manifest, local_sha = _bundle(Path(assets_dir))
        _need(supplied_sha == local_sha)
        description = _description(local_sha)
        _check_project(_project(runner, gws, script), sheet, script)
        _need(_same_content(_content(runner, gws, script), script, code, manifest))
        _need(
            _same_content(
                _content(runner, gws, script, version_number),
                script, code, manifest,
            )
        )
        deployment_reply = _call(
            runner, gws, ["script", "projects", "deployments", "get"],
            {"scriptId": script, "deploymentId": deployment},
        )
        _check_deployment(deployment_reply, script, version_number, description, deployment)
        return _out(
            "ready_for_task5", sheet, script, version_number,
            deployment, local_sha, True,
        )
    except Exception:
        return _out("hold")

def main(
    argv=None,
    *,
    runner=default_runner,
    assets_dir=None,
    temp_parent=None,
    gws_executable: str | None = None,
) -> AttendanceCopyScriptResult:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--copied-spreadsheet-id", required=True)
    parser.add_argument("--copied-script-id", required=True)
    parser.add_argument("--apply", action="store_true")
    from_cli = argv is None
    args = parser.parse_args(argv)
    result = prepare_attendance_copy_script(
        args.copied_spreadsheet_id,
        args.copied_script_id,
        assets_dir=Path(assets_dir or SCRIPTS_DIR.parent / "assets"),
        apply=args.apply,
        runner=runner,
        temp_parent=temp_parent,
        gws_executable=gws_executable,
    )
    if from_cli:
        print(json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")))
    return result

__all__ = [
    "AttendanceCopyScriptResult",
    "main",
    "prepare_attendance_copy_script",
    "verify_prepared_copied_script",
]

if __name__ == "__main__":
    raise SystemExit(2 if main().state == "hold" else 0)
