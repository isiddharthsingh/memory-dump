from __future__ import annotations

import base64
import hashlib
import logging
from array import array
from typing import Literal

from redis.exceptions import RedisError

from app import kv
from app.ai.clients import voyage_embed, voyage_rerank
from app.config import get_settings

log = logging.getLogger(__name__)

InputType = Literal["document", "query"]


def chunk_text(text: str, max_chars: int = 2_000, overlap: int = 200) -> list[str]:
    """Naive but predictable chunker: split on paragraphs first, then pack.

    Voyage 3-large handles up to 32k tokens, but smaller chunks (~500 tok)
    consistently win on retrieval quality. ~2000 chars ≈ 500 tokens.
    """
    if not text:
        return []
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        if len(buf) + len(para) + 2 <= max_chars:
            buf = f"{buf}\n\n{para}".strip()
            continue
        if buf:
            chunks.append(buf)
        if len(para) <= max_chars:
            buf = para
        else:
            chunks.extend(_split_long(para, max_chars, overlap))
            buf = ""
    if buf:
        chunks.append(buf)
    return chunks


def _split_long(para: str, max_chars: int, overlap: int) -> list[str]:
    """A paragraph too big for one chunk: every flattened section is one (a
    transcript, a linked PDF's whole text). Packed by its lines, and a line
    longer than a chunk is cut between words, each piece starting about
    `overlap` characters back."""
    pieces: list[str] = []
    for line in para.split("\n"):
        while len(line) > max_chars:
            cut = line.rfind(" ", max_chars // 2, max_chars)
            if cut <= 0:
                cut = max_chars
            pieces.append(line[:cut].rstrip())
            back = line.find(" ", max(cut - overlap, 1), cut)
            line = line[back + 1 if back != -1 else cut :].lstrip()
        if line:
            pieces.append(line)
    chunks: list[str] = []
    buf = ""
    for piece in pieces:
        if buf and len(buf) + len(piece) + 1 > max_chars:
            chunks.append(buf)
            buf = ""
        buf = f"{buf}\n{piece}" if buf else piece
    if buf:
        chunks.append(buf)
    return chunks


async def embed_texts(
    texts: list[str],
    input_type: InputType = "document",
) -> list[list[float]]:
    """Return one embedding per input text. Order preserved."""
    if not texts:
        return []
    return await voyage_embed(texts, input_type=input_type)


async def embed_chunks(chunks: list[str], context: str | None) -> list[list[float]]:
    """Embeddings of one document's chunks, in order. Each chunk after the
    first is embedded with `context` (what the document is: its title and a
    line of summary) in front, so a passage deep in a long transcript still
    says what it belongs to. The chunk's stored text stays the chunk alone."""
    texts = [c if i == 0 or not context else f"{context}\n\n{c}" for i, c in enumerate(chunks)]
    return await embed_texts(texts, input_type="document")


# A question asked again (or typed again in Search) reuses its embedding.
QUERY_CACHE_TTL_S = 24 * 3600


async def embed_query(query: str) -> list[float]:
    """The query's embedding, from the cache when it was embedded lately."""
    key = f"qemb:{get_settings().voyage_embed_model}:{hashlib.sha256(query.encode()).hexdigest()}"
    try:
        cached = await kv.redis().get(key)
    except RedisError:
        cached = None
    if cached:
        return list(array("f", base64.b64decode(cached)))
    vector = (await embed_texts([query], input_type="query"))[0]
    try:
        packed = base64.b64encode(array("f", vector).tobytes()).decode()
        await kv.redis().set(key, packed, ex=QUERY_CACHE_TTL_S)
    except RedisError:
        log.warning("couldn't cache a query embedding", exc_info=True)
    return vector


async def rerank(
    query: str,
    documents: list[str],
    top_k: int | None = None,
) -> list[tuple[int, float]]:
    """Return [(original_index, relevance_score), ...] sorted desc."""
    if not documents:
        return []
    return await voyage_rerank(query, documents, top_k or len(documents))
