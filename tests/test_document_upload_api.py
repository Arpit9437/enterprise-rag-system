
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token, hash_password
from app.db.session import get_db
from app.main import app
from app.models.document import Document
from app.models.tenants import Tenant
from app.models.tenant_membership import TenantMembership
from app.models.users import User


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def storage_dir(tmp_path, monkeypatch):
    from app.services import document_storage

    monkeypatch.setattr(
        document_storage.settings,
        "document_storage_dir",
        tmp_path,
    )
    monkeypatch.setattr(
        document_storage.settings,
        "max_document_size_bytes",
        10 * 1024 * 1024,
    )
    return tmp_path


def create_user(db_session):
    user = User(
        email=f"{uuid.uuid4()}@example.com",
        password_hash=hash_password("TestPassword123!"),
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def create_tenant(db_session, owner):
    tenant = Tenant(name=f"tenant-{uuid.uuid4()}")
    db_session.add(tenant)
    db_session.flush()

    db_session.add(
        TenantMembership(
            user_id=owner.id,
            tenant_id=tenant.id,
            role="admin",
        )
    )
    db_session.flush()
    return tenant


def auth_headers(user):
    return {
        "Authorization": f"Bearer {create_access_token(user.id)}"
    }


def test_member_can_upload_pdf(client, db_session, storage_dir):
    user = create_user(db_session)
    tenant = create_tenant(db_session, user)

    response = client.post(
        f"/tenants/{tenant.id}/documents/upload",
        headers=auth_headers(user),
        files={
            "file": (
                "handbook.pdf",
                b"%PDF-1.7\nexample content",
                "application/pdf",
            )
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()

    assert body["filename"] == "handbook.pdf"
    assert body["tenant_id"] == str(tenant.id)
    assert body["status"] == "pending"
    assert body["content_type"] == "application/pdf"

    stored = storage_dir / body["storage_key"]
    assert stored.exists()
    assert stored.read_bytes().startswith(b"%PDF-")


def test_member_can_upload_text_file(client, db_session, storage_dir):
    user = create_user(db_session)
    tenant = create_tenant(db_session, user)

    response = client.post(
        f"/tenants/{tenant.id}/documents/upload",
        headers=auth_headers(user),
        files={
            "file": (
                "notes.txt",
                b"Internal knowledge base notes",
                "text/plain",
            )
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()

    assert body["filename"] == "notes.txt"
    assert (storage_dir / body["storage_key"]).read_bytes() == (
        b"Internal knowledge base notes"
    )


def test_non_member_cannot_upload(client, db_session, storage_dir):
    owner = create_user(db_session)
    outsider = create_user(db_session)
    tenant = create_tenant(db_session, owner)

    response = client.post(
        f"/tenants/{tenant.id}/documents/upload",
        headers=auth_headers(outsider),
        files={
            "file": (
                "private.txt",
                b"secret",
                "text/plain",
            )
        },
    )

    assert response.status_code == 404
    assert list(storage_dir.iterdir()) == []


def test_upload_requires_authentication(client, db_session, storage_dir):
    owner = create_user(db_session)
    tenant = create_tenant(db_session, owner)

    response = client.post(
        f"/tenants/{tenant.id}/documents/upload",
        files={
            "file": (
                "notes.txt",
                b"hello",
                "text/plain",
            )
        },
    )

    assert response.status_code == 401
    assert list(storage_dir.iterdir()) == []


def test_invalid_pdf_is_rejected(client, db_session, storage_dir):
    user = create_user(db_session)
    tenant = create_tenant(db_session, user)

    response = client.post(
        f"/tenants/{tenant.id}/documents/upload",
        headers=auth_headers(user),
        files={
            "file": (
                "broken.pdf",
                b"not actually a PDF",
                "application/pdf",
            )
        },
    )

    assert response.status_code == 422
    assert list(storage_dir.iterdir()) == []


def test_unsupported_extension_is_rejected(
    client,
    db_session,
    storage_dir,
):
    user = create_user(db_session)
    tenant = create_tenant(db_session, user)

    response = client.post(
        f"/tenants/{tenant.id}/documents/upload",
        headers=auth_headers(user),
        files={
            "file": (
                "script.py",
                b"print('hello')",
                "text/plain",
            )
        },
    )

    assert response.status_code == 415
    assert list(storage_dir.iterdir()) == []


def test_database_failure_removes_stored_file(
    client,
    db_session,
    storage_dir,
    monkeypatch,
):
    user = create_user(db_session)
    tenant = create_tenant(db_session, user)

    def fail_commit():
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(db_session, "commit", fail_commit)

    response = client.post(
        f"/tenants/{tenant.id}/documents/upload",
        headers=auth_headers(user),
        files={
            "file": (
                "notes.txt",
                b"temporary content",
                "text/plain",
            )
        },
    )

    assert response.status_code == 500
    assert list(storage_dir.iterdir()) == []
