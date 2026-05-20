from __future__ import annotations

import asyncio
from typing import Literal

from app.config import get_settings

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
            # paragraph too big — hard split
            for i in range(0, len(para), max_chars - overlap):
                chunks.append(para[i : i + max_chars])
            buf = ""
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
    settings = get_settings()

    def _call() -> list[list[float]]:
        import voyageai

        client = voyageai.Client(api_key=settings.voyage_api_key)
        result = client.embed(
            texts=texts,
            model=settings.voyage_embed_model,
            input_type=input_type,
            truncation=True,
        )
        return list(result.embeddings)

    return await asyncio.to_thread(_call)


async def rerank(
    query: str,
    documents: list[str],
    top_k: int | None = None,
) -> list[tuple[int, float]]:
    """Return [(original_index, relevance_score), ...] sorted desc."""
    if not documents:
        return []
    settings = get_settings()

    def _call() -> list[tuple[int, float]]:
        import voyageai

        client = voyageai.Client(api_key=settings.voyage_api_key)
        result = client.rerank(
            query=query,
            documents=documents,
            model=settings.voyage_rerank_model,
            top_k=top_k or len(documents),
        )
        return [(r.index, r.relevance_score) for r in result.results]

    return await asyncio.to_thread(_call)
