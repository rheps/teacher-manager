from __future__ import annotations

import base64
import json
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

from brity_bridge import process_win

OFFICE_REQUIRED_MESSAGE = "이 첨부파일을 읽으려면 Microsoft Office가 필요해요."
BROKEN_MESSAGE = "첨부파일을 읽을 수 없어요. 파일의 암호나 상태를 확인해 주세요."


@dataclass(frozen=True)
class OfficeReadResult:
    ok: bool
    text: str = ""
    message: str = ""


def extract_docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    paragraphs: list[str] = []
    for paragraph in root.findall(".//{*}p"):
        text = "".join(
            node.text or "" for node in paragraph.iter() if node.tag.endswith("}t")
        ).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def extract_pptx_text(path: Path) -> str:
    lines: list[str] = []
    with zipfile.ZipFile(path) as archive:
        names = [
            name
            for name in archive.namelist()
            if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
        ]
        names.sort(key=lambda name: int(re.search(r"slide(\d+)\.xml$", name).group(1)))
        for name in names:
            root = ET.fromstring(archive.read(name))
            text = " ".join(
                node.text.strip()
                for node in root.iter()
                if node.tag.endswith("}t") and node.text and node.text.strip()
            )
            if text:
                lines.append(text)
    return "\n".join(lines)


def _powershell_script(path: Path) -> str:
    encoded_path = base64.b64encode(str(Path(path).resolve()).encode("utf-8")).decode("ascii")
    suffix = path.suffix.lower()
    if suffix == ".doc":
        reader = r"""
$app = New-Object -ComObject Word.Application
$app.Visible = $false
$document = $app.Documents.Open($path, $false, $true)
$text = $document.Content.Text
"""
        cleanup = r"""
if ($null -ne $document) { $document.Close($false) }
if ($null -ne $app) { $app.Quit() }
"""
    elif suffix == ".xls":
        reader = r"""
$app = New-Object -ComObject Excel.Application
$app.Visible = $false
$app.DisplayAlerts = $false
$document = $app.Workbooks.Open($path, 0, $true)
$lines = New-Object System.Collections.Generic.List[string]
foreach ($sheet in $document.Worksheets) {
  $range = $sheet.UsedRange
  for ($row = 1; $row -le $range.Rows.Count; $row++) {
    $cells = New-Object System.Collections.Generic.List[string]
    for ($column = 1; $column -le $range.Columns.Count; $column++) {
      $value = $range.Cells.Item($row, $column).Text
      $cells.Add([string]$value)
    }
    if (($cells -join '').Trim()) { $lines.Add($cells -join "`t") }
  }
}
$text = $lines -join "`n"
"""
        cleanup = r"""
if ($null -ne $document) { $document.Close($false) }
if ($null -ne $app) { $app.Quit() }
"""
    elif suffix == ".ppt":
        reader = r"""
$app = New-Object -ComObject PowerPoint.Application
$document = $app.Presentations.Open($path, $true, $true, $false)
$lines = New-Object System.Collections.Generic.List[string]
foreach ($slide in $document.Slides) {
  foreach ($shape in $slide.Shapes) {
    if ($shape.HasTextFrame -and $shape.TextFrame.HasText) {
      $lines.Add([string]$shape.TextFrame.TextRange.Text)
    }
  }
}
$text = $lines -join "`n"
"""
        cleanup = r"""
if ($null -ne $document) { $document.Close() }
if ($null -ne $app) { $app.Quit() }
"""
    else:
        raise ValueError("구형 Office 형식이 아닙니다")
    return rf"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$path = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded_path}'))
$app = $null
$document = $null
try {{
{reader}
  @{{ok=$true;text=[string]$text}} | ConvertTo-Json -Compress
  exit 0
}} catch [System.Runtime.InteropServices.COMException] {{
  exit 4
}} catch {{
  exit 5
}} finally {{
{cleanup}
}}
"""


def _default_runner(script: str, timeout: float) -> tuple[int, str]:
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded,
        ],
        capture_output=True,
        text=True,
        # 스크립트의 UTF-8 선언과 짝 — 로케일(cp949) 의존 디코드는 파이썬 3.15
        # (UTF-8 기본화)에서 전 한국어 Windows가 한꺼번에 어긋난다.
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        **process_win.hidden_process_kwargs(),
    )
    # 디코드 실패로 리더 스레드가 죽으면 stdout이 None일 수 있다.
    return completed.returncode, completed.stdout or ""


def extract_legacy_office_text(path: Path, runner=None, timeout: float = 30.0) -> OfficeReadResult:
    run = runner or _default_runner
    try:
        code, output = run(_powershell_script(Path(path)), timeout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return OfficeReadResult(False, message=BROKEN_MESSAGE)
    if code == 4:
        return OfficeReadResult(False, message=OFFICE_REQUIRED_MESSAGE)
    if code != 0:
        return OfficeReadResult(False, message=BROKEN_MESSAGE)
    try:
        payload = json.loads((output or "").strip().splitlines()[-1])
    except (IndexError, ValueError):
        return OfficeReadResult(False, message=BROKEN_MESSAGE)
    text = str(payload.get("text") or "").strip() if isinstance(payload, dict) else ""
    if not text:
        return OfficeReadResult(False, message=BROKEN_MESSAGE)
    return OfficeReadResult(True, text=text)
