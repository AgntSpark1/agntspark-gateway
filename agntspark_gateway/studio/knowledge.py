"""Owner-uploaded knowledge: split into passages, found by full-text search.

Postgres full-text search, no embeddings: an FAQ or a few pages of notes is
the common case, and a small knowledge base is simply sent whole.
"""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.studio import KnowledgeChunk, KnowledgeDocument

CHUNK_CHARS = 1200
# A knowledge base with at most this many passages goes into every prompt.
SEND_WHOLE_MAX_CHUNKS = 8
TOP_K = 5
_MAX_QUERY_TERMS = 24


def split_into_chunks(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Pack paragraphs into passages of at most ``size`` characters."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    for p in paragraphs:
        while len(p) > size:
            cut = p.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            pieces.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            pieces.append(p)

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + 2 + len(piece) > size:
            chunks.append(current)
            current = piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def _or_query(text: str) -> str | None:
    """Any-word tsquery from free text; only word characters reach to_tsquery."""
    terms = list(dict.fromkeys(t.lower() for t in re.findall(r"\w+", text) if len(t) > 1))
    if not terms:
        return None
    return " | ".join(terms[:_MAX_QUERY_TERMS])


async def relevant_passages(
    db: AsyncSession, *, assistant_id: str, question: str
) -> list[tuple[str, str]]:
    """``(document title, passage)`` pairs worth putting in the prompt."""
    base = (
        select(KnowledgeDocument.title, KnowledgeChunk.content)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
        .where(KnowledgeChunk.assistant_id == assistant_id)
    )
    total = (
        await db.execute(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.assistant_id == assistant_id)
        )
    ).scalar_one()
    if total == 0:
        return []
    if total <= SEND_WHOLE_MAX_CHUNKS:
        rows = await db.execute(
            base.order_by(KnowledgeDocument.created_at, KnowledgeChunk.position)
        )
        return [(r[0], r[1]) for r in rows]

    query = _or_query(question)
    if query is None:
        return []
    tsvector = func.to_tsvector("simple", KnowledgeChunk.content)
    tsquery = func.to_tsquery("simple", query)
    rows = await db.execute(
        base.where(tsvector.op("@@")(tsquery))
        .order_by(func.ts_rank(tsvector, tsquery).desc())
        .limit(TOP_K)
    )
    return [(r[0], r[1]) for r in rows]


__all__ = ["split_into_chunks", "relevant_passages", "CHUNK_CHARS"]
