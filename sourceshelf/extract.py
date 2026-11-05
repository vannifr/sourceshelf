import html
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


class MissingTool(Exception):
    pass


HINTS = {"pdftotext": "install poppler-utils (poppler)", "rg": "install ripgrep"}


def run_tool(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, check=False, **kwargs)
    except FileNotFoundError as error:
        name = argv[0]
        raise MissingTool(f"{name} not found: {HINTS.get(name, 'install it')} and make sure it is on PATH") from error


def pdf_to_text(pdf: Path, target: Path) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    proc = run_tool(["pdftotext", "-layout", str(pdf), str(target)], capture_output=True)
    if proc.returncode != 0 or not target.exists():
        return False
    return bool(target.read_text(encoding="utf-8", errors="ignore").replace("\f", "").strip())


def html_to_text(content: str) -> str:
    clean = re.sub(r"(?is)<(script|style).*?</\1>", "", content)
    clean = html.unescape(re.sub(r"<[^>]+>", " ", clean))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", clean)).strip()


def html_title(content: str) -> str | None:
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", content)
    if not m:
        return None
    return re.sub(r"\s+", " ", html.unescape(m.group(1))).strip() or None


MAX_DOCX_XML = 100 * 1024 * 1024
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_to_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            if z.getinfo("word/document.xml").file_size > MAX_DOCX_XML:
                raise ValueError("docx text part too large")
            xml = z.read("word/document.xml")
        if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
            raise ValueError("docx contains a DTD or entity declaration (refused)")
        root_el = ET.fromstring(xml)
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as error:
        raise ValueError(f"not a readable docx file ({error})") from error
    paragraphs = [
        "".join(t.text or "" for t in p.iter(f"{W_NS}t")) for p in root_el.iter(f"{W_NS}p")
    ]
    text = "\n".join(a for a in paragraphs if a.strip())
    if not text.strip():
        raise ValueError("docx contains no text")
    return text


def subtitle_to_text(content: str) -> str:
    lines = []
    for line in content.splitlines():
        r = line.strip()
        if not r or r == "WEBVTT" or "-->" in r or r.isdigit() or r.startswith(("NOTE", "STYLE")):
            continue
        lines.append(re.sub(r"<[^>]+>", "", r))
    return "\n".join(lines)


def audio_to_text(path: Path) -> str:
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise RuntimeError("faster-whisper is not installed") from error
    model = WhisperModel("small", device="cpu", compute_type="int8", local_files_only=True)
    segments, _ = model.transcribe(str(path), vad_filter=True)
    text = " ".join(s.text.strip() for s in segments).strip()
    if not text:
        raise RuntimeError("transcription is empty")
    return text
