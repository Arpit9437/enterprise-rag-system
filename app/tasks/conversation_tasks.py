import logging
from datetime import timedelta

from sqlalchemy import delete, func

from app.core.celery_app import celery_app
from app.core.config import settings
from app.db.session import LocalSession
from app.models.conversation import Conversation

logger = logging.getLogger(__name__)


@celery_app.task(name="conversations.delete_expired")
def delete_expired_conversations() -> int:
    """Delete conversations inactive beyond the configured retention period."""
    with LocalSession() as db:
        try:
            result = db.execute(
                delete(Conversation).where(
                    Conversation.updated_at
                    < func.now()
                    - timedelta(days=settings.conversation_retention_days)
                )
            )
            db.commit()
            return result.rowcount or 0
        except Exception:
            db.rollback()
            logger.exception("Could not delete expired conversations")
            raise


celery_app.conf.beat_schedule = {
    **celery_app.conf.beat_schedule,
    "delete-expired-conversations-daily": {
        "task": "conversations.delete_expired",
        "schedule": 86400.0,
    },
}
