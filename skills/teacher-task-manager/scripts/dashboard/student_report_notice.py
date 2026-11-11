"""New student absence submissions as a Windows notification (2026-11-11 student link design, §13.1 Q-L11).

The helper asks the server whether anything changed (`/v1/data/check`). Only then it reads the year
(`/v1/data/read`) and looks at `studentReports`. A submission is announced once: its ID goes into a seen
set kept in the account folder. Nothing here writes to the server, and no note text, token or link is
read into a log (R17, LINK-02).
"""
import json
from pathlib import Path

from brity_bridge.atomic_io import atomic_write_text

SEEN_FILE = "student-report-seen.json"
CHECK_ROUTE = "/v1/data/check"
READ_ROUTE = "/v1/data/read"
MAX_SEEN = 3000
_KIND_WORD = {"결석함": "결석", "지각함": "지각", "조퇴함": "조퇴", "결과함": "결과",
              "결석": "결석", "지각": "지각", "조퇴": "조퇴", "결과": "결과"}


def _load(folder) -> dict:
    try:
        value = json.loads((Path(folder) / SEEN_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _save(folder, state) -> None:
    atomic_write_text(Path(folder) / SEEN_FILE, json.dumps(state, ensure_ascii=False))


def _object_list(value):
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def _josa_eul(word: str) -> str:
    code = ord(word[-1]) - 0xAC00 if word else -1
    return "을" if 0 <= code <= 11171 and code % 28 else "를"


def notice_text(report: dict, students: dict) -> str:
    student = students.get(str(report.get("studentId") or "")) or {}
    label = (str(student.get("number") or "") + str(student.get("name") or "")) if student.get("name") else ""
    label = label or str(report.get("studentLabel") or "").replace(" ", "") or "학생"
    kind = _KIND_WORD.get(str(report.get("kind") or ""), "결석")
    return f"{label} 학생이 {kind}{_josa_eul(kind)} 알렸어요."


def new_submissions(data_call, folder) -> list:
    """The notice texts of submissions this PC has not announced yet. An unread answer announces nothing."""
    state = _load(folder)
    check = data_call(CHECK_ROUTE, {}) or {}
    body = check.get("body") if isinstance(check.get("body"), dict) else None
    if check.get("status") != 200 or not body or body.get("error"):
        return []
    marker = [body.get("year"), body.get("revision")]
    if state.get("marker") == marker and isinstance(state.get("seen"), list):
        return []
    read = data_call(READ_ROUTE, {}) or {}
    answer = read.get("body") if isinstance(read.get("body"), dict) else None
    if read.get("status") != 200 or not answer or answer.get("error") or not answer.get("year"):
        return []
    seen = [str(x) for x in state.get("seen", []) if isinstance(x, str)]
    known = set(seen)
    students = {str(s.get("id")): s for s in _object_list(answer.get("students")) if s.get("id")}
    texts = []
    for report in sorted(_object_list(answer.get("studentReports")), key=lambda x: str(x.get("submittedAt") or "")):
        report_id = str(report.get("id") or report.get("reportId") or "")
        if not report_id or report_id in known:
            continue
        known.add(report_id)
        seen.append(report_id)
        if str(report.get("state") or "submitted") == "submitted":
            texts.append(notice_text(report, students))
    # The marker moves only with a complete read, so a partial one is read again next time.
    _save(folder, {"marker": marker if answer.get("complete") is not False else None, "seen": seen[-MAX_SEEN:]})
    return texts
