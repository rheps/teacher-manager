from __future__ import annotations

import base64
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from brity_bridge import attach_read, process_win
from brity_bridge.message_parse import MessageRecord, compute_source_hash

_LIST_ITEM_RE = re.compile(r"^\d+_\d+_\d+$")
_SIZE_RE = re.compile(r"^\d[\d.,]*\s?(KB|MB|GB)$", re.IGNORECASE)
_LINE_TOLERANCE = 5     # 같은 줄로 볼 y 오차(px)
_BOUNDARY_MARGIN = 10   # 목록 오른끝에서 본문까지 여유(px)
_ATTACHMENT_LABEL = "첨부파일"
_ATTACHMENT_ACTIONS = {"다운로드", "열기"}
_ATTACHMENT_SUMMARY_PARTS = {"총", "합계", "개", "(", ")", "[", "]"}
_CAPTURE_ATTEMPTS = 3
_ATTACHMENT_WRAP_GAP = 30
_ATTACHMENT_WRAP_X_TOLERANCE = 40


@dataclass
class ScreenElement:
    text: str
    ctrl: str
    x: int
    y: int
    w: int
    h: int
    aid: str


def parse_elements(raw: str) -> list[ScreenElement]:
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    elements: list[ScreenElement] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text:
            continue
        numbers = {}
        for key in ("x", "y", "w", "h"):
            value = item.get(key)
            numbers[key] = int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else -1
        elements.append(ScreenElement(
            text=text, ctrl=str(item.get("ctrl") or ""),
            x=numbers["x"], y=numbers["y"], w=max(numbers["w"], 0), h=max(numbers["h"], 0),
            aid=str(item.get("aid") or ""),
        ))
    return elements


def _group_lines(texts: list[ScreenElement]) -> list[list[ScreenElement]]:
    lines: list[list[ScreenElement]] = []
    for element in sorted(texts, key=lambda e: (e.y, e.x)):
        if lines and abs(element.y - lines[-1][0].y) <= _LINE_TOLERANCE:
            lines[-1].append(element)
        else:
            lines.append([element])
    return lines


def _content_elements(elements: list[ScreenElement]) -> list[ScreenElement]:
    """왼쪽 대화 목록을 빼고 오른쪽 본문에 속한 이름 있는 요소를 돌려준다."""
    named = [element for element in elements if element.text.strip()]
    list_edges = [
        element.x + element.w
        for element in elements
        if element.ctrl in ("Button", "ListItem") and _LIST_ITEM_RE.match(element.aid)
    ]
    if not list_edges:
        return named
    boundary = max(list_edges) + _BOUNDARY_MARGIN
    content = [element for element in named if element.x >= boundary]
    return content if any(element.ctrl == "Text" for element in content) else named


def _is_attachment_name_candidate(element: ScreenElement, size_x: int) -> bool:
    text = element.text.strip()
    if element.ctrl not in ("Text", "Button", "CheckBox") or element.x >= size_x:
        return False
    if text == _ATTACHMENT_LABEL or text in _ATTACHMENT_SUMMARY_PARTS:
        return False
    compact_text = re.sub(r"\s+", "", text)
    if any(compact_text.endswith(action) for action in _ATTACHMENT_ACTIONS):
        return False
    if _SIZE_RE.match(text) or text.isdecimal():
        return False
    return True


def _best_attachment_name_candidate(
    line: list[ScreenElement], size_x: int
) -> ScreenElement | None:
    candidates = [
        element for element in line
        if _is_attachment_name_candidate(element, size_x)
    ]
    return max(candidates, key=lambda element: len(element.text.strip()), default=None)


def _attachment_expected_count(elements: list[ScreenElement]) -> int | None:
    for line in _group_lines(_content_elements(elements)):
        ordered = [element.text.strip() for element in sorted(line, key=lambda item: item.x)]
        if _ATTACHMENT_LABEL not in ordered:
            continue
        for index, value in enumerate(ordered):
            combined = re.fullmatch(r"총\s*(\d+)", value)
            if combined:
                return int(combined.group(1))
            if value != "총":
                continue
            for following in ordered[index + 1 :]:
                if following.isdecimal():
                    return int(following)
                if following not in ("(", ")"):
                    break
        return None
    return None


