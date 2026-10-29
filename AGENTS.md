# AGENTS.md

Guidance for AI coding agents (Claude Code, Codex and others) working in this repository. `CLAUDE.md` imports this file, so edit it here.

## Repo shape

Two deployables in one repo:

- `backend/` — FastAPI + Postgres (pgvector) + Redis + arq workers. Python 3.12. Run via `docker compose` from `backend/`. `.env` lives at the repo root and is loaded by both `api` and `worker` services.
- `apps/ios/` — native SwiftUI app (iOS 26+) plus a Share Extension. Talks to the backend over HTTPS; the server address and session live in the shared Keychain, set from the sign-in screen and Settings.

`docs/architecture.md` is the canonical condensed design doc; the README has the smoke-test curl recipes and the API table.

## Backend — commands

All commands assume `cd backend` unless noted. The compose file mounts `.` into the containers, so code changes hot-reload.

```bash
docker compose up --build              # full stack (postgres + redis + supertokens + api + worker)
docker compose up postgres redis supertokens   # infra only, then run api/worker on host
                                       # with SUPERTOKENS_CONNECTION_URI=http://localhost:3567

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

## iOS — commands

```bash
cd apps/ios
xcodegen                                                # project.yml → MemoryDump.xcodeproj (gitignored)
open MemoryDump.xcodeproj                               # set your team under Signing, run on a device

# simulator build and tests (pass -parallel-testing-enabled NO, or xcodebuild clones simulators and stalls)
xcodebuild -project MemoryDump.xcodeproj -scheme MemoryDump -destination 'platform=iOS Simulator,name=iPhone 17 Pro' build
xcodebuild test -project MemoryDump.xcodeproj -scheme MemoryDump -destination 'platform=iOS Simulator,name=iPhone 17 Pro' -parallel-testing-enabled NO
```

`project.yml` is the source of truth for targets, entitlements and Info.plist keys; edit it and re-run `xcodegen` instead of changing the generated project in Xcode. `DEVELOPMENT_TEAM` is the free Personal Team (`L53NYQQX76`) until a paid account exists: device installs expire after 7 days, and the app's `aps-environment` entitlement is left out because Personal Teams can't sign push. With a paid team, change the ID and add `aps-environment: development` back under the app target's entitlements.

Three targets: `MemoryDump` (app), `ShareExtension`, `MemoryDumpTests` (Swift Testing). `Shared/` is compiled into both the app and the extension: the API client, models, Keychain session store and the offline share queue. The two processes share state through App Group `group.com.siddharthsingh.memorydump` (queue files, lock file) and a Keychain access group (session, server URL).

The simulator reaches the local backend at `http://localhost:8000`.

## Architecture — the parts that span multiple files

### Ingest pipeline (per-item, async)

`POST /ingest` (`app/api/ingest.py`) inserts an `Item` row with `status="pending"` and enqueues `ingest_item` on arq. `(user_id, url)` is unique, so sharing a saved URL again returns the saved item, re-queued if it had failed; uploads get a `file://<item id>/<filename>` placeholder URL so files with the same name stay separate. The worker in `app/workers/ingest.py` runs the full pipeline:

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
5. `/ask` (`app/ai/ask.py`) feeds the reranked context to Claude (`CLAUDE_ASK_MODEL`, Opus) which must cite as `[item:UUID]` / `[wiki:slug]`. `/search` returns the hits directly, minus chunks found only by the dense search whose rerank score is under `_MIN_SCORE` (`app/api/search.py`): nearest neighbours exist even for a query that matches nothing.

If you change the chunking or embedding model, both `items` and `topic_pages` need re-embedding, and `EMBED_DIM` in `app/db/models.py` plus the migration's `Vector(N)` must match.

### Ask history (`/chats`)

`app/api/chats.py`: each `chats` row holds one conversation's turns as JSONB, under an id the app picks. The app PUTs the whole chat when a question is asked and again when the answer finishes; `GET /chats` returns only each chat's questions for History, and `GET /chats/{id}` the full turns. Turn JSON uses single-word keys (`status`, `error: {title, detail}`) because the app encodes request bodies with a plain `JSONEncoder`, without snake_case conversion. On iOS, `AskModel` sends every chat write through one serial queue, so an older copy never lands after a newer one or after a delete.

### Offline copy (iOS)

The app stays readable with no server. `APIClient` saves every GET body to disk per account (`Shared/SavedResponses.swift`, Application Support/Offline/<user id>/, keyed by path and sorted query), and when a request can't reach the server (any `URLError` but cancel, or 502/503/504) a GET returns that saved copy and sets `isOffline`, which the Library and Wiki subtitles show. Writes never fall back. `APIClient.saved` reads only the saved copies, so screens show them at once and then refresh from the server. `MemoryDump/App/OfflineSync.swift` runs when the app becomes active: it walks `/items` in the Library's page size (so offline scrolling finds the same pages), then fetches each item, wiki page and chat whose status, version or `updated_at` changed since its last walk. Thumbnails go through `CachedImage`, a disk `URLCache` that always prefers its copy. Signing out, deleting the account, or a rejected session clears both. Search and Ask still need the server.

### Auth

