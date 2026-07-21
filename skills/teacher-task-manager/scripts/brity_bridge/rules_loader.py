from __future__ import annotations

from pathlib import Path

RULE_REFERENCES = ("1_quick_check.md", "2_calendar_selection.md", "3_time_analysis.md")
WORKFLOW_HEADING = "## 워크플로우"

_CACHE: dict[str, str] = {}


def _workflow_section(skill_text: str) -> str:
    if WORKFLOW_HEADING not in skill_text:
        raise ValueError(f"SKILL.md에 '{WORKFLOW_HEADING}' 절이 없습니다.")
    start = skill_text.index(WORKFLOW_HEADING)
    end = skill_text.find("\n## ", start + len(WORKFLOW_HEADING))
    return skill_text[start:] if end < 0 else skill_text[start:end]


def load_analysis_rules(skill_root: Path) -> str:
    """SKILL.md 워크플로우 절 + references 1~3을 분석 프롬프트용 규칙 텍스트로 합친다.

    Gemini API는 파일을 스스로 읽지 못하므로 Python이 읽어 프롬프트에 넣는다.
    파일은 프로세스당 1회만 읽는다(도우미는 상주 프로그램이라 매 호출 재독이 낭비).
    """
    root = Path(skill_root).resolve()
    key = str(root)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    skill_text = (root / "SKILL.md").read_text(encoding="utf-8")
    parts = [_workflow_section(skill_text)]
    for name in RULE_REFERENCES:
        parts.append(f"\n\n<!-- references/{name} -->\n")
        parts.append((root / "references" / name).read_text(encoding="utf-8"))
    rules = "".join(parts)
    _CACHE[key] = rules
    return rules
