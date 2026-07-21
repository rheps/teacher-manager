"""영속 파일의 원자 교체 쓰기 — 쓰다 만 파일이 절대 남지 않게 한다.

제자리 쓰기(open('w')/write_text)는 truncate와 write 완료 사이에 프로세스가
죽으면 잘린 파일을 남긴다. 설정·기억·기록이 전부 이 방식이어서 도우미
정지·전원 단절 한 번에 Gemini 키가 증발하거나 중복 방지 기억이 사라졌다
(2026-07-21 재검증 unit-09). 같은 폴더의 임시 파일에 전부 쓰고 fsync한 뒤
os.replace로 바꿔치면, 어느 순간에 죽어도 파일은 이전 완본 아니면 새 완본
둘 중 하나다.
"""
from __future__ import annotations

import os
from pathlib import Path


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 도우미는 단일 실행(뮤텍스)이고 파일마다 작성자가 하나라 고정 이름이면 충분하다.
    tmp = path.with_name(path.name + ".tmp-write")
    try:
        with open(tmp, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
