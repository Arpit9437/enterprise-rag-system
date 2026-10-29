# Authentication, Authorization, and Tenant Isolation

This document explains how identity and access control work in this application: how accounts authenticate, how workspace roles are assigned, how records are connected to tenants, and how to demonstrate that one tenant cannot access another tenant's data.

## 1. The concepts

- **Authentication** answers: "Who is making this request?" A user signs in with an email and password and receives a signed, expiring access token.
- **Authorization** answers: "What may this authenticated user do here?" The API checks the user's membership and role in the specific tenant named by the request.
- **Tenant isolation** means tenant-owned records and retrieval results stay within the tenant boundary. Knowing or guessing a tenant, document, or chunk ID is not a grant of access.

The browser is not the security boundary. The backend checks identity, membership, role, and tenant-scoped data on every protected request.

## 2. Data relationships

The main identity and content relationships are:

```mermaid
erDiagram
    USER ||--o{ TENANT_MEMBERSHIP : joins
    TENANT ||--o{ TENANT_MEMBERSHIP : has
    TENANT ||--o{ DOCUMENT : owns
    DOCUMENT ||--o{ DOCUMENT_CHUNK : contains
    TENANT ||--o{ DOCUMENT_CHUNK : scopes
    USER ||--o{ CONVERSATION : owns
    TENANT ||--o{ CONVERSATION : scopes
    CONVERSATION ||--o{ CONVERSATION_MESSAGE : contains

    USER {
        uuid id PK
        string email UK
        string password_hash
        boolean is_active
    }
    TENANT {
        uuid id PK
        string name UK
    }
    TENANT_MEMBERSHIP {
        uuid id PK
        uuid user_id FK
        uuid tenant_id FK
        string role
    }
    DOCUMENT {
        uuid id PK
        uuid tenant_id FK
        string filename
        string status
    }
    DOCUMENT_CHUNK {
        uuid id PK
        uuid tenant_id FK
        uuid document_id FK
        int chunk_index
        text content
    }
    CONVERSATION {
        uuid id PK
        uuid tenant_id FK
        uuid owner_id FK
        string title
    }
    CONVERSATION_MESSAGE {
        uuid id PK
        uuid conversation_id FK
        string role
        text content
    }
```

### What the relationships mean

1. **Users and tenants have a many-to-many relationship through `tenant_memberships`.** A user can join several tenants, and a tenant can have several users. Each user/tenant pair has one membership row and one role for that tenant. The database enforces uniqueness of `(user_id, tenant_id)`.
2. **A tenant owns its documents.** Each document has a `tenant_id`; document list, status, retry, and delete operations use tenant-scoped lookup conditions.
3. **A document owns its chunks.** A chunk stores both `document_id` and `tenant_id`. A composite foreign key requires that pair to match the parent document, preventing a chunk from being attached to a document under a different tenant.
4. **A conversation is scoped to a tenant and owned by one user.** Its `tenant_id` says which workspace's knowledge it uses; `owner_id` makes the conversation private to its creator. Other members of the same tenant do not automatically gain access to it.
5. **Messages belong to conversations.** They do not have a separate tenant membership; access is enforced by looking up the parent conversation with its tenant and owner.
6. Foreign keys cascade deletion for membership and tenant-owned database records where configured. Local files are not part of a database transaction; see [DOCUMENTS.md](./DOCUMENTS.md) for document deletion and file cleanup.

## 3. How a role is chosen and assigned

The application currently supports two tenant roles:

| Role | Meaning |
|---|---|
| `admin` | Can use member-management operations, delete the tenant, and retry failed file-backed document processing. Admins can also perform ordinary member actions. |
| `member` | Can access the tenant's documents, search, ask questions, and use their own conversations. Cannot manage members, delete the tenant, or retry failed documents. |

Roles are **per tenant**, not global account attributes. The same account can be an `admin` in one tenant and a `member` in another.

### Assignment rules

