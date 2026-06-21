"""Initial Professor AI schema.

Revision ID: 20260622_0001
Revises:
"""

from typing import Sequence, Union

from alembic import op

from app.core.database import Base
from app.core import models  # noqa: F401

revision: str = "20260622_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
