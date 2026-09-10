"""Add professor email verification records.

Revision ID: 20260806_0002
Revises: 20260622_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_0002"
down_revision: str | Sequence[str] | None = "20260622_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The original baseline revision used live model metadata. A database created
    # from a newer checkout can therefore already contain this table.
    if sa.inspect(op.get_bind()).has_table("email_verifications"):
        return
    op.create_table(
        "email_verifications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_email_verifications_email"), "email_verifications", ["email"], unique=True)
    op.create_index(op.f("ix_email_verifications_status"), "email_verifications", ["status"], unique=False)


def downgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("email_verifications"):
        return
    op.drop_index(op.f("ix_email_verifications_status"), table_name="email_verifications")
    op.drop_index(op.f("ix_email_verifications_email"), table_name="email_verifications")
    op.drop_table("email_verifications")
