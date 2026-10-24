"""Визуализация PDF-страниц в PNG через PDFium, не через PyMuPDF."""
from __future__ import annotations

from io import BytesIO
from os import PathLike

import pypdfium2 as pdfium


def render_pdf_page(source: str | PathLike[str] | bytes, page_number: int, dpi: int = 144) -> bytes:
    """Вернуть изображение одной PDF-страницы в PNG.

    Нумерация для пользователя начинается с 1. Страница создаётся в памяти,
    чтобы рабочие картинки OCR/предпросмотра не попадали в репозиторий.
    """
    if page_number < 1:
        raise ValueError("Номер страницы должен быть от 1.")
    if not 72 <= dpi <= 600:
        raise ValueError("Разрешение страницы должно быть от 72 до 600 DPI.")

    document = pdfium.PdfDocument(source)
    try:
        if page_number > len(document):
            raise ValueError(f"В документе только {len(document)} страниц.")
        page = document[page_number - 1]
        try:
            bitmap = page.render(scale=dpi / 72)
            try:
                image = bitmap.to_pil()
                output = BytesIO()
                image.save(output, format="PNG", optimize=True)
                return output.getvalue()
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        document.close()
