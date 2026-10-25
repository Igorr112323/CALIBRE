"""Allow explicitly selected internal photos to sync over LAN.

Revision ID: cf2974df6541
Revises: ae0e4a1b7c31
Create Date: 2026-10-25
"""
from alembic import op
import sqlalchemy as sa

revision = "cf2974df6541"
down_revision = "ae0e4a1b7c31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("photos") as batch:
        batch.add_column(sa.Column("sync_to_phones", sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("photos") as batch:
        batch.drop_column("sync_to_phones")
