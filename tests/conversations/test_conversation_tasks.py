from unittest.mock import MagicMock, patch

import pytest

from app.core.celery_app import celery_app
from app.tasks.conversation_tasks import delete_expired_conversations


def test_delete_expired_conversations_commits_and_returns_count():
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    session.execute.return_value.rowcount = 3

    with patch(
        "app.tasks.conversation_tasks.LocalSession",
        return_value=session,
    ):
        result = delete_expired_conversations.run()

    assert result == 3
    assert session.commit.call_count == 1
    assert session.rollback.call_count == 0


def test_delete_expired_conversations_rolls_back_and_propagates():
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    session.execute.side_effect = RuntimeError("database unavailable")

    with (
        patch(
            "app.tasks.conversation_tasks.LocalSession",
            return_value=session,
        ),
        pytest.raises(RuntimeError, match="database unavailable"),
    ):
        delete_expired_conversations.run()

    session.rollback.assert_called_once()


def test_beat_schedules_conversation_retention():
    schedule = celery_app.conf.beat_schedule[
        "delete-expired-conversations-daily"
    ]

    assert schedule["task"] == "conversations.delete_expired"
    assert schedule["schedule"] == 86400.0
