from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_GWS_COMMAND = ["gws"]
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
