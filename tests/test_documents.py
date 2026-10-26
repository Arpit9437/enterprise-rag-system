
import uuid

from sqlalchemy import select

from app.models.document import Document, DocumentStatus
from app.models.tenants import Tenant


def create_tenant(db_session):
    tenant = Tenant(name=f"tenant-{uuid.uuid4()}")
    db_session.add(tenant)
    db_session.flush()
    return tenant


def test_document_can_be_created_for_tenant(db_session):
    tenant = create_tenant(db_session)

    document = Document(
        tenant_id=tenant.id,
        filename="employee-handbook.pdf",
    )
    db_session.add(document)
    db_session.flush()

    saved = db_session.scalar(
        select(Document).where(Document.id == document.id)
    )

    assert saved is not None
    assert saved.tenant_id == tenant.id
    assert saved.filename == "employee-handbook.pdf"


def test_document_defaults_to_pending_status(db_session):
    tenant = create_tenant(db_session)

    document = Document(
        tenant_id=tenant.id,
        filename="policy.pdf",
    )
    db_session.add(document)
    db_session.flush()

    assert document.status == DocumentStatus.PENDING


def test_document_has_creation_timestamp(db_session):
    tenant = create_tenant(db_session)

    document = Document(
        tenant_id=tenant.id,
        filename="guide.pdf",
    )
    db_session.add(document)
    db_session.flush()

    assert document.created_at is not None


def test_document_status_can_be_updated(db_session):
    tenant = create_tenant(db_session)

    document = Document(
        tenant_id=tenant.id,
        filename="report.pdf",
    )
    db_session.add(document)
    db_session.flush()

    document.status = DocumentStatus.PROCESSING
    db_session.flush()
    db_session.refresh(document)

    assert document.status == DocumentStatus.PROCESSING
