# Operations, security and data lifecycle

## Run and health

For local development, copy `.env.example` to `.env`, replace the sample
passwords, then run `docker compose up -d --build`. Compose builds one image
for the API, migration runner, worker and Beat; it waits for PostgreSQL and
Redis health, applies `alembic upgrade head`, then starts the application
processes separately. The database, Redis data, uploaded documents and
Hugging Face model cache use named volumes. The first worker start downloads
the embedding model into its persistent cache. The API and database ports bind
to loopback only; do not expose this development Compose file directly to the
public internet.

Check startup with `docker compose ps`, `docker compose logs migrate api worker
beat`, and `curl -fsS http://127.0.0.1:8000/health/ready`. For native macOS
development, the Celery worker can use `--pool=solo`; the Compose worker runs
in Linux with Celery's prefork pool.

`GET /health/live` reports process liveness. `GET /health/ready` checks
PostgreSQL, Redis, and whether a Celery worker responds; it returns `503`
until all three are healthy. Run Beat as well as a worker: Beat schedules
document-lease recovery every five minutes and conversation retention once a
day.

`GET /metrics` exports shared Redis-backed counters and duration sums/counts
in Prometheus text format. It includes HTTP status and latency, retrieval and
LLM latency/errors, document pending age, per-stage extraction/chunking/
embedding/persistence duration, and processing duration/status. Pending age
uses the document creation timestamp; it is not broker enqueue or queue wait
latency. These are aggregate sums/counts, not histograms. Metrics do not
export questions, prompts, document content, or identifiers. Restrict the
metrics route to a trusted monitoring network at the ingress layer. Metric
keys expire after 30 days without updates by default.

Requests receive an `X-Request-ID` response header. A valid UUID supplied in
the request header is retained; invalid values are replaced. Logs include that
ID, route, status, and duration, but do not include request bodies or prompts.

## RAG and conversation API

The FastAPI OpenAPI document describes the Pydantic request/response models.
The tenant-scoped endpoints require an authenticated tenant member:

- `POST /tenants/{tenant_id}/search` accepts `query`, bounded `top_k`, and
  optional document/type/date filters. It returns matching chunk metadata and
  retrieval scores; `total_results` is the number of returned results.
- `POST /tenants/{tenant_id}/ask` accepts `question` and the same retrieval
  filters. It returns the original question, grounded answer, and only the
  citations whose source labels survived validation. No relevant chunks
  returns a deliberate no-context answer without calling the LLM.
- Invalid inputs return `422`, non-member tenant access returns `404`,
  request/provider throttling returns `429`, and answer-provider failures
  return a controlled `502`. Search embedding/retrieval outages return `503`.
- `POST /tenants/{tenant_id}/conversations` creates an owner-private
  conversation. `POST .../{conversation_id}/messages` stores a user question
  and assistant answer; `GET` lists/detail use bounded pagination, and `DELETE`
  removes an owner's conversation and its messages.

The `X-Request-ID` response header applies to both success and handled error
responses, allowing a client to correlate a failed call with request logs.

## Resource and provider controls

Copy `.env.example` to `.env` for local setup, then replace the sample database
password and JWT secret. Production secrets must be injected through a secret
manager or protected environment configuration, not committed to source
control. The API requires a JWT secret of at least 32 characters and accepts
only HS256. Access tokens expire; deleting an account invalidates its tokens
because protected requests re-check the user record.

The configured provider is `groq`. Its key stays server-side, calls have a
bounded timeout and output-token limit, and provider failures return a
controlled `429` or `502`. A missing key is only reported when generation is
needed, so no-context answers do not depend on the provider being configured.

Redis-backed fixed-window request limits are enabled by default and fail
closed with `503` if Redis is unavailable. Limits are per source IP and
operation. The limiter hashes the IP for its Redis key and does not trust
client-supplied forwarding headers. Configure the ASGI server/reverse proxy
to provide the correct trusted client address; do not make an untrusted
forwarded-address header authoritative. Upload size is enforced while reading
the body, and query length, result count, context characters, provider timeout,
and Celery worker concurrency are bounded by settings.