def _has_attachment_evidence(elements: list[ScreenElement]) -> bool:
    content = _content_elements(elements)
    if any(
        element.ctrl == "CheckBox" and element.text.strip() == _ATTACHMENT_LABEL
        for element in content
    ):
        return True
    if any(
        element.ctrl == "Button" and element.text.strip() == "다운로드"
        for element in content
    ):
        return True
    lines = _group_lines(content)
    for line in lines:
        values = {element.text.strip() for element in line}
        has_size = any(_SIZE_RE.match(value) for value in values)
        if _ATTACHMENT_LABEL in values and has_size:
            return True
        if has_size and any(value in _ATTACHMENT_ACTIONS for value in values):
            return True
    return False


def assemble_message(elements: list[ScreenElement]) -> tuple[str, list[str]]:
    """요소 덤프에서 (본문 텍스트, 첨부파일명 목록)을 만든다. 순수 함수 — 시험 대상."""
    content = _content_elements(elements)
    text_lines = _group_lines([element for element in content if element.ctrl == "Text"])
    body = "\n".join(
        " ".join(element.text.strip() for element in line) for line in text_lines
    ).strip()
    names: list[str] = []
    attachment_section_seen = False
    pending_fragment: tuple[int, ScreenElement] | None = None
    for line in _group_lines(content):
        if any(element.text.strip() == _ATTACHMENT_LABEL for element in line):
            attachment_section_seen = True
            pending_fragment = None
            continue  # 첨부 개수·전체 용량 머리줄은 실제 파일 행이 아니다.
        sizes = [element for element in line if _SIZE_RE.match(element.text.strip())]
        if not sizes:
            if attachment_section_seen:
                candidate = _best_attachment_name_candidate(line, 2**31 - 1)
                pending_fragment = (line[0].y, candidate) if candidate is not None else None
            continue
        size_x = min(element.x for element in sizes)
        candidate = _best_attachment_name_candidate(line, size_x)
        parts: list[str] = []
        if pending_fragment is not None:
            previous_y, previous = pending_fragment
            current_x = candidate.x if candidate is not None else previous.x
            if (
                0 < line[0].y - previous_y <= _ATTACHMENT_WRAP_GAP
                and abs(previous.x - current_x) <= _ATTACHMENT_WRAP_X_TOLERANCE
            ):
                parts.append(previous.text.strip())
        if candidate is not None:
            parts.append(candidate.text.strip())
        if parts:
            names.append(" ".join(parts))
        pending_fragment = None
    return body, names


