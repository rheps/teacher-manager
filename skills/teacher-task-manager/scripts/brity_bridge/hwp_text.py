"""옛 .hwp(5.x 바이너리)에서 본문 텍스트만 뽑는다. 표준 라이브러리 전용.

HWP 5.x = CFB(OLE 복합 문서) 컨테이너 + BodyText/Section{n} 스트림(레코드 나열).
전체 서식 변환이 아니라 Gemini 분석용 텍스트 추출이 목적이다.
"""
from __future__ import annotations

import struct
import zlib

_CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_FREE = 0xFFFFFFFF
_END = 0xFFFFFFFE
_HWPTAG_PARA_TEXT = 67
_NEWLINE_UTF16 = "\n".encode("utf-16-le")


class HwpReadError(Exception):
    pass


def _read_chain(raw: bytes, start: int, fat: list[int], sector_size: int) -> bytes:
    out = bytearray()
    sector, guard = start, 0
    while sector not in (_END, _FREE):
        if sector >= len(fat) or guard > len(fat):
            raise HwpReadError("FAT 사슬이 잘못됨")
        offset = 512 + sector * sector_size
        out += raw[offset : offset + sector_size]
        sector = fat[sector]
        guard += 1
    return bytes(out)


def read_cfb_streams(raw: bytes) -> dict[str, bytes]:
    if raw[:8] != _CFB_MAGIC:
        raise HwpReadError("CFB 파일이 아님")
    major = struct.unpack_from("<H", raw, 26)[0]
    sector_size = 1 << struct.unpack_from("<H", raw, 30)[0]
    mini_size = 1 << struct.unpack_from("<H", raw, 32)[0]
    dir_start = struct.unpack_from("<I", raw, 48)[0]
    mini_cutoff = struct.unpack_from("<I", raw, 56)[0]
    minifat_start = struct.unpack_from("<I", raw, 60)[0]
    num_minifat = struct.unpack_from("<I", raw, 64)[0]
    difat_start = struct.unpack_from("<I", raw, 68)[0]
    num_difat = struct.unpack_from("<I", raw, 72)[0]
    per_sector = sector_size // 4

    difat = list(struct.unpack_from("<109I", raw, 76))
    sector, guard = difat_start, 0
    while sector not in (_END, _FREE) and guard <= num_difat:
        offset = 512 + sector * sector_size
        entries = struct.unpack_from(f"<{per_sector}I", raw, offset)
        difat += entries[:-1]
        sector = entries[-1]
        guard += 1

    fat: list[int] = []
    for fat_sector in difat:
        if fat_sector in (_END, _FREE):
            continue
        offset = 512 + fat_sector * sector_size
        fat += struct.unpack_from(f"<{per_sector}I", raw, offset)

    directory = _read_chain(raw, dir_start, fat, sector_size)
    entries = []
    for pos in range(0, len(directory), 128):
        chunk = directory[pos : pos + 128]
        if len(chunk) < 128:
            break
        name_len = struct.unpack_from("<H", chunk, 64)[0]
        if name_len < 2 or name_len > 64:
            continue
        name = chunk[: name_len - 2].decode("utf-16-le", errors="replace")
        kind = chunk[66]
        start = struct.unpack_from("<I", chunk, 116)[0]
        size = struct.unpack_from("<Q", chunk, 120)[0]
        if major == 3:
            size &= 0xFFFFFFFF
        entries.append((name, kind, start, size))

    root = next((e for e in entries if e[1] == 5), None)
    if root is None:
        raise HwpReadError("루트 항목이 없음")
    mini_container = _read_chain(raw, root[2], fat, sector_size) if root[3] else b""

    minifat: list[int] = []
    sector, guard = minifat_start, 0
    while sector not in (_END, _FREE) and guard <= num_minifat:
        offset = 512 + sector * sector_size
        minifat += struct.unpack_from(f"<{per_sector}I", raw, offset)
        sector = fat[sector] if sector < len(fat) else _END
        guard += 1

    streams: dict[str, bytes] = {}
    for name, kind, start, size in entries:
        if kind != 2:
            continue
        if size >= mini_cutoff:
            data = _read_chain(raw, start, fat, sector_size)
        else:
            out = bytearray()
            mini_sector, guard = start, 0
            while mini_sector not in (_END, _FREE) and guard <= len(minifat):
                out += mini_container[mini_sector * mini_size : (mini_sector + 1) * mini_size]
                mini_sector = minifat[mini_sector] if mini_sector < len(minifat) else _END
                guard += 1
            data = bytes(out)
        streams[name] = data[: size]
    return streams


def _iter_records(data: bytes):
    pos = 0
    while pos + 4 <= len(data):
        header = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        tag = header & 0x3FF
        size = (header >> 20) & 0xFFF
        if size == 0xFFF:
            if pos + 4 > len(data):
                return
            size = struct.unpack_from("<I", data, pos)[0]
            pos += 4
        yield tag, data[pos : pos + size]
        pos += size


def _para_text_to_str(payload: bytes) -> str:
    kept = bytearray()
    units = len(payload) // 2
    i = 0
    while i < units:
        code = struct.unpack_from("<H", payload, i * 2)[0]
        if code in (10, 13):
            kept += _NEWLINE_UTF16
            i += 1
        elif 1 <= code <= 23:
            i += 8            # 인라인·확장 컨트롤은 8칸을 차지한다
        elif code < 32:
            i += 1
        else:
            kept += payload[i * 2 : i * 2 + 2]
            i += 1
    return kept.decode("utf-16-le", errors="ignore")


def extract_hwp_text(raw: bytes) -> tuple[bool, str]:
    try:
        streams = read_cfb_streams(raw)
    except (HwpReadError, struct.error, IndexError, ValueError):
        return False, "hwp 컨테이너를 읽지 못함"
    header = streams.get("FileHeader", b"")
    if len(header) < 40 or not header.startswith(b"HWP Document File"):
        return False, "hwp 파일이 아님"
    flags = struct.unpack_from("<I", header, 36)[0]
    if flags & 0x2 or flags & 0x4:
        return False, "보안 문서라 내용을 읽을 수 없음"
    compressed = bool(flags & 0x1)
    texts: list[str] = []
    index = 0
    while True:
        name = f"Section{index}"
        if name not in streams:
            break
        data = streams[name]
        if compressed:
            try:
                data = zlib.decompress(data, -15)
            except zlib.error:
                return False, "본문 압축을 풀지 못함"
        for tag, payload in _iter_records(data):
            if tag == _HWPTAG_PARA_TEXT:
                text = _para_text_to_str(payload)
                if text.strip():
                    texts.append(text.strip())
        index += 1
    if not texts:
        return False, "본문 텍스트를 찾지 못함"
    return True, "\n".join(texts)
