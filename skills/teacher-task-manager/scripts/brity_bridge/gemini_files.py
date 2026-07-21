from __future__ import annotations

import base64
import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from brity_bridge.message_parse import MediaPart

INLINE_LIMIT = 8 * 1024 * 1024
FILES_ROOT = "https://generativelanguage.googleapis.com/v1beta"
UPLOAD_ROOT = "https://generativelanguage.googleapis.com/upload/v1beta/files"


@dataclass(frozen=True)
class PreparedMedia:
    parts: tuple[dict, ...]
    _cleanups: tuple[object, ...] = ()

    def cleanup(self) -> None:
        for callback in reversed(self._cleanups):
            try:
                callback()
            except OSError:
                pass


def _request_json(request: urllib.request.Request, timeout: float = 60.0) -> tuple[dict, object]:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8") or "{}")
        return payload, response.headers


def _file_url(name: str, api_key: str) -> str:
    return f"{FILES_ROOT}/{name}?{urllib.parse.urlencode({'key': api_key})}"


def _wait_until_active(file_info: dict, api_key: str) -> dict:
    state = str(file_info.get("state") or "ACTIVE")
    name = str(file_info.get("name") or "")
    for _attempt in range(30):
        if state == "ACTIVE":
            return file_info
        if state == "FAILED" or not name:
            raise OSError("Gemini 임시 파일 처리가 실패했습니다")
        time.sleep(1)
        request = urllib.request.Request(_file_url(name, api_key), method="GET")
        file_info, _headers = _request_json(request)
        state = str(file_info.get("state") or "")
    raise OSError("Gemini 임시 파일 준비 시간이 초과됐습니다")


def _default_uploader(part: MediaPart, api_key: str) -> tuple[dict, object]:
    start_url = f"{UPLOAD_ROOT}?{urllib.parse.urlencode({'key': api_key})}"
    metadata = json.dumps({"file": {"display_name": part.name}}, ensure_ascii=False).encode("utf-8")
    start = urllib.request.Request(
        start_url,
        data=metadata,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(len(part.data)),
            "X-Goog-Upload-Header-Content-Type": part.mime_type,
        },
    )
    with urllib.request.urlopen(start, timeout=60.0) as response:
        upload_url = response.headers.get("X-Goog-Upload-URL", "")
    if not upload_url:
        raise OSError("Gemini 임시 업로드 주소를 받지 못했습니다")

    upload = urllib.request.Request(
        upload_url,
        data=part.data,
        method="POST",
        headers={
            "Content-Length": str(len(part.data)),
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize",
        },
    )
    payload, _headers = _request_json(upload)
    file_info = _wait_until_active(payload.get("file") or payload, api_key)
    name = str(file_info.get("name") or "")
    uri = str(file_info.get("uri") or "")
    mime_type = str(file_info.get("mimeType") or file_info.get("mime_type") or part.mime_type)
    if not name or not uri:
        raise OSError("Gemini 임시 파일 정보를 받지 못했습니다")

    def cleanup() -> None:
        request = urllib.request.Request(_file_url(name, api_key), method="DELETE")
        with urllib.request.urlopen(request, timeout=30.0):
            pass

    return {
        "file_data": {"mime_type": mime_type, "file_uri": uri}
    }, cleanup


def prepare_media_parts(
    media_parts: tuple[MediaPart, ...],
    api_key: str,
    uploader=None,
) -> PreparedMedia:
    upload = uploader or _default_uploader
    parts: list[dict] = []
    cleanups: list[object] = []
    for media in media_parts:
        if len(media.data) <= INLINE_LIMIT:
            parts.append(
                {
                    "inline_data": {
                        "mime_type": media.mime_type,
                        "data": base64.b64encode(media.data).decode("ascii"),
                    }
                }
            )
            continue
        request_part, cleanup = upload(media, api_key)
        parts.append(request_part)
        cleanups.append(cleanup)
    return PreparedMedia(tuple(parts), tuple(cleanups))