The API enables CORS only for explicit `CORS_ALLOWED_ORIGINS`; wildcard origins
are rejected. TLS must terminate at a trusted reverse proxy or load balancer
in deployed environments. The deployment must preserve authorization and
request-ID headers and prevent direct public access to the application port.

## Authorization and data retention

Documents are shared among members of their tenant, consistent with the
current document and search authorization model. Conversations are private
to their creator, even when other users belong to the same tenant. Every
conversation lookup is scoped by tenant and owner; follow-up history is
bounded by the configured message and character limits and is used only to
resolve references, never as factual evidence.

Conversations and their messages are deleted after 90 days without a new
message. Celery Beat runs the cleanup daily. If Beat is stopped, expiration
is delayed until it resumes. Owners may delete a conversation immediately.
Deleting an account cascades its memberships and owned conversations; an
account that is the last administrator of a tenant cannot be deleted until
administration is transferred or that tenant is deleted.

Tenant deletion is administrator-only. It deletes tenant metadata, documents,
chunks, memberships, conversations, and messages transactionally in
PostgreSQL, then removes associated local files. The database and filesystem
are not one transaction. If file cleanup fails, the API logs the failure and
the orphan cleanup procedure in [DOCUMENTS.md](./DOCUMENTS.md) can remove
unreferenced local files. This is a recoverable local-storage baseline, not a
durable production deletion outbox; deployments requiring guaranteed cleanup
after process crashes should add a durable deletion queue before production.

Deleting an individual document removes its file and searchable chunks but
does not rewrite an owner's retained conversation history. Historical answers
and citation IDs remain subject to the 90-day conversation retention period or
the owner's explicit conversation deletion; they are not searchable evidence.

User deletion does not delete tenant-shared documents. Removing a member
revokes access through membership checks; tenant data remains available to
the tenant's other members. The app does not expose a tenant-wide document
access-control list.

Database backups and local document files must be backed up as a coordinated
set. Encrypt backups, restrict access, test restoration, and keep the
database/storage snapshot timestamps aligned. Redis contains transient task
queue state, rate-limit counters, and operational metrics; it is not the
source of truth for documents or conversations. After a restore, verify
database migrations, document-file references, the worker, and Beat before
reopening traffic. Log retention is controlled by the deployment's logging
system; logs are designed to exclude passwords, bearer tokens, prompts, and
document contents.

## Deployment and recovery

### Provision and configure

Provision PostgreSQL with the pgvector extension available, Redis, durable
document storage, and a container runtime. This Compose file is a local
reference deployment: for production, use managed PostgreSQL/Redis and
durable shared storage where appropriate, terminate TLS at a trusted ingress,
and limit network access to the API. Do not publish database or Redis ports.

Inject `DATABASE_URL`, `DATABASE_URL_CONTAINER`, `REDIS_URL`, `JWT_SECRET_KEY`,
`POSTGRES_PASSWORD`, `GROQ_API_KEY`, and `CORS_ALLOWED_ORIGINS` from the
deployment's secret/config system. `DATABASE_URL_CONTAINER` must point at the
database address resolvable by the containers; URL-encode reserved characters
in its username/password and keep its password consistent with
`POSTGRES_PASSWORD`. Compose assembles a local fallback URL from
`POSTGRES_PASSWORD`. Keep `DEBUG=false`, set `ENV=production`, and use a
unique JWT key of at least 32 characters. The sample `.env.example` values are
for local development only. Do not bake environment files or credentials into
the image.

The image installs exact top-level package versions from `requirements.txt`
and pins transitive versions in `constraints.txt`; test dependency versions
are separate in `requirements-dev.txt`. Review and update both pins together.
The image includes PyTorch and sentence-transformers and is consequently
large. The Python base tag and OS packages should be pinned to approved image
digests/versions by the production build pipeline.

