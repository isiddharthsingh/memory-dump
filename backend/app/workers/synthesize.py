"""Wiki synthesis worker — real implementation lands in Phase 5.

Stub keeps the arq function registration valid so the ingest worker can
enqueue without crashing.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


async def synthesize_topics(ctx: dict[str, Any], item_id: str) -> str:
    log.info("synthesize_topics(%s): stub (Phase 5)", item_id)
    return "stub"