- When an authenticated user creates a tenant with `POST /tenants`, the API creates that user's membership as `admin`. The client does not submit a role for tenant creation.
- An existing tenant admin adds an existing account by email with `POST /tenants/{tenant_id}/members`. If the request omits `role`, it defaults to `member`. An administrator may explicitly assign `admin` or `member`.
- An admin can change an existing member's role with `PATCH /tenants/{tenant_id}/members/{user_id}`. Only the two supported roles are accepted by the request schema and database constraint.
- Users cannot promote themselves by placing a role in a document, search, chat, or other ordinary request. The server uses the saved membership row. Add-member and role-update routes require an admin dependency before changing membership.
- A tenant must retain at least one administrator. The API rejects demoting or removing the last admin.
- There is no implicit cross-tenant inheritance: membership and role in Tenant A say nothing about access to Tenant B.

**Current UI note:** the React app supports account registration/sign-in, workspace creation/selection, document management, search, and chat. It does not currently include a member-administration screen. To add or promote members for a demo, use the API documented in Swagger/ReDoc or the endpoint examples below.

## 4. Authentication flow

### Registration

1. The client sends an email and password to `POST /auth/register`.
2. Email input is validated and normalized to lowercase; duplicate identities are rejected.
3. The service hashes the password using the configured recommended Argon2 password-hashing implementation.
4. Only `password_hash` is stored. The API response does not return the password or hash.
5. Registration creates an account only; it does not automatically create a tenant or sign the user in.

### Login and token validation

1. The client sends credentials to `POST /auth/login`.
2. The service verifies the submitted password against the stored hash. Invalid email and invalid password produce the same `401` response so the endpoint does not reveal whether an account exists.
3. On success, the API signs a JWT with the configured server-side `JWT_SECRET_KEY` and HS256 algorithm. The token includes the user's ID as `sub` and an expiration time (`exp`); token lifetime is controlled by `ACCESS_TOKEN_EXPIRE_MINUTES`.
4. The client sends the token on protected calls using:

   ```http
   Authorization: Bearer <access-token>
   ```

5. `get_current_user` validates the signature and required `sub`/`exp` claims, parses the user ID, then loads the user from the database. Missing, invalid, expired tokens and missing/inactive user records are rejected with `401 Unauthorized`.

The frontend stores the access token in browser `sessionStorage` and removes it when signing out. The current application does not implement refresh tokens or a separate token-revocation list. Because protected calls re-read the user record, deleting or deactivating an account prevents its otherwise-unexpired token from authenticating.

## 5. Authorization flow for a tenant request

For a protected route such as `GET /tenants/{tenant_id}/documents`:

1. **Authenticate:** parse and validate the bearer token; resolve an active `User`.
2. **Authorize membership:** `require_tenant_member` checks `tenant_memberships` for that exact `user_id` and path `tenant_id`.
3. **Authorize role if required:** administrator-only endpoints use `require_tenant_admin`, which checks the same membership and requires `role == "admin"`.
4. **Scope the data operation:** the route/service query filters by the path tenant ID, and content-sensitive operations also filter by document status, ownership, or other applicable IDs.
5. **Return only authorized data:** response schemas expose public fields, not storage keys or password hashes.

Typical responses:

| Situation | Result |
|---|---|
| No token, invalid token, expired token, or inactive/missing user | `401 Unauthorized` |
| Authenticated user is not a member of the path tenant | `404 Not Found` with a generic tenant-access message |
| Tenant member calls an administrator-only operation | `403 Forbidden` |
| A document or conversation does not belong to the requested tenant/owner | `404 Not Found` |
| A request attempts to use an unsupported role | `422 Unprocessable Content` |
| Request has a valid role shape but the caller is not an admin | Rejected before membership is changed (`403`) |

The `404` for non-members is deliberate: it avoids confirming that another tenant exists.

### Resource-specific scopes

- **Documents and chunks:** shared with authorized members of their tenant. Retrieval searches only completed documents and applies tenant filtering in the database. The document/chunk composite foreign key also binds chunks to their document's tenant.
- **Search and Ask:** require tenant membership. Semantic search, keyword search, and hybrid search all receive the authorized tenant ID; optional document IDs narrow results but cannot override the tenant filter. Ask citations are derived only from that request's retrieved tenant results.
- **Conversations:** require both the path tenant and the authenticated owner. A member can list only their own conversations. Detail, message, and delete operations look up the conversation by conversation ID, tenant ID, and owner ID.
- **Member administration and retry:** require the `admin` role in the exact path tenant. Being an admin elsewhere does not satisfy this check.

