"""constrain tenant membership roles

Revision ID: b6288e348cb6
Revises: be8dcb622f64
Create Date: 2026-10-24 10:14:44.593993

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6288e348cb6'
down_revision: Union[str, Sequence[str], None] = 'be8dcb622f64'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_tenant_memberships_role",
        "tenant_memberships",
        "role IN ('admin', 'member')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_tenant_memberships_role",
        "tenant_memberships",
        type_="check",
    )