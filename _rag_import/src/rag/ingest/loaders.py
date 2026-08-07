"""Multi-format loader: markdown, plain text, HTML, PDF -> normalized Document."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup
from pypdf import PdfReader

SUPPORTED_SUFFIXES = {".md", ".txt", ".html", ".htm", ".pdf"}


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
    else:
        raise ValueError(f"Unsupported file type: {path}")

    text = text.strip()
    return Document(
        doc_id=_doc_id_for(path),
        source_path=str(path),
        format=fmt,
        title=_title_from_text(path, text),
        text=text,
        metadata={"filename": path.name},
    )


def load_corpus(data_dir: Path) -> list[Document]:
    paths = sorted(
        p for p in Path(data_dir).iterdir() if p.suffix.lower() in SUPPORTED_SUFFIXES
    )
    return [load_document(p) for p in paths]
