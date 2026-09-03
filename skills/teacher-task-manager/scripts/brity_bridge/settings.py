from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# 새 설치는 PATH의 gws를 절대 기본 실행 대상으로 삼지 않는다. 이 목록 필드는
# 예전 settings.json을 읽고 다시 저장하기 위해서만 남겨 둔다.
DEFAULT_GWS_COMMAND: list[str] = []
ALLOWED_GEMINI_MODELS = ("gemini-3.5-flash", "gemini-3.1-flash-lite")
DEFAULT_GEMINI_MODEL = ALLOWED_GEMINI_MODELS[0]
DEFAULT_HOTKEY = "ctrl+alt+win"


def normalize_gemini_model(value: object) -> str:
    return value if isinstance(value, str) and value in ALLOWED_GEMINI_MODELS else DEFAULT_GEMINI_MODEL


@dataclass
class BridgeSettings:
    hotkey: str = DEFAULT_HOTKEY
    gemini_api_key: str = ""
    gemini_model: str = DEFAULT_GEMINI_MODEL
    brity_download_dir: str = r"C:\BrityWorks\BrityMessenger\download"
    # 실패 화면이 뜰 때 개발자에게 자동 보고할지. 기본은 켬(2026-09-02 사용자 결정).
    error_reports_enabled: bool = True
    # 예전 settings.json을 잃지 않고 다시 저장하기 위한 읽기 전용 호환 자료다.
    # 실행할 때는 이 값을 쓰지 않고 tool_runtime이 검증한 전체 경로만 쓴다.
    gws_command: list[str] = field(default_factory=lambda: list(DEFAULT_GWS_COMMAND))


def load_settings(path: Path) -> BridgeSettings:
    loaded = BridgeSettings()
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return loaded
    if not isinstance(raw, dict):
        return loaded
    for key, value in raw.items():
        if not hasattr(loaded, key):
            continue
        default = getattr(loaded, key)
        if isinstance(default, bool):
            if isinstance(value, bool):
                setattr(loaded, key, value)
        elif isinstance(default, float):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                setattr(loaded, key, float(value))
        elif isinstance(default, str):
            if isinstance(value, str):
                setattr(loaded, key, value)
        elif isinstance(default, list):
            if isinstance(value, list):
                setattr(loaded, key, value)
    loaded.gemini_model = normalize_gemini_model(loaded.gemini_model)
    try:
        from brity_bridge.hotkey import parse_hotkey

        loaded.hotkey = parse_hotkey(loaded.hotkey).text
    except ValueError:
        loaded.hotkey = DEFAULT_HOTKEY
    return loaded


def save_settings(path: Path, bridge_settings: BridgeSettings) -> None:
    # 설정 화면이 변경마다 저장해 쓰기 창이 상시 열려 있다 — 원자 교체가 아니면
    # 저장 중 죽는 순간 Gemini 키·단축키가 잘린 파일과 함께 증발한다.
    from brity_bridge import atomic_io

    atomic_io.atomic_write_text(
        Path(path), json.dumps(asdict(bridge_settings), ensure_ascii=False, indent=2) + "\n"
    )
