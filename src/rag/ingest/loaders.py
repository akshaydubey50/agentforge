"""Multi-format loader: markdown, plain text, HTML, PDF, images -> normalized Document."""

from __future__ import annotations

import base64
import hashlib
import io
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup
from PIL import Image
from pypdf import PdfReader

from rag.config import settings
from rag.llm import describe_image

SUPPORTED_SUFFIXES = {".md", ".txt", ".html", ".htm", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"}

_IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

_IMAGE_DESCRIPTION_PROMPT = """Look at this image and produce ONE plain-text passage covering, \
in order:

1. TRANSCRIPTION: every piece of text visible in the image, verbatim, exactly as written \
   (labels, captions, numbers, headings, body text -- all of it). Write "(no visible text)" if \
   there is none.
2. DESCRIPTION: what the image actually shows -- if it's a chart or diagram, describe its \
   structure and any data it conveys; if it's a photo, describe the concrete subject and \
   composition. Factual and specific, not vague ("a bar chart comparing Q1 and Q2 revenue \
   showing Q2 higher", not "a chart with some bars").

Only describe what is actually visible -- never guess at or invent text or details you cannot \
actually make out."""


@dataclass
class Document:
    doc_id: str
    source_path: str
    format: str
    title: str
    text: str
    metadata: dict = field(default_factory=dict)


def _doc_id_for(path: Path) -> str:
    return hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:12]


def _title_from_text(path: Path, text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped
    return path.stem.replace("_", " ").title()


def _load_markdown_or_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_html(path: Path) -> str:
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    lines = []
    for el in soup.find_all(["h1", "h2", "h3", "h4", "p", "li"]):
        text = el.get_text(strip=True)
        if not text:
            continue
        if el.name in {"h1", "h2", "h3", "h4"}:
            prefix = "#" * int(el.name[1])
            lines.append(f"{prefix} {text}")
        else:
            lines.append(text)
    return "\n\n".join(lines)


def _load_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(pages)


def _resized_b64(path: Path) -> tuple[str, str]:
    """Loads the image, downscales if it exceeds image_max_dimension_px
    (keeping aspect ratio; a no-op for smaller images), and returns
    (base64, mime_type). Re-encodes as JPEG for anything not already a
    simple web format, since Pillow's PNG/GIF/WEBP support varies by mode
    (e.g. palette/CMYK) and JPEG output is universally safe to send."""
    with Image.open(path) as img:
        img = img.convert("RGB") if img.mode not in ("RGB", "L") else img
        longest = max(img.size)
        if longest > settings.image_max_dimension_px:
            scale = settings.image_max_dimension_px / longest
            img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
        return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"


def _vision_cache_path(path: Path) -> Path:
    content_hash = hashlib.sha1(path.read_bytes()).hexdigest()
    return settings.vision_cache_dir / f"{content_hash}.txt"


def _load_image(path: Path) -> str:
    """Turns an image into text via describe_image(), cached on disk by
    content hash. The cache is what keeps this affordable: without it, the
    same image gets billed to the vision API once on /v1/documents/upload
    (to get a title) and again on every /v1/ingest (full corpus reload) --
    this makes every call after the first a free disk read."""
    cache_path = _vision_cache_path(path)
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8")

    image_b64, mime_type = _resized_b64(path)
    text = describe_image(image_b64, mime_type, prompt=_IMAGE_DESCRIPTION_PROMPT).strip()

    settings.vision_cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(text, encoding="utf-8")
    return text


def load_document(path: Path) -> Document:
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt"}:
        text = _load_markdown_or_text(path)
        fmt = "markdown" if suffix == ".md" else "text"
    elif suffix in {".html", ".htm"}:
        text = _load_html(path)
        fmt = "html"
    elif suffix == ".pdf":
        text = _load_pdf(path)
        fmt = "pdf"
    elif suffix in _IMAGE_MIME_TYPES:
        text = _load_image(path)
        fmt = "image"
    else:
        raise ValueError(f"Unsupported file type: {path}")

    text = text.strip()
    # An image's extracted text starts with "TRANSCRIPTION:"/"DESCRIPTION:" --
    # not a usable title -- so images use the filename directly instead of
    # _title_from_text's "first line of the text" heuristic.
    title = path.stem.replace("_", " ").title() if fmt == "image" else _title_from_text(path, text)
    return Document(
        doc_id=_doc_id_for(path),
        source_path=str(path),
        format=fmt,
        title=title,
        text=text,
        metadata={"filename": path.name},
    )


def load_corpus(data_dir: Path) -> list[Document]:
    paths = sorted(
        p for p in Path(data_dir).iterdir() if p.suffix.lower() in SUPPORTED_SUFFIXES
    )
    return [load_document(p) for p in paths]
