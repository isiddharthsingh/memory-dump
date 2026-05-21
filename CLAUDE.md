# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repo shape

Two deployables in one repo:

- `backend/` — FastAPI + Postgres (pgvector) + Redis + arq workers. Python 3.12. Run via `docker compose` from `backend/`. `.env` lives at the repo root and is loaded by both `api` and `worker` services.
- `apps/mobile/` — Expo (iOS + Android) with `expo-router` file-based routing and an `expo-share-intent` share extension. Talks to the backend over HTTPS with a bearer token configured in the in-app Settings tab.

`docs/architecture.md` is the canonical condensed design doc; the README has the smoke-test curl recipes and the API table.

## Backend — commands

All commands assume `cd backend` unless noted. The compose file mounts `.` into the containers, so code changes hot-reload.

```bash
docker compose up --build              # full stack (postgres + redis + api + worker)
docker compose up postgres redis       # infra only, then run api/worker on host

docker compose exec api alembic upgrade head            # migrations (also runs at api startup)
docker compose exec api alembic revision -m "msg" --autogenerate

docker compose exec api pytest                          # all tests
docker compose exec api pytest tests/test_ask.py        # single file
docker compose exec api pytest tests/test_ask.py::test_name -x

docker compose exec api ruff check .                    # lint
docker compose exec api ruff format .
docker compose exec api mypy app                        # type-check
```

Worker entry point: `arq app.workers.worker.WorkerSettings` (the `worker` compose service runs this). Functions registered there: `ingest_item`, `synthesize_topics`, `compact_topics`. A cron job runs `compact_topics` nightly at 03:14 UTC.

`pytest-asyncio` is in `asyncio_mode = "auto"` — don't decorate async tests with `@pytest.mark.asyncio`.

## Mobile — commands

```bash
cd apps/mobile
npm install
npx expo prebuild                                       # generates ios/ and android/
npx eas build --profile development --platform ios      # or android — needed for share extension
npx expo start --dev-client                             # then open the dev client on device

npm run typecheck                                       # tsc --noEmit
npm run lint                                            # eslint app components lib
```

**Expo Go cannot host the iOS Share Extension or Android send-target** — a dev build (EAS or local `expo run:*`) is required to test sharing. The dev server alone is not enough.

Server URL + bearer token are configured at runtime in the app's Settings tab and persisted via `expo-secure-store` (`lib/settings.ts`), not via build-time env vars.

## Architecture — the parts that span multiple files

### Ingest pipeline (per-item, async)

`POST /ingest` (`app/api/ingest.py`) inserts an `Item` row with `status="pending"` and enqueues `ingest_item` on arq. The worker in `app/workers/ingest.py` runs the full pipeline:

1. `app/extractors/` — per-source extractor selected by `get_extractor(source)`. `source` is detected from the URL (`youtube` / `twitter` / `instagram` / `webpage`).
2. `app/ai/analyze.py` — Claude (`CLAUDE_ANALYSIS_MODEL`, Haiku by default) returns title, summary, tags, and the **lexical anchors**: `people`, `handles`, `brands_products`, `topics`. These anchors are persisted as Postgres `ARRAY(Text)` columns AND folded into the text that gets embedded — they're what makes proper-noun queries work.
3. `app/ai/embed.py` — `chunk_text` + Voyage `voyage-3-large` (1024-dim) into `item_chunks.embedding`. Re-ingest is idempotent: existing chunks for the item are deleted before insert.
4. Status transitions: `pending → processing → ready` (or `failed` with `error` text). `POST /items/{id}/retry` re-enqueues.
5. On success, the worker enqueues `synthesize_topics` for that item via the shared arq Redis (`ctx["redis"].enqueue_job(...)`).

If you add a new source type, the work is: new extractor in `app/extractors/`, register it in `get_extractor`, and make sure the URL→source detector in the ingest API recognizes it.

### Wiki synthesis (Karpathy LLM-wiki pattern)

`app/workers/synthesize.py` + `app/ai/synthesize.py`. After each successful ingest, Claude (`CLAUDE_SYNTHESIS_MODEL`, Sonnet) **patches** existing `topic_pages` rather than regenerating them. Pages are markdown with `[[wikilinks]]`; `backlinks` and `source_item_ids` are tracked on the row, `version` increments per patch. `topic_page_chunks` mirrors `item_chunks` so wiki content participates in the same retrieval fusion.

`compact_topics` (nightly) is the GC pass that merges/prunes drifted pages.

### Hybrid retrieval (`/search` and `/ask`)

`app/ai/retrieval.py` is the single source of truth and is used by both endpoints. The pipeline:

1. **Dense**: pgvector HNSW over `item_chunks.embedding` ∪ `topic_page_chunks.embedding`, query embedded with Voyage `input_type="query"`.
2. **BM25**: Postgres `tsvector` full-text over the same two tables. The GIN index on the lexical-anchor arrays is what gives proper-noun queries deterministic recall.
3. **Reciprocal Rank Fusion** merges the two ranked lists.
4. **Voyage `rerank-2.5`** reorders the top candidates.
5. `/ask` (`app/ai/ask.py`) feeds the reranked context to Claude (`CLAUDE_ASK_MODEL`, Opus) which must cite as `[item:UUID]` / `[wiki:slug]`. `/search` returns the hits directly.

If you change the chunking or embedding model, both `items` and `topic_pages` need re-embedding, and `EMBED_DIM` in `app/db/models.py` plus the migration's `Vector(N)` must match.

### Auth

Single-user bearer token. `app/deps.py:require_token` is attached to every router except `/healthz` and uses `secrets.compare_digest`. The token comes from `MEMORY_DUMP_API_TOKEN` in `.env`. On mobile it's stored in `expo-secure-store` and attached by `apps/mobile/lib/api.ts`.

### DB & migrations

SQLAlchemy 2.x async (`asyncpg`) for app code; Alembic uses the sync `psycopg` URL (see `Settings.sync_database_url`). The schema lives in `app/db/models.py` and the initial migration `backend/alembic/versions/0001_initial.py` — that file also creates the pgvector HNSW indexes, the GIN FTS indexes, and the immutable `tsvector` generated columns. Don't autogenerate over the FTS bits blindly; they were hand-tuned (see commit `7d70d17` for the immutability fix).

### Models / providers — where they're set

All Claude/Voyage model IDs are env-overridable in `app/config.py` (`CLAUDE_ANALYSIS_MODEL`, `CLAUDE_SYNTHESIS_MODEL`, `CLAUDE_ASK_MODEL`, `VOYAGE_EMBED_MODEL`, `VOYAGE_RERANK_MODEL`). Defaults are in `.env.example`. Don't hardcode model IDs at call sites — pull them from `get_settings()`.

## Conventions specific to this repo

- Backend lint: `ruff` with `select = ["E", "F", "I", "B", "UP", "SIM", "ASYNC"]`, line length 100, `E501` ignored. Target `py312` syntax (use `X | Y` unions, `list[...]`, `from __future__ import annotations` where helpful).
- Mobile API client is **zod-validated** (`apps/mobile/lib/api.ts`) — when adding/changing a backend response shape, update the zod schema or runtime parsing will fail in the app.
- `expo-router` file-based routing: `app/(tabs)/*` are the tab screens, `app/item/[id].tsx` and `app/wiki/[slug].tsx` are dynamic routes. The share-intent handler is wired up once in `app/_layout.tsx` via `lib/useIncomingShare.ts`.
- Logs are structlog JSON (`app/logging_setup.py`) — don't add `print()` in worker or API code.
