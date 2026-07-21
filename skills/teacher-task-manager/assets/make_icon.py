"""티처 매니저 브랜드 아이콘(.ico) 생성기 — stdlib만. 한 번 돌려 teacher-manager.ico를 만든다.

투명 배경 + 토스 블루(#3182F6) 체크 하나 — 끝은 둥글고 굵기는 일정하다.
(구글 Tasks의 "파란 타일 + 흰 체크"와 구분되도록 타일 없이 체크만 쓴다.)
4배 슈퍼샘플링으로 가장자리를 부드럽게 한다. 런타임에서는 결과 .ico만 쓴다(이 스크립트는 재현용).
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

BLUE = (0x31, 0x82, 0xF6)
SIZES = (256, 48, 32, 16)
SS = 4  # 슈퍼샘플 배수

# 예전 타일 안 체크와 같은 꼴을 1.75배로 키워 캔버스를 채운다 (중심 0.51,0.51 기준 확대).
_CHECK = ((0.1425, 0.5625), (0.3875, 0.79), (0.8775, 0.23))
_CHECK_HALF = 0.1015  # 획 반두께 — 굵기 일정, 끝은 세그먼트 거리 특성상 자동으로 둥글다


def _dist_seg(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    cx, cy = ax + t * dx, ay + t * dy
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5


def _check_inside(u: float, v: float) -> bool:
    (ax, ay), (bx, by), (cx, cy) = _CHECK
    d = min(_dist_seg(u, v, ax, ay, bx, by), _dist_seg(u, v, bx, by, cx, cy))
    return d <= _CHECK_HALF


def _subpixel(u: float, v: float):
    if _check_inside(u, v):
        return (BLUE[0], BLUE[1], BLUE[2], 255)
    return (BLUE[0], BLUE[1], BLUE[2], 0)  # 투명하되 색은 파랑 — 가장자리 어두워짐 방지


def _render_rgba(size: int) -> bytes:
    big = size * SS
    out = bytearray(size * size * 4)
    inv = 1.0 / big
    for py in range(size):
        for px in range(size):
            r = g = b = a = 0
            for sy in range(SS):
                vy = (py * SS + sy + 0.5) * inv
                for sx in range(SS):
                    vx = (px * SS + sx + 0.5) * inv
                    pr, pg, pb, pa = _subpixel(vx, vy)
                    r += pr
                    g += pg
                    b += pb
                    a += pa
            n = SS * SS
            i = (py * size + px) * 4
            out[i] = r // n
            out[i + 1] = g // n
            out[i + 2] = b // n
            out[i + 3] = a // n
    return bytes(out)


def _chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def _png(size: int, rgba: bytes) -> bytes:
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    raw = bytearray()
    for row in range(size):
        raw.append(0)  # filter None
        raw.extend(rgba[row * size * 4:(row + 1) * size * 4])
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _chunk(b"IEND", b"")
    )


def build_ico() -> bytes:
    images = [(size, _png(size, _render_rgba(size))) for size in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = bytearray()
    blobs = bytearray()
    for size, png in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        blobs += png
        offset += len(png)
    return header + bytes(entries) + bytes(blobs)


if __name__ == "__main__":
    target = Path(__file__).resolve().parent / "teacher-manager.ico"
    target.write_bytes(build_ico())
    print(f"wrote {target} ({target.stat().st_size} bytes)")
