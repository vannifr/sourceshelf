import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from sourceshelf.fiche import load_fiches, write_fiche
from sourceshelf.paths import data_path

DEFAULT_MODEL = "anthropic/claude-haiku-5.5"
DEFAULT_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_LANG = "English"
PROMPT_VERSION = "s21-b"
MAX_CHARS = 400_000
SYSTEM_PROMPT_TEMPLATE = (
    "Summarize the source in 3 to 5 sentences in {lang}. Strict rules: include only claims that "
    "appear literally in the text; copy numbers exactly with their unit and context, or leave them out; "
    "never give a conclusion or direction the authors do not draw themselves; add nothing from your own knowledge; "
    "when in doubt, leave the claim out. Only the summary, no headings."
)


def model_name() -> str:
    return os.environ.get("KB_MODEL") or DEFAULT_MODEL


def system_prompt() -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(lang=os.environ.get("KB_SUMMARY_LANG") or DEFAULT_LANG)


LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def endpoint() -> str:
    return os.environ.get("KB_ENDPOINT") or DEFAULT_ENDPOINT


def check_endpoint(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return
    if parts.scheme == "http" and parts.hostname in LOCAL_HOSTS:
        return
    raise ModelError("KB_ENDPOINT must be https (http only for localhost)")


PREAMBLE = re.compile(r"^(hier is|hieronder|dit is|here is|here's|below is|this is)\b.*:\s*$", re.IGNORECASE)
CLOSING = re.compile(
    r"\b(laat (het |me |ons )?(me |ons )?weten|hoop dat|wil je|wilt u|kan ik|zal ik|let me know|i hope|would you like|shall i)\b", re.IGNORECASE
)


class ModelError(Exception):
    pass


@dataclass
class Result:
    id: str
    done: bool
    error: str | None = None
    reason: str = ""


def filter_noise(text: str) -> str:
    paragraphs = [a.strip() for a in re.split(r"\n\s*\n", text.strip())]
    paragraphs = [
        a for a in paragraphs if a and not re.fullmatch(r"(#+\s.*|\*\*[^\n]{0,60}\*\*|samenvatting:?)", a, re.IGNORECASE)
    ]
    if paragraphs and PREAMBLE.match(paragraphs[0]):
        paragraphs = paragraphs[1:]
    if len(paragraphs) > 1 and CLOSING.search(paragraphs[-1]):
        paragraphs = paragraphs[:-1]
    lines = [r for a in paragraphs for r in a.splitlines() if not r.lstrip().startswith("#")]
    return " ".join(r.strip() for r in lines).strip()


def openrouter_model(model: str | None = None):
    model = model or model_name()
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise ModelError("OPENROUTER_API_KEY is missing")

    def call(system: str, text: str) -> str:
        data = json.dumps(
            {
                "model": model,
                "temperature": 0,
                "max_tokens": 4000,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}],
            }
        ).encode()
        url = endpoint()
        check_endpoint(url)
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                choice = json.load(r)["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ModelError("answer truncated (max_tokens reached)")
            return (choice["message"]["content"] or "").strip()
        except urllib.error.HTTPError as e:
            raise ModelError(f"HTTP {e.code}") from e
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as e:
            raise ModelError(f"{type(e).__name__}: {e}") from e

    return call


def _error(fiche, meta: dict, message: str) -> Result:
    meta["error"] = f"summary: {message}"
    write_fiche(fiche.path, meta, fiche.text)
    return Result(str(meta["id"]), False, message)


def describe(
    root: Path,
    fid: str,
    model,
    date: str,
    name: str | None = None,
    again: bool = False,
    max_chars: int = MAX_CHARS,
    dry_run: bool = False,
) -> Result:
    name = name or model_name()
    fiche = next((f for f in load_fiches(root / "fiches") if f.meta.get("id") == fid), None)
    if fiche is None:
        return Result(fid, False, f"unknown id: {fid}")
    meta = dict(fiche.meta)
    if str(meta.get("confidential", "")).strip().lower() == "yes":
        return Result(fid, False, reason="confidential: not sent to an external model")
    if meta.get("machine-summary") and not again:
        return Result(fid, False, reason="already has a summary")
    path = data_path(root, meta.get("text"))
    if dry_run:
        return Result(fid, False, reason="dry run: would describe")
    if path is None or not path.is_file():
        if meta.get("no-text"):
            if str(meta.get("error", "")).startswith(("summary:",)):
                meta.pop("error")
                write_fiche(fiche.path, meta, fiche.text)
            return Result(fid, False, reason=f"no text: {meta['no-text']}")
        return _error(fiche, meta, "no text file to summarize")
    text = path.read_text(encoding="utf-8", errors="ignore")
    if not text.strip():
        return _error(fiche, meta, "text file is empty")
    truncated = len(text) > max_chars
    try:
        answer = model(system_prompt(), text[:max_chars])
    except ModelError as e:
        return _error(fiche, meta, str(e))
    summary = filter_noise(answer)
    if not summary:
        return _error(fiche, meta, "model returned no summary")
    if summary[-1] not in ".!?)":
        return _error(fiche, meta, "summary looks truncated (no sentence end)")
    meta.pop("error", None)
    block = {"text": summary, "model": name, "prompt": PROMPT_VERSION, "date": date}
    if truncated:
        block["truncated"] = "yes"
    meta["machine-summary"] = block
    write_fiche(fiche.path, meta, fiche.text)
    return Result(fid, True)
