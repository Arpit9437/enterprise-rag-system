# Document lifecycle and cleanup

## Lifecycle

File uploads start as `pending` after the upload is validated and persisted.
The worker changes the state to `processing`, then to `completed` only after
text extraction, chunking, and embedding persistence succeed. Empty files,
files without extractable text, malformed or encrypted PDFs, and embedding
provider failures end in `failed`; the API exposes only that status, not
exception details.

Metadata-only documents created with `POST /tenants/{tenant_id}/documents`
are intentionally supported as catalog records. They remain `pending`, have
no `content_type`, are not searchable, and cannot be processed or retried.
Uploading a file creates a separate file-backed document. The retry endpoint
is tenant-admin-only and accepts only failed file-backed documents. Concurrent
retry attempts are rejected by a conditional status update. Completed
documents are not manually reprocessed through the API.

Workers claim eligible records with a conditional update and a unique claim
token. The claim commit is short-lived; extraction and embedding happen after
the claim session is closed. Chunk replacement and completion are committed
together only if the task still owns the claim. A stale worker cannot complete
or fail a newer claim. `PROCESSING` claims expire after
`DOCUMENT_PROCESSING_LEASE_SECONDS` (default one hour); Celery Beat runs the
stuck-processing reconciler every five minutes and marks expired records
`FAILED`, where an administrator can retry them. Run Celery Beat alongside
the workers so the lease has automatic recovery.

Chunk size and overlap are configured with `DOCUMENT_CHUNK_SIZE` and
`DOCUMENT_CHUNK_OVERLAP`; the values used are recorded on successful
processing. Extracted text is capped by `MAX_DOCUMENT_TEXT_CHARS`. Embeddings
are generated in batches using `EMBEDDING_BATCH_SIZE`. The persisted embedding
model identity is stored on every chunk. Changing `EMBEDDING_MODEL` requires a
full re-embedding/reindex migration before vectors from the new model can be
compared with existing vectors.

## Recovering orphaned local files

Document rows and local files cannot be committed atomically. If database
deletion succeeds but local file deletion fails, the application logs the
storage key and cleanup-procedure warning; the request still reports the
successful database deletion.

If task dispatch fails after an upload or retry, the document is conditionally
marked `failed` so it remains visible and can be retried by a tenant
administrator. If a worker has already claimed it, the conditional update
leaves its current state unchanged.

For local development, stop the API and Celery worker, then run this from the
project root with the application's database and storage settings configured:

```sh
python -m scripts.cleanup_orphaned_document_files
```

The command removes unreferenced `.pdf` and `.txt` files from the configured
document storage directory and reports files it could not remove. It leaves
referenced files and symlinks untouched. Restart the API and worker after
cleanup. This local procedure is not a durable production deletion queue.
Tenant deletion uses the same post-commit file cleanup and recovery procedure.
See [OPERATIONS.md](./OPERATIONS.md) for user/tenant deletion, backups, and
conversation retention policy.

## Retrieval evaluation

The versioned sample questions and expected chunk IDs are in
`tests/data/retrieval_evaluation.json`. They cover exact terminology,
semantic matching, multiple relevant chunks, and an absent answer. Run
`pytest --no-cov -q tests/retrieval/test_retrieval_evaluation.py` to validate the dataset and
Recall@k, Precision@k, reciprocal-rank, and nDCG calculations. For changes to
retrieval ranking, compare the semantic-only, keyword-only, and hybrid
rankings against this gold set rather than relying only on manual examples.
