from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.routes import conversations as conversations_route
from app.core.llm_dependencies import get_llm_provider
from app.core.security import create_access_token, hash_password
from app.db.session import get_db
from app.main import app
from app.models.conversation import Conversation, ConversationMessage
from app.models.tenant_membership import TenantMembership
from app.models.tenants import Tenant
from app.models.users import User


class FakeProvider:
    def __init__(self):
        self.prompts = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if "Standalone retrieval query:" in prompt:
            return "Annual leave policy for contractors"
        return "The policy applies to contractors [Source 1]."


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
    tenant = Tenant(name=f"conversations-{uuid4().hex}")
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
    return {
        "Authorization": f"Bearer {create_access_token(user.id)}",
    }


def test_conversations_are_private_and_follow_up_retrieval_is_bounded(
    client,
    db_session,
    monkeypatch,
):
    owner = create_user(db_session, "owner")
    other_member = create_user(db_session, "other")
    tenant = create_tenant(db_session, owner)
    db_session.add(
        TenantMembership(
            user_id=other_member.id,
            tenant_id=tenant.id,
            role="member",
        )
    )
    db_session.flush()
    provider = FakeProvider()
    app.dependency_overrides[get_llm_provider] = lambda: provider
    seen_queries = []

    def fake_search(**kwargs):
        seen_queries.append(kwargs["query"])
        return []

    monkeypatch.setattr(
        conversations_route,
        "hybrid_search",
        fake_search,
    )

    created = client.post(
        f"/tenants/{tenant.id}/conversations",
        headers=auth_headers(owner),
        json={},
    )
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    listed = client.get(
        f"/tenants/{tenant.id}/conversations",
        headers=auth_headers(owner),
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["conversations"][0]["id"] == conversation_id
    assert listed.json()["conversations"][0]["title"] == "New conversation"

    first = client.post(
        f"/tenants/{tenant.id}/conversations/{conversation_id}/messages",
        headers=auth_headers(owner),
        json={"question": "What is the annual leave policy?"},
    )
    assert first.status_code == 200
    assert first.json()["answer"] == (
        "I couldn't find relevant information in the available documents."
    )
    assert seen_queries == ["What is the annual leave policy?"]
    assert provider.prompts == []

    second = client.post(
        f"/tenants/{tenant.id}/conversations/{conversation_id}/messages",
        headers=auth_headers(owner),
        json={"question": "Does it apply to contractors?"},
    )
    assert second.status_code == 200
    assert seen_queries[-1] == "Annual leave policy for contractors"
    assert provider.prompts

    detail = client.get(
        f"/tenants/{tenant.id}/conversations/{conversation_id}",
        headers=auth_headers(owner),
    )
    assert detail.status_code == 200
    assert [message["role"] for message in detail.json()["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]

    member_headers = auth_headers(other_member)
    assert client.get(
        f"/tenants/{tenant.id}/conversations",
        headers=member_headers,
    ).json()["total"] == 0
    assert client.get(
        f"/tenants/{tenant.id}/conversations/{conversation_id}",
        headers=member_headers,
    ).status_code == 404
    assert client.delete(
        f"/tenants/{tenant.id}/conversations/{conversation_id}",
        headers=member_headers,
    ).status_code == 404


def test_conversation_messages_reject_an_active_request(
    client,
    db_session,
):
    owner = create_user(db_session, "busy-owner")
    tenant = create_tenant(db_session, owner)
    conversation = Conversation(
        tenant_id=tenant.id,
        owner_id=owner.id,
        title="Busy",
        active_request_id=uuid4(),
        active_request_started_at=datetime.now(timezone.utc),
    )
    db_session.add(conversation)
    db_session.flush()

    response = client.post(
        f"/tenants/{tenant.id}/conversations/{conversation.id}/messages",
        headers=auth_headers(owner),
        json={"question": "A follow-up question?"},
    )

    assert response.status_code == 409
    assert response.json()["detail"].startswith("A message is already")


def test_deleting_conversation_cascades_messages(client, db_session):
    owner = create_user(db_session, "delete-owner")
    tenant = create_tenant(db_session, owner)
    conversation = Conversation(
        tenant_id=tenant.id,
        owner_id=owner.id,
        title="Delete me",
    )
    db_session.add(conversation)
    db_session.flush()
    message = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content="Remove this content",
        citations=[],
    )
    db_session.add(message)
    db_session.flush()
    conversation_id = conversation.id
    message_id = message.id

    response = client.delete(
        f"/tenants/{tenant.id}/conversations/{conversation_id}",
        headers=auth_headers(owner),
    )

    assert response.status_code == 204
    assert db_session.scalar(
        select(Conversation.id).where(Conversation.id == conversation_id)
    ) is None
    assert db_session.scalar(
        select(ConversationMessage.id).where(
            ConversationMessage.id == message_id
        )
    ) is None


def test_non_member_cannot_create_conversation(client, db_session):
    owner = create_user(db_session, "tenant-owner")
    outsider = create_user(db_session, "tenant-outsider")
    tenant = create_tenant(db_session, owner)

    response = client.post(
        f"/tenants/{tenant.id}/conversations",
        headers=auth_headers(outsider),
        json={},
    )

    assert response.status_code == 404
