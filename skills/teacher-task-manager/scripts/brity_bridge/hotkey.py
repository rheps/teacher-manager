from __future__ import annotations

from dataclasses import dataclass

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

MODIFIER_ORDER = ("ctrl", "alt", "shift", "win")
_MODIFIERS = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}


@dataclass(frozen=True)
class HotkeySpec:
    text: str
    parts: tuple[str, ...]
    modifiers: int
    key_name: str | None
    key_code: int | None

    @property
    def modifier_only(self) -> bool:
        return self.key_name is None


def _ordinary_key_code(name: str) -> int | None:
    if len(name) == 1 and (name.isalpha() or name.isdigit()):
        return ord(name.upper())
    if name.startswith("f") and name[1:].isdigit():
        number = int(name[1:])
        if 1 <= number <= 12:
            return 0x70 + number - 1
    return None


def parse_hotkey(text: str) -> HotkeySpec:
    parts = [part.strip().lower() for part in str(text or "").split("+")]
    if not parts or any(not part for part in parts) or len(set(parts)) != len(parts):
        raise ValueError(f"단축키 형식이 잘못됨: {text!r}")

    modifiers = [part for part in parts if part in _MODIFIERS]
    ordinary = [part for part in parts if part not in _MODIFIERS]
    if len(ordinary) > 1:
        raise ValueError(f"일반 키는 하나만 쓸 수 있음: {text!r}")
    if ordinary and not modifiers:
        raise ValueError("보조키를 하나 이상 함께 눌러 주세요")
    if not ordinary and len(modifiers) < 2:
        raise ValueError("문자 없는 단축키는 보조키를 두 개 이상 눌러 주세요")

    key_name = ordinary[0] if ordinary else None
    key_code = _ordinary_key_code(key_name) if key_name else None
    if key_name and key_code is None:
        raise ValueError(f"지원하지 않는 키: {key_name!r}")

    ordered_modifiers = [name for name in MODIFIER_ORDER if name in modifiers]
    canonical_parts = tuple(ordered_modifiers + ([key_name] if key_name else []))
    flags = 0
    for name in ordered_modifiers:
        flags |= _MODIFIERS[name]
    return HotkeySpec(
        text="+".join(canonical_parts),
        parts=canonical_parts,
        modifiers=flags,
        key_name=key_name,
        key_code=key_code,
    )
