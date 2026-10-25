from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

import app.core.tenant_dependencies as tenant_dependencies


@pytest.fixture
def tenant_id():
    return uuid4()


@pytest.fixture
def current_user():
    return SimpleNamespace(id=uuid4())


def test_tenant_member_is_allowed(
    db_session,
    tenant_id,
    current_user,
    monkeypatch,
):
    monkeypatch.setattr(
        tenant_dependencies,
        "has_tenant_access",
        lambda **kwargs: True,
    )

    result = tenant_dependencies.require_tenant_member(
        tenant_id=tenant_id,
        current_user=current_user,
        db=db_session,
    )

    assert result is current_user


def test_non_member_is_denied(
    db_session,
    tenant_id,
    current_user,
    monkeypatch,
):
    monkeypatch.setattr(
        tenant_dependencies,
        "has_tenant_access",
        lambda **kwargs: False,
    )

    with pytest.raises(HTTPException) as exc_info:
        tenant_dependencies.require_tenant_member(
            tenant_id=tenant_id,
            current_user=current_user,
            db=db_session,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Tenant not found or access denied"


def test_tenant_admin_is_allowed(
    db_session,
    tenant_id,
    current_user,
    monkeypatch,
):
    monkeypatch.setattr(
        tenant_dependencies,
        "has_tenant_access",
        lambda **kwargs: True,
    )
    monkeypatch.setattr(
        tenant_dependencies,
        "has_tenant_role",
        lambda **kwargs: True,
    )

    result = tenant_dependencies.require_tenant_admin(
        tenant_id=tenant_id,
        current_user=current_user,
        db=db_session,
    )

    assert result is current_user


def test_regular_member_cannot_perform_admin_action(
    db_session,
    tenant_id,
    current_user,
    monkeypatch,
):
    monkeypatch.setattr(
        tenant_dependencies,
        "has_tenant_access",
        lambda **kwargs: True,
    )
    monkeypatch.setattr(
        tenant_dependencies,
        "has_tenant_role",
        lambda **kwargs: False,
    )

    with pytest.raises(HTTPException) as exc_info:
        tenant_dependencies.require_tenant_admin(
            tenant_id=tenant_id,
            current_user=current_user,
            db=db_session,
        )

    assert exc_info.value.status_code == 403
    assert (
        exc_info.value.detail
        == "Tenant administrator privileges required"
    )


def test_non_member_cannot_perform_admin_action(
    db_session,
    tenant_id,
    current_user,
    monkeypatch,
):
    monkeypatch.setattr(
        tenant_dependencies,
        "has_tenant_access",
        lambda **kwargs: False,
    )

    def unexpected_role_check(**kwargs):
        pytest.fail("Role check must not run for a non-member")

    monkeypatch.setattr(
        tenant_dependencies,
        "has_tenant_role",
        unexpected_role_check,
    )

    with pytest.raises(HTTPException) as exc_info:
        tenant_dependencies.require_tenant_admin(
            tenant_id=tenant_id,
            current_user=current_user,
            db=db_session,
        )

    assert exc_info.value.status_code == 404


def test_admin_check_requires_admin_role(
    db_session,
    tenant_id,
    current_user,
    monkeypatch,
):
    captured = {}

    monkeypatch.setattr(
        tenant_dependencies,
        "has_tenant_access",
        lambda **kwargs: True,
    )

    def fake_has_tenant_role(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(
        tenant_dependencies,
        "has_tenant_role",
        fake_has_tenant_role,
    )

    tenant_dependencies.require_tenant_admin(
        tenant_id=tenant_id,
        current_user=current_user,
        db=db_session,
    )

    assert captured["user_id"] == current_user.id
    assert captured["tenant_id"] == tenant_id
    assert captured["required_role"] == "admin"
