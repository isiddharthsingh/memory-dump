"""Thumbnails of uploaded images and PDFs, for the app's cards and reader.

A JPEG at most MAX_PX on its long side: the image scaled down, or the top of
a PDF's first page, where its title is (the app fills and crops thumbnails to
wide bands, which would show a page's blank middle). It is stored beside the
upload under `key(item_id)` and
served at `path(item_id)` (`GET /items/{id}/thumbnail`), which is what the
item's `thumbnail_url` holds.
"""

from __future__ import annotations

import io
import logging
import threading
import uuid

import pypdfium2 as pdfium  # type: ignore[import-untyped]
from PIL import Image, ImageOps

log = logging.getLogger(__name__)

MAX_PX = 1200
# PDFium isn't thread-safe, and jobs render in threads.
_pdfium = threading.Lock()


def key(item_id: uuid.UUID) -> str:
    return f"{item_id}.thumb.jpg"


def path(item_id: uuid.UUID) -> str:
    return f"/items/{item_id}/thumbnail"


def make(data: bytes, mime: str) -> bytes | None:
    """The thumbnail's JPEG bytes, or None when the file can't be drawn (a
    format Pillow doesn't read, a broken or encrypted PDF): a thumbnail is
    never worth failing the upload over. Blocking: run it in a thread."""
    try:
        image = _first_page(data) if mime == "application/pdf" else _image(data)
        image.thumbnail((MAX_PX, MAX_PX))
        if image.mode in ("RGBA", "LA", "P"):
            # Transparent parts go white, not black.
            rgba = image.convert("RGBA")
            image = Image.new("RGB", rgba.size, "white")
            image.paste(rgba, mask=rgba.getchannel("A"))
        out = io.BytesIO()
        image.convert("RGB").save(out, "JPEG", quality=82, optimize=True)
        return out.getvalue()
    except Exception as exc:
        # Pillow's plugins and PDFium raise all sorts on a damaged file.
        log.warning("no thumbnail for a %s: %r", mime, exc)
        return None


def _image(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    # A JPEG decodes at a fraction of its size when that's all we need.
    image.draft("RGB", (MAX_PX, MAX_PX))
    # Photos are often stored sideways with an EXIF note to turn them.
    return ImageOps.exif_transpose(image)


def _first_page(data: bytes) -> Image.Image:
    with _pdfium:
        pdf = pdfium.PdfDocument(data)
        try:
            if len(pdf) == 0:
                raise ValueError("the PDF has no pages")
            page = pdf[0]
            scale = MAX_PX / max(page.get_size())
            image = page.render(scale=scale).to_pil()
        finally:
            pdf.close()
    # The top 4:3 of the page.
    width, height = image.size
    return image.crop((0, 0, width, min(height, width * 3 // 4)))
