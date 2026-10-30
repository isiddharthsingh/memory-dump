# memory-dump

A personal "memory dump" mobile app. I share any link from my phone —
Instagram reels, YouTube videos, Twitter posts, blog articles — and an
AI extracts the content, analyzes it, and lets me find it later by
asking natural-language questions.

## What it does

1. I tap **Share → Memory Dump** on any URL, PDF or image on iOS.
2. The backend extracts the content (YouTube transcript, article body,
   tweet text, image caption + vision description for Instagram).
3. Claude summarizes it, tags it, and pulls out named entities
   (people, handles, brands, topics).
4. Voyage AI embeds the content into pgvector alongside Postgres
   full-text search for exact-name queries (hybrid retrieval).
5. A second AI pass folds the new item into an AI-maintained **wiki**
   of topic pages — the "second brain" layer inspired by Karpathy's
   LLM-wiki pattern. Pages get *patched*, not regenerated.
6. I open the app and ask things like *"that Karpathy video on async
   runtimes — what were the key points?"* and get an answer with
   citations back to the original sources.

## Repo layout

```
memory-dump/
├── apps/ios/                    # SwiftUI app + Share Extension (XcodeGen project.yml)
├── backend/                     # FastAPI + Postgres + Redis + arq workers
└── docs/architecture.md
```

## Backend — quickstart

```bash
cp .env.example .env             # fill in the AI keys and the S3 key pair
ln -s ../.env backend/.env       # compose reads SUPERTOKENS_API_KEY and S3_* from here
cd backend
docker compose up --build
```

This starts Postgres, Redis, the SuperTokens core (accounts and sessions),
SeaweedFS (uploaded files and their thumbnails), the api and the worker; a one-off `migrate` service runs the migrations before the
api and worker start. `docker compose up` merges `docker-compose.override.yml`
(published database ports, hot reload); in production run
`docker compose -f docker-compose.yml up -d` behind a TLS-terminating proxy.
Smoke test:

```bash
curl http://localhost:8000/readyz                                        # {"status":"ok"}

# sign up; the access token comes back in the st-access-token header
TOKEN=$(curl -s -D - -o /dev/null -X POST http://localhost:8000/auth/signup \
  -H "st-auth-mode: header" -H "Content-Type: application/json" \
  -d '{"formFields":[{"id":"email","value":"me@example.com"},{"id":"password","value":"a-long-pass-123"}]}' \
  | awk 'tolower($1)=="st-access-token:" {print $2}' | tr -d '\r')

curl -X POST http://localhost:8000/v1/ingest \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ"}'

# poll until ready
curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/v1/items | jq '.[0].status'

curl -X POST http://localhost:8000/v1/ask \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question":"what music video did I save?"}'
```

Access tokens last 15 minutes; `POST /auth/session/refresh` with
`Authorization: Bearer <st-refresh-token>` gets a new pair. To manage users,
open <http://localhost:8000/auth/dashboard>; it shows the command for creating
the first dashboard login.

Voyage's free tier caps you at 3 requests/min — add a payment method on
the Voyage dashboard if you plan to ingest more than a couple of items
back-to-back. The 200M free tokens still apply.

## iOS — quickstart

```bash
brew install xcodegen
cd apps/ios
xcodegen                         # generates MemoryDump.xcodeproj from project.yml
open MemoryDump.xcodeproj
```

The app talks to the server at `API_DOMAIN` in the repo's `.env`, read when
the app is built, so people who install it only enter an email and password.
Set it before you build: `https://<your domain>` for a deployed server, or
`http://<your-mac>.local:8000` for the backend on your Mac with the phone on
the same Wi-Fi (`scutil --get LocalHostName` prints the name). Without it the
build uses `http://localhost:8000`, which only the simulator reaches, and a
build for a phone stops with an error.

Connect your iPhone and press Run. The project signs with the free Personal
Team set in `project.yml`, so the install lasts 7 days; run it again from
Xcode to renew it. Allow Local Network access when asked if the server is on
your Mac. Share any link, PDF or image
to **Memory Dump**: the extension saves it without leaving the app you were
in, and keeps it for later if you're offline.

Push needs a paid Apple Developer account. Once you have one, set
`DEVELOPMENT_TEAM` in `project.yml` to the paid team, add
`aps-environment: development` back to the app's entitlements, and re-run
`xcodegen`. Then create an APNs key (.p8) under Certificates, IDs & Profiles
→ Keys, put it in `secrets/` at the repo root and fill the `APNS_*` values in
`.env`.

## API

The app's routes live under `/v1`; they are also served unversioned for app
builds from before it. SuperTokens' own routes and the health checks are not
versioned.

