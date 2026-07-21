"""개발 중 화면 모양을 확인하는 가짜 Brity 창.

실행: python fake_brity.py  (skills/teacher-task-manager/scripts/brity_bridge 폴더에서)
실제 도우미는 BrityMessenger 화면만 직접 읽으므로 이 가짜 창에는 연결되지 않는다.
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brity_bridge.clipboard_win import ClipboardSnapshot, restore_snapshot
from brity_bridge.message_parse import build_cf_html_bytes

TABLE_HTML = (
    "<p>[교무부장] 2026-07-13 09:00</p>"
    "<table><tr><th>요일</th><th>당번</th></tr>"
    "<tr><td>수요일</td><td>급식 지도</td></tr>"
    "<tr><td>금요일</td><td>하교 지도</td></tr></table>"
)

MESSAGES = [
    {
        "label": "일반: 시험 감독 요청",
        "text": "[교무부장] 2026-07-10 15:42\n7월 14일 3교시 시험 감독 부탁드립니다.\n장소: 2학년 3반",
        "copy_allowed": True,
    },
    {
        "label": "여러 줄: 준비물 안내",
        "text": "[학년부장] 2026-07-11 08:20\n내일 준비물 안내\n- 체육복\n- 물통\n\n조회 때 안내해 주세요.",
        "copy_allowed": True,
    },
    {
        "label": "표: 당번표 (HTML)",
        "text": "copied text",
        "html": TABLE_HTML,
        "copy_allowed": True,
    },
    {
        "label": "보호 대화: 복사 금지",
        "text": "이 내용은 복사되면 안 됩니다",
        "copy_allowed": False,
    },
]


def main() -> None:
    root = tk.Tk()
    root.title("가짜 Brity (도우미 시험용)")
    root.geometry("420x260")

    listbox = tk.Listbox(root, font=("Malgun Gothic", 11))
    for message in MESSAGES:
        listbox.insert(tk.END, message["label"])
    listbox.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    def copy_message(message: dict) -> None:
        snapshot = ClipboardSnapshot(
            text=message["text"],
            html=build_cf_html_bytes(message["html"]) if message.get("html") else None,
        )
        restore_snapshot(snapshot)

    def show_menu(event) -> None:
        index = listbox.nearest(event.y)
        if index < 0:
            return
        listbox.selection_clear(0, tk.END)
        listbox.selection_set(index)
        message = MESSAGES[index]
        menu = tk.Menu(root, tearoff=0)
        menu.add_command(label="답장", command=lambda: None)
        menu.add_separator()
        if message["copy_allowed"]:
            menu.add_command(label="복사", command=lambda: copy_message(message))
        else:
            menu.add_command(label="복사", state="disabled")
        menu.add_command(label="삭제", command=lambda: None)
        menu.tk_popup(event.x_root, event.y_root)

    listbox.bind("<Button-3>", show_menu)
    root.mainloop()


if __name__ == "__main__":
    main()