## 6. End-to-end request lifecycle

```text
Email + password
      |
      v
POST /auth/register ----> normalized email + password hash in users
      |
      v
POST /auth/login -------> signed JWT (sub=user ID, exp=expiry)
      |
      v
Authorization: Bearer <JWT>
      |
      v
Validate token + load active user
      |
      v
Check tenant_memberships(user_id, path tenant_id)
      |
      +---- ordinary tenant member? ----> allow tenant-member operation
      |
      +---- admin-only operation? ------> require role=admin
      |
      v
Query/modify records scoped to that tenant
      |
      v
Return public response (never password hash/storage key)
```

### Example: new account to searchable document

1. Alice registers and signs in; she receives a JWT whose subject is Alice's user ID.
2. Alice creates `People Ops`. The API creates a `Tenant` and a `TenantMembership(Alice, People Ops, admin)` in the same creation flow.
3. Alice uploads `leave-policy.pdf`. The `Document` row stores the tenant ID, and the file is stored under a server-generated key.
4. A Celery worker extracts and chunks the text. Each `DocumentChunk` stores the same tenant ID and its document ID.
5. Once processing finishes, Alice or another authorized member can search or ask questions in `People Ops`.
6. A member's search request includes the tenant ID in its URL. The API first checks membership, then semantic and keyword retrieval filter chunks and documents by that tenant and completed status.

## 7. Demonstrating tenant isolation in the app

Use clearly different, harmless sentinel text in each tenant so the expected results are obvious. For example:

- Tenant A file contains: `ALPHA-ONLY-731 Redwood leave policy`.
- Tenant B file contains: `BRAVO-ONLY-946 Northstar travel policy`.

Do not use real confidential information for a demo.

### Demo A: two separate tenant accounts using the UI

This is the easiest demonstration of isolation using only the visible app:

1. Open the frontend in a private/incognito window or a separate browser profile.
2. Register **Account A**, create **Tenant A**, and upload a small text or PDF file containing only the Alpha sentinel.
3. Wait until the document status is `completed`. Search for `ALPHA-ONLY-731`; confirm the result contains the Alpha text. Ask a question that retrieves it and inspect the citation.
4. Sign out. Register **Account B**, create **Tenant B**, and upload a different file containing only the Bravo sentinel.
5. Search for `BRAVO-ONLY-946`; confirm the Bravo document appears.
6. Search Tenant B for `ALPHA-ONLY-731` and Tenant A's unique phrase; the results should be empty. Ask about the Alpha phrase; Tenant B should not receive Tenant A's content or citations.
7. Sign out and back in as Account A. Confirm the workspace selector lists only Tenant A, and the Alpha file is still available there.

Separate accounts created this way are each the admin of their own tenant. Since the current UI does not offer a member-management screen, use Demo B to show an outsider request and the admin/member distinction.

### Demo B: prove API enforcement with a second account

This demonstrates that changing a tenant ID in the URL does not bypass authorization. Use the actual IDs returned by the API; do not assume IDs.

1. Start the API and open `http://127.0.0.1:8000/docs`.
2. Create and sign in as Account A; create Tenant A and note its ID and returned access token.
3. Create and sign in as Account B; create Tenant B and upload the Bravo sentinel document. Note Tenant B's ID.
4. Sign back in as Account A and call Account A's document list using Tenant B's ID:

   ```sh
   curl -i \
     -H "Authorization: Bearer ACCOUNT_A_TOKEN" \
     "http://127.0.0.1:8000/tenants/TENANT_B_ID/documents"
   ```

   Expected result: `404` with a generic tenant-access message. Repeat with `POST /tenants/TENANT_B_ID/search` and `POST /tenants/TENANT_B_ID/ask`; both should be denied before returning Tenant B content.