| Method | Path                  | Purpose                                 |
|--------|-----------------------|-----------------------------------------|
| GET    | `/livez`, `/healthz`  | liveness (no auth)                      |
| GET    | `/readyz`             | Postgres and Redis answer (no auth)     |
| GET    | `/metrics`            | Prometheus (`Bearer $METRICS_TOKEN`)    |
| POST   | `/auth/signup`        | create an account (SuperTokens, no auth) |
| POST   | `/auth/signin`        | sign in (SuperTokens, no auth)          |
| POST   | `/auth/session/refresh` | new tokens for the refresh token      |
| POST   | `/auth/signout`       | end this session                        |
| GET    | `/v1/auth/me`         | the signed-in account                   |
| DELETE | `/v1/auth/me`         | delete the account and everything in it |
| POST   | `/v1/ingest`          | submit a URL                            |
| POST   | `/v1/ingest/file`     | upload a PDF or image (`Idempotency-Key`) |
| GET    | `/v1/items`           | list (filter by `status`, `tag`, `source`; page with `before=<id>`) |
| GET    | `/v1/items/changes`   | ids changed and deleted since `since`   |
| GET    | `/v1/items/tags`      | tag counts for the chip filter         |
| GET    | `/v1/items/{id}`      | item detail (ETag)                      |
| PATCH  | `/v1/items/{id}`      | edit the note, re-run the pipeline      |
| POST   | `/v1/items/{id}/retry` | re-queue a failed item                 |
| GET    | `/v1/items/{id}/thumbnail` | an upload's thumbnail (JPEG)       |
| DELETE | `/v1/items/{id}`      | delete                                  |
| POST   | `/v1/search`          | hybrid retrieval (returns SearchHits)   |
| POST   | `/v1/ask`             | RAG answer with citations               |
| GET    | `/v1/chats`           | Ask history: each chat's questions (page with `before=<id>`) |
| GET    | `/v1/chats/{id}`      | one chat with its answers (ETag)        |
| PUT    | `/v1/chats/{id}`      | save a chat (the app picks the id; `version` → 409 on conflict) |
| DELETE | `/v1/chats/{id}`      | delete a chat                           |
| GET    | `/v1/wiki`            | list topic pages                        |
| GET    | `/v1/wiki/{slug}`     | topic page markdown (ETag)              |

Everything except the health checks, metrics, sign-up, sign-in and refresh takes
`Authorization: Bearer <access token>`. Send `st-auth-mode: header` with sign-up
and sign-in so the tokens come back as `st-access-token` and `st-refresh-token`
response headers. Saving, editing and retrying, Ask and Search are rate-limited
per user, sign-in and sign-up per address; a 429 or 503 carries `Retry-After`.

## Architecture

See [`docs/architecture.md`](docs/architecture.md).

```
                       ┌──────────────────────┐
   iOS                 │  SwiftUI app          │
   Share Sheet ──URL──▶│   Library · Wiki · Ask│
                       └──────────┬────────────┘
                              HTTPS (bearer)
                                  │
                       ┌──────────▼────────────┐
                       │  FastAPI              │
                       └──────────┬────────────┘
                                  │ enqueue (arq)
                       ┌──────────▼────────────┐
                       │  workers              │
                       │  1. ingest_item       │
                       │  2. synthesize_topics │
                       │  3. compact_topics    │
                       └──────────┬────────────┘
                                  │
                       ┌──────────▼────────────┐
                       │  Postgres + pgvector  │
                       └───────────────────────┘
```

Retrieval is **hybrid**: dense (Voyage `voyage-3-large` over `pgvector`
HNSW) ∪ BM25 (`tsvector`) → Reciprocal Rank Fusion → Voyage `rerank-2.5`
→ Claude Opus for the answer.

## Stack

- **Backend**: Python 3.12, FastAPI, async SQLAlchemy 2 (`asyncpg`),
  Postgres 16 + pgvector (HNSW), Redis + arq workers, Alembic. Anthropic
  SDK (Claude Haiku for analyze, Sonnet for synthesis, Opus for /ask),
  Voyage AI (`voyage-3-large` embed, `rerank-2.5`). `yt-dlp` +
  `youtube-transcript-api` for video extraction, `trafilatura` for web.
- **iOS**: SwiftUI (iOS 26+, Swift 6), a Share Extension that uploads
  directly, Keychain sharing and an App Group between the two, `URLSession`
  streaming for Ask, `SFSpeechRecognizer` for voice, APNs for push. No
  third-party packages. Project generated by XcodeGen.

## License

Personal project, no license currently granted. Contact the author for
reuse.
