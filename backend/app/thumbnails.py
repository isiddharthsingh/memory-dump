"""Thumbnails of uploaded images and PDFs, for the app's cards and reader.

A JPEG at most MAX_PX on its long side: the image scaled down, or the top of
a PDF's first page, where its title is (the app fills and crops thumbnails to
wide bands, which would show a page's blank middle). It is stored beside the
upload under `key(item_id)` and served at `path(item_id)`
(`GET /items/{id}/thumbnail`), which is what the item's `thumbnail_url` holds.

Anyone who can sign up picks the files drawn here, so `draw` draws each in a
child process (`python -m app.thumbnails`) that may use at most MEMORY_CAP and
is killed after TIMEOUT_S: an image built to decode into gigabytes, or a PDF
built to render forever, costs that process and not the worker. It gets none
of the worker's environment (its keys and passwords), so a file that exploits a
decoder bug finds nothing to steal. Images over MAX_PIXELS aren't decoded at
all.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import resource
import sys
import uuid

import pypdfium2 as pdfium  # type: ignore[import-untyped]
from PIL import Image, ImageOps

log = logging.getLogger(__name__)

MAX_PX = 1200
# 8000 × 6000; Claude, which reads every image first, takes up to 8000 a side.
MAX_PIXELS = 50_000_000
TIMEOUT_S = 30
# Address space for the child: an image at MAX_PIXELS decodes into ~200 MB.
MEMORY_CAP = 1 << 30
# All the child's environment: enough for Python to find this module.
_CHILD_ENV = ("PATH", "PYTHONPATH", "LANG", "LC_ALL")


def key(item_id: uuid.UUID) -> str:
    return f"{item_id}.thumb.jpg"


def path(item_id: uuid.UUID) -> str:
    return f"/items/{item_id}/thumbnail"


async def draw(data: bytes, mime: str) -> bytes | None:
    """`make` in a child process with a time and memory limit (see above)."""
    try:
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "app.thumbnails",
            mime,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=child_env(),
        )
    except OSError as exc:
        # Out of processes or file descriptors: the upload is still saved.
        log.warning("no thumbnail for a %s: couldn't start drawing it: %r", mime, exc)
        return None
    try:
        async with asyncio.timeout(TIMEOUT_S):
            jpeg, errors = await child.communicate(data)
    except TimeoutError:
        log.warning("no thumbnail for a %s: drawing it took over %ss", mime, TIMEOUT_S)
        return None
    finally:
        # Also when the job is cancelled: never leave one drawing.
        if child.returncode is None:
            child.kill()
            await child.wait()
    if child.returncode != 0 or not jpeg:
        reason = errors.decode(errors="replace").strip()[-500:]
        log.warning("%s", reason or f"no thumbnail for a {mime}: exit {child.returncode}")
        return None
    return jpeg


def child_env() -> dict[str, str]:
    return {name: os.environ[name] for name in _CHILD_ENV if name in os.environ}


def make(data: bytes, mime: str) -> bytes | None:
    """The thumbnail's JPEG bytes, or None when the file can't be drawn (a
    format Pillow doesn't read, a broken or encrypted PDF, an image over
    MAX_PIXELS): a thumbnail is never worth failing the upload over. Runs in
    the child process; call `draw`."""
    try:
        pdf = mime == "application/pdf"
        image = _first_page(data) if pdf else _image(data)
        image.thumbnail((MAX_PX, MAX_PX))
        if not pdf:
            # Photos are often stored sideways with an EXIF note to turn them;
            # turned once small, so the full-size image is never copied.
            image = ImageOps.exif_transpose(image)
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
    # Opening reads only the header: refuse a decompression bomb before decoding.
    if image.width * image.height > MAX_PIXELS:
        raise ValueError(f"{image.width}×{image.height} is over {MAX_PIXELS} pixels")
    # A JPEG decodes at a fraction of its size when that's all we need.
    image.draft("RGB", (MAX_PX, MAX_PX))
    return image


def _first_page(data: bytes) -> Image.Image:
    # PDFium isn't thread-safe; each child draws one file.
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


def _main() -> None:
    """The child: the file on stdin, its mime type as the argument, the JPEG on
    stdout, or exit 1 with the reason on stderr."""
    # Never above a hard limit the worker already runs under.
    _, hard = resource.getrlimit(resource.RLIMIT_AS)
    cap = MEMORY_CAP if hard == resource.RLIM_INFINITY else min(MEMORY_CAP, hard)
    resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    jpeg = make(sys.stdin.buffer.read(), sys.argv[1])
    if jpeg is None:
        sys.exit(1)
    sys.stdout.buffer.write(jpeg)


if __name__ == "__main__":
    _main()