Pool settings apply per application process: `DATABASE_POOL_SIZE` plus
`DATABASE_MAX_OVERFLOW` is the maximum pool capacity for each process, and
`DATABASE_POOL_TIMEOUT_SECONDS` bounds pool checkout. Include API replicas,
each Celery prefork child, Beat, migrations, monitoring and administrative
connections when setting PostgreSQL `max_connections`. The defaults are
development starting points, not a capacity recommendation. Worker
concurrency, task soft/hard time limits, per-child memory recycling, and the
Compose worker memory/CPU limits are configurable; size them from measured
document sizes and host memory. Avoid setting concurrency so high that
multiple model copies exhaust worker memory.

### Migrate and start services

For an initial local deployment, `docker compose up -d --build` runs the
one-shot migration service before the API, worker and Beat. For a controlled
deployment, build and test the image first, then run the migration explicitly:

```sh
docker compose build
docker compose up --build migrate
docker compose up -d api worker beat
docker compose ps
curl -fsS http://127.0.0.1:8000/health/ready
```

In production, run migrations once as a release job with the same application
version and database configuration as the release. Back up the database
before schema changes, and use backward-compatible migrations when old and
new application versions may overlap. The readiness check requires database,
Redis, and a responding Celery worker; `/health/live` only checks process
liveness.

### Backup and restore

Take coordinated, encrypted backups of PostgreSQL and document storage. Run
the backup during a quiet period or record a consistent snapshot boundary so
database references and files can be restored together. Example local commands
(create `backups/` first and protect the resulting files):

```sh
mkdir -p backups
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > backups/database.dump
docker compose run --rm --no-deps -v "$PWD/backups:/backup" --entrypoint tar api -czf /backup/documents.tar.gz -C /app/storage/documents .
```

To restore, stop API/worker/Beat writes, restore the dump to the intended
database, and restore the matching document archive into the document
storage volume. For this local Compose setup:

```sh
docker compose stop api worker beat
cat backups/database.dump | docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists'
docker compose run --rm --no-deps -v "$PWD/backups:/backup" --entrypoint tar api -xzf /backup/documents.tar.gz -C /app/storage/documents
docker compose run --rm migrate
docker compose up -d api worker beat
```

Review the target database carefully before using `--clean` because it
replaces existing objects. Check readiness and verify representative document
retrieval and file references before reopening traffic. Test this procedure
periodically in an isolated environment. Redis can be restarted empty; task
recovery/reconciliation handles expired processing claims, but queued work
may need to be dispatched again.

### Failed deployment and performance follow-up

If migration or readiness fails, keep ingress closed, inspect
`docker compose logs migrate api worker beat`, and preserve the failed
deployment logs. Fix configuration or code and redeploy; do not blindly roll
back a database migration. Restore the pre-deployment database and matching
file backup only when the migration is not safely reversible and the recovery
has been rehearsed. Confirm migrations, API readiness, worker/Beat operation,
and document retrieval after recovery.

The current local PostgreSQL dataset contains one document and three chunks.
Warm `EXPLAIN (ANALYZE, BUFFERS)` samples for representative semantic,
keyword and list queries were approximately 0.16 ms, 0.48 ms and 0.02 ms,
respectively; these timings are single-run, tiny-table observations, not a
benchmark. The planner used the tenant B-tree index for chunk filtering,
scanned/sorted the tiny document table for listing, and did not choose the
HNSW or full-text GIN index at this cardinality. The delete lookup used the
tenant index and filtered by document. Existing migrations define the HNSW
cosine and English full-text GIN indexes. Do not infer index effectiveness or
production capacity from these plans; use a representative non-production
dataset, `EXPLAIN (ANALYZE, BUFFERS)`, and workload measurements before adding
indexes or changing pool/concurrency settings.
