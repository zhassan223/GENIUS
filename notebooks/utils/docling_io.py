from __future__ import annotations

from pathlib import Path
from typing import Optional


def pdf_to_markdown(
    pdf_path: str | Path,
    *,
    save_markdown_path: Optional[str | Path] = None,
) -> str:
    """
    Convert a PDF to markdown using Docling and return the markdown text.

    Files are a side effect only: if `save_markdown_path` is provided, the markdown
    will be written there.
    """

    from docling.document_converter import DocumentConverter

    pdf_path = Path(pdf_path)
    converter = DocumentConverter()
    document = converter.convert(str(pdf_path))
    markdown = document.document.export_to_markdown()

    if save_markdown_path is not None:
        save_markdown_path = Path(save_markdown_path)
        save_markdown_path.write_text(markdown, encoding="utf-8")

    return markdown