SuperTokens (self-hosted core, `supertokens-python` SDK) owns accounts, passwords and sessions. The core is the `supertokens` compose service, keeping its tables in the `supertokens` schema of the same database; `app/auth/supertokens.py` initialises the SDK with the Session, EmailPassword and Dashboard recipes, and `app.main` adds its middleware. SuperTokens serves its own routes under `/auth` (`signup`, `signin`, `signout`, `session/refresh`, and the user dashboard at `/auth/dashboard`); `GET`/`DELETE /auth/me` in `app/api/auth.py` sit beside them. The app uses header-based sessions: it sends `st-auth-mode: header`, reads the `st-access-token` and `st-refresh-token` response headers, and sends `Authorization: Bearer`. Access tokens last 15 minutes and refresh tokens expire after 30 days unused (`ACCESS_TOKEN_VALIDITY` / `REFRESH_TOKEN_VALIDITY` on the core service), and the core hashes passwords with Argon2id (`PASSWORD_HASHING_ALG`). The core's `API_KEYS` must match `SUPERTOKENS_API_KEY`. Passwords must be 10 to 100 characters (`_validate_password`); with `ALLOW_SIGNUP=false` sign-up answers a `GENERAL_ERROR` the app shows as is.

The app's `users` table holds only `id` (the SuperTokens user ID), `display_name` and `created_at`; the email and password live in SuperTokens. The EmailPassword `sign_up` override creates the `users` row for every new login, including ones made from the dashboard, and the optional `name` sign-up field becomes `display_name`. Migration `0006_supertokens` imported the pre-SuperTokens accounts into the core with their Argon2 hashes and their old ids as external user IDs, so it needs the core running.

Owner-scoped routes depend on `app/deps.py:get_current_user`, which runs SuperTokens' `verify_session()` and loads the `users` row, and filter by `user_id`, so no user sees another's items, wiki, chats or devices (`tests/test_ownership.py`, `tests/test_chats.py`). `DELETE /auth/me` deletes the row, relying on `ON DELETE CASCADE` foreign keys to remove everything the user owns, then deletes the SuperTokens user and its sessions. Deleting a user from the dashboard removes only the login; the library stays until the row is deleted. `/ingest` and `/ask` are rate-limited per user per hour in Redis (`app/auth/rate_limit.py`).

SuperTokens rotates refresh tokens, and a refresh token used again after its replacement has been used ends the session (theft detection). On iOS the session is stored in the shared Keychain (`apps/ios/Shared/SessionStore.swift`) and attached by `apps/ios/Shared/APIClient.swift`, which refreshes once after a 401; `AuthAPI` there speaks SuperTokens' header-based protocol directly. The official SuperTokens iOS SDK isn't used: its URL protocol buffers whole responses and drops upload bodies, which would break `/ask/stream` and file uploads. The app and the share extension can both refresh, so `SessionStore.refresh` holds a `flock()` on a file in the App Group and re-reads the Keychain after taking it. Keep that lock if you touch refresh.

### Push

`app/notifications.py` sends straight to APNs over HTTP/2 with a token-based `.p8` key (`APNS_KEY_PATH`, `APNS_KEY_ID`, `APNS_TEAM_ID`, `APNS_TOPIC` in `.env`; the worker mounts `secrets/` at `/run/secrets`). With no key path, pushes are skipped. The iOS app can't receive them until it is signed by a paid team with the `aps-environment` entitlement (see iOS — commands). Each `devices` row stores the hex token and `apns_env` (`sandbox` for Xcode builds, `production` for TestFlight/App Store), which picks the APNs host. Tokens APNs reports as gone (410 / BadDeviceToken) are deleted.

### DB & migrations

SQLAlchemy 2.x async (`asyncpg`) for app code; Alembic uses the sync `psycopg` URL (see `Settings.sync_database_url`). The schema lives in `app/db/models.py` and the initial migration `backend/alembic/versions/0001_initial.py` — that file also creates the pgvector HNSW indexes, the GIN FTS indexes, and the immutable `tsvector` generated columns. Don't autogenerate over the FTS bits blindly; they were hand-tuned (see commit `7d70d17` for the immutability fix).

### Models / providers — where they're set

All Claude/Voyage model IDs are env-overridable in `app/config.py` (`CLAUDE_ANALYSIS_MODEL`, `CLAUDE_SYNTHESIS_MODEL`, `CLAUDE_ASK_MODEL`, `VOYAGE_EMBED_MODEL`, `VOYAGE_RERANK_MODEL`). Defaults are in `.env.example`. Don't hardcode model IDs at call sites — pull them from `get_settings()`.

## Conventions specific to this repo

- Backend lint: `ruff` with `select = ["E", "F", "I", "B", "UP", "SIM", "ASYNC"]`, line length 100, `E501` ignored. Target `py312` syntax (use `X | Y` unions, `list[...]`, `from __future__ import annotations` where helpful).
- iOS response types are `Codable` structs in `apps/ios/Shared/Models.swift`, decoded with `convertFromSnakeCase` — when adding/changing a backend response shape, update the struct or decoding will fail in the app.
- iOS navigation: three tabs (Library, Wiki, Ask), each a `NavigationStack` whose path lives in `Router`; `Route.item` / `Route.wiki` are the detail screens. In-app links use `memorydump://wiki/<slug>` and `memorydump://cite/item:<id>`; `LinkRewriter` turns `[[slug]]` and `[item:<id>]` tokens into them and `Router.open` handles them.
- iOS colors come from `Palette` and `Swatch` in `Design/Theme.swift`, each with a light and a dark value; don't hardcode `.white` or `.black` for anything that sits on the page. Text on an ink, accent or failed fill uses `Palette.onInk`, titles on a topic pastel use `Palette.onPastel`, and shadows use `Palette.shadow(_:)`. The Settings › Appearance choice (`Appearance`, in UserDefaults) sets the window's interface style, so sheets and alerts follow it.
- Swift 6 with main-actor default isolation. Closures handed to system APIs that call back on other queues (NSItemProvider, AVAudioEngine, Speech) must be `@Sendable` or built in a `nonisolated` function, or they trap at runtime.
- Logs are structlog JSON (`app/logging_setup.py`) — don't add `print()` in worker or API code.