_PS_SCRIPT = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type -Namespace Native -Name Win -MemberDefinition @'
[DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
[DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
[DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
'@
$procName = "__PROC__"
$target = [IntPtr]::Zero
$fg = [Native.Win]::GetForegroundWindow()
if ($fg -ne [IntPtr]::Zero) {
  $fgPid = [uint32]0
  [Native.Win]::GetWindowThreadProcessId($fg, [ref]$fgPid) | Out-Null
  $p = Get-Process -Id $fgPid -ErrorAction SilentlyContinue
  if ($p -and $p.ProcessName -ieq $procName) { $target = $fg }
}
if ($target -eq [IntPtr]::Zero) {
  $p = Get-Process -Name $procName -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -ne "" -and $_.MainWindowTitle -ne "Log in" } |
    Select-Object -First 1
  if ($p) { $target = $p.MainWindowHandle }
}
if ($target -eq [IntPtr]::Zero) { Write-Output "[]"; exit 3 }
[Native.Win]::SendMessage($target, 0x003D, [IntPtr]::Zero, [IntPtr](-4)) | Out-Null
[Native.Win]::SendMessage($target, 0x003D, [IntPtr]::Zero, [IntPtr](-25)) | Out-Null
$root = [System.Windows.Automation.AutomationElement]::FromHandle($target)
$found = $null
for ($i = 0; $i -lt 6; $i++) {
  Start-Sleep -Milliseconds 700
  $found = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
  if ($found.Count -gt 30) { break }
}
$out = New-Object System.Collections.ArrayList
foreach ($e in $found) {
  $n = $e.Current.Name
  if (-not $n) { continue }
  $x = -1; $y = -1; $w = 0; $h = 0
  try {
    $r = $e.Current.BoundingRectangle
    if (-not [double]::IsInfinity($r.X)) { $x = [int]$r.X; $y = [int]$r.Y; $w = [int]$r.Width; $h = [int]$r.Height }
  } catch {}
  [void]$out.Add(@{ text = $n; ctrl = ($e.Current.ControlType.ProgrammaticName -replace 'ControlType\.', ''); x = $x; y = $y; w = $w; h = $h; aid = [string]$e.Current.AutomationId })
}
Write-Output (ConvertTo-Json -InputObject @($out) -Compress -Depth 3)
"""

_MIN_BODY_CHARS = 10
_BRITY_PROCESS_NAME = "BrityMessenger"


@dataclass
class ScreenCapture:
    ok: bool
    reason: str
    body: str
    attachments: list[str]


def _default_ps_runner(script: str, timeout: float) -> tuple[int, str]:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return process_win.run_captured(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-EncodedCommand", encoded],
        timeout=timeout,
    )


def capture_brity_text(runner=None, timeout: float = 20.0) -> ScreenCapture:
    runner = runner or _default_ps_runner
    script = _PS_SCRIPT.replace("__PROC__", _BRITY_PROCESS_NAME)
    no_attachment: ScreenCapture | None = None
    attachment_failure: ScreenCapture | None = None
    last_failure = ScreenCapture(False, "화면 요소를 받지 못했습니다", "", [])

    for _attempt in range(_CAPTURE_ATTEMPTS):
        try:
            code, output = runner(script, timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            return ScreenCapture(False, f"화면 읽기 실행 실패: {error}", "", [])
        if code == 3:
            return ScreenCapture(False, "로그인된 Brity 창을 찾지 못했습니다", "", [])
        if code != 0:
            return ScreenCapture(False, f"화면 읽기 도구가 실패했습니다(코드 {code})", "", [])
        start, end = output.find("["), output.rfind("]")
        if start == -1 or end <= start:
            last_failure = ScreenCapture(False, "화면 요소를 받지 못했습니다", "", [])
            continue
        elements = parse_elements(output[start : end + 1])
        body, names = assemble_message(elements)
        if len(body) < _MIN_BODY_CHARS:
            last_failure = ScreenCapture(False, "읽은 내용이 너무 짧습니다", "", [])
            continue
        if _has_attachment_evidence(elements):
            expected_count = _attachment_expected_count(elements)
            names_complete = bool(names)
            if expected_count is not None:
                names_complete = len(names) == expected_count
            if names_complete:
                return ScreenCapture(True, "", body, names)
            attachment_failure = ScreenCapture(
                False, "첨부파일 이름을 읽지 못했습니다", body, []
            )
            continue
        candidate = ScreenCapture(True, "", body, [])
        if no_attachment is None or len(candidate.body) > len(no_attachment.body):
            no_attachment = candidate

    # 한 번이라도 첨부 흔적을 봤다면, 다른 읽기에서 잠깐 사라졌어도 본문만 등록하지 않는다.
    if attachment_failure is not None:
        return attachment_failure
    if no_attachment is not None:
        return no_attachment
    return last_failure


def capture_failure_message(reason: str) -> str:
    """실패 이유에 맞춰 사용자가 바로 다시 시도할 수 있게 안내한다."""
    if "찾지 못" in reason:
        return "브리티 메신저를 열고 읽을 대화방을 띄운 뒤 단축키를 다시 눌러 주세요."
    if "첨부파일 이름" in reason:
        return "첨부파일 목록이 모두 보이게 한 뒤 단축키를 다시 눌러 주세요."
    if "너무 짧" in reason or "요소를 받지 못" in reason:
        return "읽을 메시지가 화면에 보이게 한 뒤 단축키를 다시 눌러 주세요."
    return (
        "브리티 대화방과 메시지를 바꾸지 말고 단축키를 다시 눌러 주세요. "
        "다른 프로그램을 앞으로 띄우는 것은 괜찮아요."
    )


def build_screen_record(
    capture: ScreenCapture,
    download_dir: Path,
    attachment_paths: tuple[Path, ...] | None = None,
) -> tuple[MessageRecord, str]:
    """캡처 결과를 본문과 읽을 수 있는 첨부 묶음이 포함된 기록으로 만든다."""
    if attachment_paths is None:
        bundle = attach_read.prepare_attachment_bundle(Path(download_dir), capture.attachments)
    else:
        bundle = attach_read.prepare_resolved_attachment_bundle(attachment_paths)
    plain = capture.body if not bundle.block else capture.body + "\n\n" + bundle.block
    identity_text = "\n".join([capture.body, *bundle.fingerprints])
    record = MessageRecord(
        source="brity-screen",
        sender="",
        sent_at="",
        plain_text=plain,
        html="",
        source_hash=compute_source_hash("", "", identity_text),
        attachment_count=bundle.count,
        attachment_names=bundle.names,
        media_parts=bundle.media_parts,
    )
    note = ""
    if bundle.skipped_names:
        note = "읽을 수 없는 첨부파일은 제외하고 처리했습니다: " + ", ".join(bundle.skipped_names)
    return record, note
