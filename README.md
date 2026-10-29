# Enterprise RAG

Enterprise RAG is a tenant-scoped document ingestion, search, and question-answering application. Users upload PDF or UTF-8 text files to a workspace; background workers extract and index their contents; members can search indexed documents or ask questions and receive answers with validated citations.

The repository includes a FastAPI backend and a small React + JavaScript web client. PostgreSQL stores application data and vector embeddings, Redis provides the Celery broker and shared rate-limit/metrics storage, and a Celery worker processes documents asynchronously.

## Contents

- [Features](#features)
- [Architecture and data flow](#architecture-and-data-flow)
- [Requirements](#requirements)
- [Run with Docker Compose](#run-with-docker-compose)
- [Run the frontend](#run-the-frontend)
- [Run the backend natively](#run-the-backend-natively)
- [Configuration](#configuration)
- [Using the application](#using-the-application)
- [HTTP API](#http-api)
- [Document lifecycle and recovery](#document-lifecycle-and-recovery)
- [Development and tests](#development-and-tests)
- [Project layout](#project-layout)
- [Troubleshooting](#troubleshooting)
- [Security and deployment notes](#security-and-deployment-notes)

## Features

- Email and password registration with Argon2 password hashing and expiring bearer access tokens.
- Workspaces (tenants), database-backed memberships, and administrator/member roles.
- Tenant-scoped document upload, listing, status, retry, and deletion.
- PDF and UTF-8 plain-text extraction, deterministic overlapping chunks, and 384-dimensional embeddings.
- Hybrid retrieval that combines vector similarity and PostgreSQL full-text search using reciprocal-rank fusion.
- Grounded question answering with source-label validation and citations identifying the source document and chunk.
- Private conversation histories for the authenticated conversation owner, with bounded context and pagination.
- A React interface for signing in, selecting workspaces, chatting, searching, and managing documents.
- Readiness and liveness endpoints, request IDs, structured logs, and Prometheus-format metrics.

## Architecture and data flow

| Component | Responsibility |
|---|---|
| FastAPI | Authentication, tenant authorization, document and conversation APIs, search, and answer generation. |
| PostgreSQL + pgvector | Users, tenants, memberships, document metadata, chunks, conversations, and embeddings. Migrations create the required schema and search indexes. |
| Redis | Celery task broker and shared storage for rate limits and aggregate metrics. |
| Celery worker | Claims eligible documents, extracts text, chunks content, generates embeddings, and atomically persists the new chunks and completed status. |
| Celery Beat | Schedules recovery of expired document-processing claims and conversation-retention cleanup. |
| React + Vite | Lightweight chat-first web interface. During local development, Vite proxies API requests to FastAPI on port 8000. |
| Sentence Transformers | `sentence-transformers/all-MiniLM-L6-v2` embeddings, 384 dimensions by default. |
| Groq | Configured answer-generation provider; credentials remain on the backend. |

Typical ingestion and retrieval flow:

1. A signed-in workspace member uploads a supported file. The API validates its type, contents, and actual byte count, stores it under a generated storage name, and creates a `pending` document record.
2. The API submits a Celery task. A worker claims the document, then extracts and chunks text and creates embeddings.
3. The worker saves the new chunks and marks the document `completed` in a transaction. Failed processing is exposed as `failed`, without returning internal exception details to the client.
4. Search filters by tenant and completed document, then combines semantic and keyword rankings. Search filters can narrow the results by document ID, media type, and creation time.
5. Ask uses retrieved context to generate a grounded answer. Citation validation removes source labels that do not correspond to sources used in the response.

## Requirements

### Docker Compose (recommended)

- Docker Engine or Docker Desktop with the Compose plugin.
- A network connection the first time the worker starts, to download the embedding model and container dependencies.
- A Groq API key if you want answers generated from retrieved context. Search and no-context answers do not require a Groq call.

### Native development

- Python 3.14 is the version used by the application container.
- Node.js and npm for the web client.
- PostgreSQL with the pgvector extension.
- Redis.
- A Groq API key for answer generation from relevant document context.

## Run with Docker Compose

1. Create a local environment file:

   ```sh
   cp .env.example .env
   ```

   Change `POSTGRES_PASSWORD` and `JWT_SECRET_KEY` to local development values. The JWT secret must be at least 32 characters. Set `GROQ_API_KEY` if you want provider-generated answers. Do not commit `.env`.

2. Build and start PostgreSQL, Redis, migrations, the API, a Celery worker, and Celery Beat:

   ```sh
   docker compose up -d --build
   docker compose ps
   ```

   The one-shot `migrate` service runs `alembic upgrade head` before the API and worker start. The first worker startup downloads the embedding model; this can take a while.

3. Check application readiness:

   ```sh
   curl -fsS http://127.0.0.1:8000/health/ready
   ```

   Readiness requires PostgreSQL, Redis, and a responding Celery worker. The API and database ports in the development Compose file bind to loopback.

4. Start the frontend as described in [Run the frontend](#run-the-frontend).

Useful Compose commands:

```sh
docker compose logs -f api worker beat
docker compose logs migrate
docker compose restart api worker beat
```

Compose uses named volumes for PostgreSQL, Redis, uploaded files, and the Hugging Face model cache. See [OPERATIONS.md](./OPERATIONS.md) for backup, restore, cleanup, and operational procedures.

## Run the frontend

With the API available at `http://127.0.0.1:8000`:

```sh
cd frontend
npm ci
npm run dev
```

Open the local URL printed by Vite (normally `http://localhost:5173`). The Vite development proxy forwards `/auth`, `/tenants`, and `/health` to the local API. The browser does not need a permissive CORS configuration for this setup.

To create a production frontend bundle:

```sh
cd frontend
npm ci
npm run build
```

The built static site is written to `frontend/dist/`. For a separately hosted frontend, set `VITE_API_BASE_URL` at build time to the API origin and configure the backend's `CORS_ALLOWED_ORIGINS` with the exact trusted frontend origin(s).

## Run the backend natively

You can run the API and worker on the host while using Compose for PostgreSQL and Redis:

1. Create `.env` as described above. For host-side processes, use `DATABASE_URL` with the host-mapped PostgreSQL port (the Compose default is `5433`) and `REDIS_URL=redis://localhost:6379/0`.
2. Start the database and broker:

   ```sh
   docker compose up -d db redis
   ```

3. Create and activate a virtual environment, then install development dependencies and migrate the database:

   ```sh
   python3.14 -m venv .venv
   source .venv/bin/activate
   python -m pip install -r requirements-dev.txt
   python -m alembic upgrade head
   ```

4. In separate terminals with the virtual environment activated, run the API, worker, and Beat:

   ```sh
   uvicorn app.main:app --reload
   ```

   ```sh
   celery -A app.core.celery_app.celery_app worker --pool=solo --loglevel=INFO
   ```

   ```sh
   celery -A app.core.celery_app.celery_app beat --loglevel=INFO
   ```

   Celery's `solo` pool is useful for local macOS development. It is not a production worker-concurrency recommendation.

## Configuration

Settings are loaded from environment variables and `.env`; `.env.example` is the development template. The complete setting definitions and validation rules are in [`app/core/config.py`](./app/core/config.py).

| Variable | Purpose | Default |
|---|---|---|
| `ENV` | Environment name. | `development` |
| `DEBUG` | Debug configuration flag. | `false` |
| `DATABASE_URL` | SQLAlchemy PostgreSQL connection URL. Required. | — |
| `POSTGRES_PASSWORD` | Database password setting. Required. | — |
| `DATABASE_POOL_SIZE` | SQLAlchemy pool size. | `5` |
| `DATABASE_MAX_OVERFLOW` | Additional overflow connections. | `5` |
| `JWT_SECRET_KEY` | HS256 signing secret; must be at least 32 characters. Required. | — |
| `JWT_ALGORITHM` | JWT signing algorithm. Only `HS256` is supported. | `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Access-token lifetime. | `30` |
| `REDIS_URL` | Redis connection URL for Celery and shared services. | `redis://localhost:6379/0` |
| `DOCUMENT_STORAGE_DIR` | Local directory for uploaded files. | `storage/documents` |
| `MAX_DOCUMENT_SIZE_BYTES` | Maximum upload size, enforced while reading the upload. | `10485760` (10 MiB) |
| `DOCUMENT_CHUNK_SIZE` / `DOCUMENT_CHUNK_OVERLAP` | Chunk length and overlap. | `1000` / `150` |
| `MAX_DOCUMENT_TEXT_CHARS` | Maximum extracted text accepted for indexing. | `5000000` |
| `EMBEDDING_MODEL` / `EMBEDDING_BATCH_SIZE` | Embedding model identity and batch size. | `sentence-transformers/all-MiniLM-L6-v2` / `32` |
| `SEARCH_DEFAULT_TOP_K` / `SEARCH_MAX_TOP_K` | Default and maximum search result counts. | `5` / `20` |
| `SEARCH_RANK_CONSTANT` | Reciprocal-rank fusion constant. | `60` |
| `LLM_PROVIDER` / `LLM_MODEL` | Answer provider and model. This build supports `groq`. | `groq` / `llama-3.3-70b-versatile` |
| `GROQ_API_KEY` | Server-side key for Groq answer generation. | unset |
| `LLM_TIMEOUT_SECONDS` / `LLM_MAX_COMPLETION_TOKENS` | Provider call timeout and response limit. | `30` / `800` |
| `MAX_CONTEXT_CHARS` | Maximum retrieved context passed for answer generation. | `12000` |
| `CONVERSATION_HISTORY_MAX_MESSAGES` / `CONVERSATION_HISTORY_MAX_CHARS` | Bounds for conversation context. | `10` / `8000` |
| `CONVERSATION_RETENTION_DAYS` | Inactivity period before conversation cleanup. | `90` |
| `DOCUMENT_PROCESSING_LEASE_SECONDS` | Processing claim lease before recovery considers it expired. | `3600` |
| `DOCUMENT_PROCESSING_WORKER_CONCURRENCY` | Configured document-worker concurrency. | `2` |
| `RATE_LIMIT_ENABLED` | Enables Redis-backed request limits. | `true` |
| `CORS_ALLOWED_ORIGINS` | JSON array of explicit trusted browser origins. Wildcards are rejected. | `[]` |

Additional rate limits, task timeouts, worker resource controls, Redis timeouts, and metrics retention settings are documented in `.env.example` and validated in the application configuration. Do not put credentials in the frontend bundle; `VITE_API_BASE_URL` is an origin, not a secret.

Changing the embedding model is a data migration, not merely a runtime setting change. Existing vectors must be re-embedded consistently before comparing them with vectors generated by a different model.

## Using the application

1. Register an account and sign in.
2. Create a workspace. Its creator becomes the first administrator. An administrator can add existing accounts by email and assign supported roles.
3. Open **Documents** and upload a PDF or UTF-8 `.txt` file.
4. Check its status. An upload being accepted means it was stored and queued; wait for `completed` before expecting it in retrieval.
5. Use **Search** to inspect matching source passages, or ask a question in a conversation.
6. Select a citation to open the document list and locate the referenced source.

Documents are shared among members of a tenant. Conversations are private to the user who created them, even if other users are members of the same workspace.

## HTTP API

Interactive API documentation is available when the backend is running:

- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`
- OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`

Most tenant routes require an `Authorization: Bearer <access-token>` header and active membership in the tenant identified in the URL. Member-management routes and failed-document retries require a tenant administrator. Authorization is checked against database membership records; a client-supplied tenant ID or role does not grant access.

### Main endpoints

| Method and path | Access | Purpose |
|---|---|---|
| `POST /auth/register` | Public | Register with a unique email and password. |
| `POST /auth/login` | Public | Authenticate and receive an expiring access token. |
| `GET /auth/me` | Signed in | Read the current account. |
| `DELETE /auth/me` | Signed in | Delete the current account, unless it is the last administrator of a tenant. |
| `POST /tenants` | Signed in | Create a workspace; creator becomes its administrator. |
| `GET /tenants` | Signed in | List the caller's workspaces. |
| `POST /tenants/{tenant_id}/members` | Tenant admin | Add an existing account to the workspace. |
| `GET /tenants/{tenant_id}/members` | Tenant admin | List workspace members. |
| `PATCH /tenants/{tenant_id}/members/{user_id}` | Tenant admin | Change a member's role. |
| `DELETE /tenants/{tenant_id}/members/{user_id}` | Tenant admin | Remove a member. |
| `DELETE /tenants/{tenant_id}` | Tenant admin | Delete tenant records and attempt stored-file cleanup. |
| `POST /tenants/{tenant_id}/documents` | Tenant member | Create metadata-only document catalog entry. |
| `GET /tenants/{tenant_id}/documents` | Tenant member | List tenant documents. |
| `GET /tenants/{tenant_id}/documents/{document_id}` | Tenant member | Read document metadata and processing status. |
| `POST /tenants/{tenant_id}/documents/upload` | Tenant member | Upload a PDF or UTF-8 text file and queue processing. |
| `POST /tenants/{tenant_id}/documents/{document_id}/retry` | Tenant admin | Retry a failed file-backed document. |
| `DELETE /tenants/{tenant_id}/documents/{document_id}` | Tenant member | Delete document metadata, chunks, and its stored file. |
| `POST /tenants/{tenant_id}/search` | Tenant member | Run tenant-scoped hybrid search. |
| `POST /tenants/{tenant_id}/ask` | Tenant member | Generate a grounded answer with validated citations. |
| `POST /tenants/{tenant_id}/conversations` | Tenant member | Create an owner-private conversation. |
| `GET /tenants/{tenant_id}/conversations` | Tenant member | List the caller's conversations. |
| `GET /tenants/{tenant_id}/conversations/{conversation_id}` | Conversation owner | Read a conversation and paginated messages. |
| `POST /tenants/{tenant_id}/conversations/{conversation_id}/messages` | Conversation owner | Send a follow-up question. |
| `DELETE /tenants/{tenant_id}/conversations/{conversation_id}` | Conversation owner | Delete conversation history. |
| `GET /health/live` | Public | Liveness check. |
| `GET /health/ready` | Public | Readiness check for PostgreSQL, Redis, and a Celery worker. |
| `GET /metrics` | Deployment-dependent | Prometheus text-format metrics; restrict at the ingress layer. |

### Pagination, search, and responses

- Document and conversation lists accept `limit` (default `20`, range `1–100`) and non-negative `offset`. Responses include `total`, which counts all matches rather than just the current page. Ordering is deterministic.
- Conversation details accept `message_limit` (default `50`, range `1–100`) and non-negative `message_offset`.
- Search accepts `query`, bounded `top_k` (maximum `20`), and optional `document_ids`, `content_type`, `created_after`, and `created_before` filters. Date filters must include a timezone. Results include chunk/document identifiers, chunk index, content, and semantic, keyword, and fused scores.
- Ask accepts a `question` and the retrieval filters. Its response contains `question`, `answer`, and `citations`. Each citation identifies `source_id`, `document_id`, `chunk_id`, and `chunk_index`. If no relevant context exists, the API returns a deliberate no-context answer without calling the LLM.
- Metadata-only documents are supported as catalog records. They remain pending and are not searchable because they do not have uploaded file content.
- Document status values are `pending`, `processing`, `completed`, and `failed`. Internal storage keys and stack traces are not public response fields.
- Typical errors include `401` for invalid authentication, `404` for inaccessible tenant resources, `409` for duplicate identities or state conflicts, `413` for oversized uploads, `415` for unsupported file types, `422` for invalid input, `429` for request/provider throttling, `502` for answer-provider failures, and `503` for unavailable dependencies or failed task dispatch.
- Responses include an `X-Request-ID` header. Valid UUID request IDs supplied by clients are retained; invalid or missing IDs are replaced. Use the response ID to correlate errors with server logs.

### cURL example

The following illustrates registration, login, and workspace creation. Replace the sample credentials and use a unique email:

```sh
curl -sS -X POST http://127.0.0.1:8000/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"replace-with-a-strong-password"}'

TOKEN=$(
  curl -sS -X POST http://127.0.0.1:8000/auth/login \
    -H 'Content-Type: application/json' \
    -d '{"email":"you@example.com","password":"replace-with-a-strong-password"}' \
  | python -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'
)

curl -sS -X POST http://127.0.0.1:8000/tenants \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"People Ops"}'
```

Use the returned tenant ID to upload a file:

```sh
curl -sS -X POST "http://127.0.0.1:8000/tenants/TENANT_ID/documents/upload" \
  -H "Authorization: Bearer $TOKEN" \
  -F 'file=@./handbook.pdf'
```

Poll `GET /tenants/{tenant_id}/documents/{document_id}` until the status is `completed`, then use `POST /tenants/{tenant_id}/search` or `POST /tenants/{tenant_id}/ask`. For exact request and response schemas, use Swagger UI or the OpenAPI JSON.

## Document lifecycle and recovery

File-backed uploads transition through:

```text
pending -> processing -> completed
                    \-> failed
```

Only completed documents are searchable. Metadata-only records remain pending and have no indexed content. Empty or unextractable files, malformed or encrypted PDFs, and embedding failures are processing failures. The API exposes the status without leaking internal exception information.

Workers claim eligible records using a conditional update and claim token. Expired processing leases are reconciled by Celery Beat; failed uploads can be retried by a tenant administrator when the stored file is available. Completed documents cannot be manually reprocessed through the retry endpoint.

The PostgreSQL transaction and local filesystem cannot be committed atomically. If database deletion succeeds but local file deletion fails, the application logs the cleanup failure. For local recovery, follow the orphan-file cleanup procedure in [DOCUMENTS.md](./DOCUMENTS.md). Run Celery Beat alongside workers so processing-lease and conversation-retention jobs run.

## Development and tests

### Backend tests

The test configuration uses the configured database connection and PostgreSQL-specific features (including pgvector). Use a dedicated local development/test database, not production data. Start the Compose database, apply the schema, and configure the test environment before running tests:

```sh
docker compose up -d db
source .venv/bin/activate
python -m alembic upgrade head
python -m pytest -q
```

The test fixture wraps database work in a transaction and rolls it back after each test. External providers and task calls are mocked in unit/API tests where appropriate. Database-specific behavior should also be exercised against PostgreSQL with pgvector enabled.

Tests are grouped by backend domain under `tests/`. The default run measures
line and branch coverage and fails if total coverage falls below 75%. Focused
development runs can skip the whole-suite coverage threshold:

```sh
python -m pytest --no-cov -q tests/conversations
python -m pytest --no-cov -q tests/documents
python -m pytest --no-cov -q tests/retrieval
```

The retrieval evaluation data is in
[`tests/data/retrieval_evaluation.json`](./tests/data/retrieval_evaluation.json).
The test in
[`tests/retrieval/test_retrieval_evaluation.py`](./tests/retrieval/test_retrieval_evaluation.py)
calculates Recall@k, Precision@k, reciprocal rank, and nDCG for semantic,
keyword, and hybrid rankings.

### Frontend checks

```sh
cd frontend
npm ci
npm run build
npm audit
```

The frontend package currently defines development, preview, and production-build scripts; no frontend unit-test script is configured.

### Database migrations

Compose runs migrations as part of startup. To run them manually from the project root:

```sh
python -m alembic upgrade head
python -m alembic check
```

Migration scripts are in [`migrations/versions/`](./migrations/versions/). Review migration operations and make a coordinated database and document-storage backup before applying schema changes to a persistent environment.

## Project layout

```text
app/
  api/routes/       FastAPI endpoints for auth, tenants, documents, search, ask, and conversations
  core/             Settings, security, dependencies, Celery, rate limiting, and observability
  db/               SQLAlchemy engine and session setup
  models/           SQLAlchemy database models
  schemas/          Pydantic request and response schemas
  services/         Ingestion, extraction, embeddings, retrieval, ranking, and answer generation
  tasks/            Celery document and conversation maintenance tasks
frontend/
  src/              React application and CSS
  vite.config.js    Local frontend development proxy
migrations/
  versions/         Alembic schema migrations
tests/
  authentication/   Registration, login, and token tests
  tenants/          Membership, roles, and tenant-isolation tests
  documents/        Upload, processing, task, and lifecycle tests
  retrieval/        Search, citation, answer, and evaluation tests
  conversations/    Private conversation and retention tests
  platform/         Configuration, provider, and observability tests
  data/             Versioned retrieval evaluation dataset
  conftest.py       Shared test database fixtures
scripts/            Operational and local recovery scripts
storage/documents/  Default local upload directory (runtime data; do not commit)
```

## Troubleshooting

### Readiness returns `503`

`/health/ready` requires a reachable database, Redis, and Celery worker. Inspect `docker compose ps` and `docker compose logs db redis api worker beat`. In native development, confirm that host-side `DATABASE_URL` uses the mapped PostgreSQL port and Redis is available at the configured URL.

### Uploaded document remains pending or processing

Confirm that the Celery worker and Beat are running and connected to the same Redis broker and database as the API. Check worker logs for extraction or embedding errors. Beat performs expired-claim recovery; without Beat, a crashed worker's claim may remain until recovery runs.

### Search returns no results

Confirm the document status is `completed`; pending, processing, failed, and metadata-only records are not indexed. Check that the request uses the correct tenant ID and that optional document/type/date filters include the document.

### Answer generation fails

Search can work without a configured Groq key, but answer generation for relevant results requires `GROQ_API_KEY`. Check the API logs using the `X-Request-ID`; provider throttling returns `429`, while other provider failures return a controlled `502`.

### Frontend cannot reach the API

For Vite development, start FastAPI on `127.0.0.1:8000`; the configured proxy forwards API paths there. For a separately served frontend, set `VITE_API_BASE_URL` at build time and add its exact origin to `CORS_ALLOWED_ORIGINS`.

## Security and deployment notes

- Replace all example credentials. Use a secret manager or protected environment injection for production secrets; do not commit `.env` or bake credentials into images.
- Use TLS at a trusted ingress or reverse proxy and restrict direct access to the application, database, Redis, and metrics endpoints.
- Configure only explicit trusted CORS origins. Wildcard origins are rejected by settings validation.
- Keep tenant authorization in the backend. The UI's selected workspace and route IDs are not authorization controls.
- Access tokens expire. This project does not implement refresh tokens; account deletion invalidates tokens because protected requests re-check the account.
- Uploaded files, the database, and Redis have separate persistence and backup requirements. Coordinate backups and test restore procedures; see [OPERATIONS.md](./OPERATIONS.md).
- The Compose configuration is intended for local development and demonstration, not direct public production exposure. Review resource limits, network policy, secret handling, storage durability, TLS, observability access, and recovery procedures before deployment.

## Additional documentation

- [auth_exp.md](./auth_exp.md) — authentication, authorization, roles, data relationships, and tenant-isolation demos.
- [OPERATIONS.md](./OPERATIONS.md) — health checks, metrics, resource controls, security, retention, backups, and recovery.
- [DOCUMENTS.md](./DOCUMENTS.md) — document lifecycle, retry behavior, lease recovery, cleanup, and retrieval evaluation.