5. As Account A, use Account B's document ID under Tenant A, for example:

   ```sh
   curl -i \
     -H "Authorization: Bearer ACCOUNT_A_TOKEN" \
     "http://127.0.0.1:8000/tenants/TENANT_A_ID/documents/TENANT_B_DOCUMENT_ID"
   ```

   Expected result: `404`. The same tenant-scoped lookup rule applies to deletion and processing.

For interactive testing, authorize with Account A's token in Swagger UI, then send a request using Tenant B's ID. Use a fresh Account B token when testing Tenant B's own successful access.

### Demo C: member vs administrator inside one tenant

This demonstrates that role checks are tenant-specific and enforced by the API:

1. Account A creates Tenant A and therefore becomes its admin.
2. Register Account B using the app or `POST /auth/register`.
3. While authenticated as Account A, add Account B to Tenant A using `POST /tenants/{tenant_id}/members` with Account B's email. Omit `role` or set it to `member`.
4. Sign in as Account B and verify that listing Tenant A's documents or searching Tenant A is allowed.
5. As Account B, call `GET /tenants/{tenant_id}/members`, `POST /tenants/{tenant_id}/documents/{document_id}/retry`, or another admin-only operation. Expected result: `403`.
6. As Account A, promote Account B using `PATCH /tenants/{tenant_id}/members/{user_id}` with `{"role":"admin"}`. Account B should now be allowed to use admin-only operations in Tenant A.
7. Try to make Account B an admin in Tenant B using a role in a normal document/search request. It will not change access; each membership role is stored and checked independently.

Example member-add request:

```sh
curl -i -X POST \
  "http://127.0.0.1:8000/tenants/TENANT_A_ID/members" \
  -H "Authorization: Bearer ACCOUNT_A_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"email":"account-b@example.com","role":"member"}'
```

Example role update:

```sh
curl -i -X PATCH \
  "http://127.0.0.1:8000/tenants/TENANT_A_ID/members/ACCOUNT_B_USER_ID" \
  -H "Authorization: Bearer ACCOUNT_A_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"role":"admin"}'
```

The target account must already be registered. Only a tenant admin can call these endpoints. The API prevents removal or demotion of the last remaining tenant administrator.

### What you should observe

| Test | Expected outcome |
|---|---|
| No token calls a protected endpoint | `401`; no tenant data returned |
| Account A calls a Tenant B endpoint without membership | `404`; no Tenant B data returned |
| Tenant A member lists/searches Tenant A documents | `200`; only Tenant A records/results |
| Tenant A member requests a document ID belonging to Tenant B under Tenant A | `404` |
| Tenant A member calls an admin-only route | `403` |
| Tenant A admin calls an admin-only route | Allowed if the operation's other validation passes |
| User tries an unsupported role such as `owner` | `422`; role constraint rejects it |
| Member sends `"role": "admin"` to an unrelated endpoint | Role has no effect; trusted membership row controls authorization |

## 8. Automated isolation coverage

The repository includes tests for complementary layers of the isolation model:

- `tests/tenants/test_tenant_isolation_end_to_end.py` checks tenant-scoped document listing, search and ask, wrong-tenant document access/deletion, and service-level processing lookup.
- `tests/retrieval/test_retrieval_tenant_isolation.py` checks semantic, keyword, and hybrid search do not return another tenant's chunks.
- `tests/tenants/test_tenant_memberships.py` and `tests/tenants/test_tenants.py` cover membership administration and tenant behavior.
- `tests/conversations/test_conversations_api.py` covers owner-private conversation access within a tenant.

Run the focused checks against the configured PostgreSQL/pgvector test database:

```sh
python -m pytest --no-cov -q \
  tests/tenants \
  tests/retrieval/test_retrieval_tenant_isolation.py \
  tests/conversations
```

## 9. Related documentation

- [README.md](./README.md) — installation, configuration, API overview, development, and operations.
- [OPERATIONS.md](./OPERATIONS.md) — health checks, security controls, retention, backup, and recovery.
- [DOCUMENTS.md](./DOCUMENTS.md) — document lifecycle, retry behavior, processing leases, and local file cleanup.
