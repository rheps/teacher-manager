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


class UploadUncertainError(OSError):
    """The upload body may have arrived, but its exact file reply was lost."""

    def __init__(self, message: str, verify=None, cleanup=None):
        super().__init__(message)
        self.verify = verify
        self.cleanup = cleanup


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
    try:
        payload, _headers = _request_json(upload)
    except (OSError, ValueError, TypeError) as error:
        # finalize 요청 본문을 보낸 뒤의 오류다. 같은 내용을 다시 올리면 원격
        # 임시 파일이 둘이 될 수 있으므로 호출자가 확인 전 재업로드하지 못하게 한다.
        raise UploadUncertainError("Gemini 임시 업로드 결과를 확인하지 못했습니다") from error
    file_info = payload.get("file") or payload
    name = str(file_info.get("name") or "")
    if not name:
        raise OSError("Gemini 임시 파일 정보를 받지 못했습니다")

    def cleanup() -> None:
        request = urllib.request.Request(_file_url(name, api_key), method="DELETE")
        with urllib.request.urlopen(request, timeout=30.0):
            pass

    def prepared_from(info: dict) -> PreparedMedia:
        verified_name = str(info.get("name") or "")
        uri = str(info.get("uri") or "")
        mime_type = str(
            info.get("mimeType") or info.get("mime_type") or part.mime_type
        )
        if verified_name != name or not uri:
            raise OSError("Gemini 임시 파일 정보를 확인하지 못했습니다")
        return PreparedMedia(
            (
                {
                    "file_data": {
                        "mime_type": mime_type,
                        "file_uri": uri,
                    }
                },
            ),
            (cleanup,),
        )

    try:
        active_info = _wait_until_active(file_info, api_key)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        def verify() -> PreparedMedia:
            return prepared_from(_wait_until_active(file_info, api_key))

        raise UploadUncertainError(
            "Gemini 임시 파일 상태를 확인하지 못했습니다",
            verify=verify,
            cleanup=cleanup,
        ) from error
    prepared = prepared_from(active_info)
    return prepared.parts[0], cleanup


def prepare_media_parts(
    media_parts: tuple[MediaPart, ...],
    api_key: str,
    uploader=None,
) -> PreparedMedia:
    upload = uploader or _default_uploader
    parts: list[dict] = []
    cleanups: list[object] = []
    next_index = 0
    pending_error: UploadUncertainError | None = None

    def continue_preparation():
        nonlocal next_index, pending_error
        if pending_error is not None:
            verify = pending_error.verify
            if not callable(verify):
                return None
            verified = verify()
            if not isinstance(verified, PreparedMedia):
                return verified
            parts.extend(verified.parts)
            cleanups.extend(verified._cleanups)
            pending_error = None
            next_index += 1

        while next_index < len(media_parts):
            media = media_parts[next_index]
            if len(media.data) <= INLINE_LIMIT:
                parts.append(
                    {
                        "inline_data": {
                            "mime_type": media.mime_type,
                            "data": base64.b64encode(media.data).decode("ascii"),
                        }
                    }
                )
                next_index += 1
                continue
            try:
                request_part, cleanup = upload(media, api_key)
            except UploadUncertainError as error:
                pending_error = error
                raise
            parts.append(request_part)
            cleanups.append(cleanup)
            next_index += 1
        return PreparedMedia(tuple(parts), tuple(cleanups))

    def cleanup_all() -> None:
        current_cleanup = (
            (pending_error.cleanup,)
            if pending_error is not None and callable(pending_error.cleanup)
            else ()
        )
        PreparedMedia((), tuple(cleanups) + current_cleanup).cleanup()

    try:
        return continue_preparation()
    except UploadUncertainError as error:
        def verify_all():
            return continue_preparation()

        raise UploadUncertainError(
            str(error),
            verify=verify_all if callable(error.verify) else None,
            cleanup=cleanup_all,
        ) from error
    except Exception:
        cleanup_all()
        raise
