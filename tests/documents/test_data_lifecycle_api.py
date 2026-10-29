from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.routes import tenants as tenants_route
from app.core.security import create_access_token, hash_password
from app.db.session import get_db
from app.main import app
from app.models.conversation import Conversation, ConversationMessage
from app.models.document import Document, DocumentStatus
from app.models.tenant_membership import TenantMembership
from app.models.tenants import Tenant
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


def create_user(db_session, name: str) -> User:
    user = User(
        email=f"{name}-{uuid4().hex}@example.com",
        password_hash=hash_password("TestPassword123!"),
    )
    db_session.add(user)
    db_session.flush()
    return user


def create_tenant(db_session, owner: User) -> Tenant:
    tenant = Tenant(name=f"lifecycle-{uuid4().hex}")
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


def auth_headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def test_tenant_deletion_cascades_database_data_and_cleans_files(
    client,
    db_session,
    monkeypatch,
):
    owner = create_user(db_session, "tenant-admin")
    tenant = create_tenant(db_session, owner)
    document = Document(
        tenant_id=tenant.id,
        filename="policy.txt",
        storage_key=f"{uuid4().hex}.txt",
        content_type="text/plain",
        status=DocumentStatus.PENDING,
    )
    conversation = Conversation(
        tenant_id=tenant.id,
        owner_id=owner.id,
        title="Private conversation",
    )
    db_session.add_all([document, conversation])
    db_session.flush()
    message = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content="Question",
        citations=[],
    )
    db_session.add(message)
    db_session.flush()
    stored_key = document.storage_key
    tenant_id = tenant.id
    document_id = document.id
    conversation_id = conversation.id
    message_id = message.id
    deleted_files = []
    monkeypatch.setattr(
        tenants_route,
        "delete_stored_file",
        deleted_files.append,
    )

    response = client.delete(
        f"/tenants/{tenant_id}",
        headers=auth_headers(owner),
    )

    assert response.status_code == 204
    assert deleted_files == [stored_key]
    db_session.expire_all()
    assert db_session.scalar(
        select(Tenant.id).where(Tenant.id == tenant_id)
    ) is None
    assert db_session.scalar(
        select(Document.id).where(Document.id == document_id)
    ) is None
    assert db_session.scalar(
        select(Conversation.id).where(Conversation.id == conversation_id)
    ) is None
    assert db_session.scalar(
        select(ConversationMessage.id).where(
            ConversationMessage.id == message_id
        )
    ) is None


def test_user_cannot_delete_account_while_last_tenant_admin(
    client,
    db_session,
):
    owner = create_user(db_session, "last-admin")
    tenant = create_tenant(db_session, owner)
    owner_id = owner.id
    tenant_id = tenant.id

    response = client.delete("/auth/me", headers=auth_headers(owner))

    assert response.status_code == 409
    assert db_session.scalar(
        select(User.id).where(User.id == owner_id)
    ) is not None
    assert db_session.scalar(
        select(Tenant.id).where(Tenant.id == tenant_id)
    ) is not None


def test_user_deletion_cascades_owned_conversations(
    client,
    db_session,
):
    owner = create_user(db_session, "account-owner")
    peer_admin = create_user(db_session, "peer-admin")
    tenant = create_tenant(db_session, owner)
    db_session.add(
        TenantMembership(
            user_id=peer_admin.id,
            tenant_id=tenant.id,
            role="admin",
        )
    )
    conversation = Conversation(
        tenant_id=tenant.id,
        owner_id=owner.id,
        title="Owned history",
    )
    db_session.add(conversation)
    db_session.flush()
    message = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content="Private question",
        citations=[],
    )
    db_session.add(message)
    db_session.flush()
    owner_id = owner.id
    tenant_id = tenant.id
    conversation_id = conversation.id
    message_id = message.id

    response = client.delete("/auth/me", headers=auth_headers(owner))

    assert response.status_code == 204
    db_session.expire_all()
    assert db_session.scalar(
        select(User.id).where(User.id == owner_id)
    ) is None
    assert db_session.scalar(
        select(Conversation.id).where(Conversation.id == conversation_id)
    ) is None
    assert db_session.scalar(
        select(ConversationMessage.id).where(
            ConversationMessage.id == message_id
        )
    ) is None
    assert db_session.scalar(
        select(Tenant.id).where(Tenant.id == tenant_id)
    ) is not None
