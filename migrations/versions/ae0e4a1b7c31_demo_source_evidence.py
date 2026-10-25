"""Store names and evidence for the GRN-471 demo slice.

Revision ID: ae0e4a1b7c31
Revises: f3fab3211427
Create Date: 2026-10-25
"""
from alembic import op
import sqlalchemy as sa

revision = "ae0e4a1b7c31"
down_revision = "f3fab3211427"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("machine_characteristics") as batch:
        batch.add_column(sa.Column("auto_verified", sa.Boolean(), server_default=sa.false(), nullable=False))
        batch.add_column(sa.Column("human_verification_required", sa.Boolean(), server_default=sa.false(), nullable=False))
    with op.batch_alter_table("nodes") as batch:
        batch.add_column(sa.Column("source_page", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("source_quote", sa.Text(), nullable=True))
    with op.batch_alter_table("norms") as batch:
        batch.add_column(sa.Column("name", sa.String(length=300), server_default="", nullable=False))
        batch.add_column(sa.Column("condition", sa.Text(), server_default="", nullable=False))
        batch.add_column(sa.Column("verification_note", sa.Text(), server_default="", nullable=False))
        batch.drop_constraint("ck_norm_review_status", type_="check")
        batch.drop_constraint("ck_norm_source_kind", type_="check")
        batch.drop_constraint("ck_norm_has_source", type_="check")
        batch.create_check_constraint(
            "ck_norm_review_status",
            "review_status IN ('unverified', 'auto_verified', 'verified', 'rejected', 'forced_accept')",
        )
        batch.create_check_constraint(
            "ck_norm_source_kind",
            "source_kind IN ('document', 'department_measurement', 'packaged_demo')",
        )
        batch.create_check_constraint(
            "ck_norm_has_source",
            "(source_kind = 'document' AND source_document_id IS NOT NULL AND source_page_start IS NOT NULL AND source_page_start >= 1 AND length(trim(source_quote)) > 0) OR "
            "(source_kind = 'department_measurement' AND length(trim(measured_by)) > 0 AND measured_at IS NOT NULL) OR "
            "(source_kind = 'packaged_demo' AND source_document_id IS NULL AND source_page_start IS NOT NULL AND source_page_start >= 1 AND length(trim(source_quote)) > 0)",
        )
    with op.batch_alter_table("problems") as batch:
        batch.add_column(sa.Column("source_page", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("source_quote", sa.Text(), nullable=True))
    with op.batch_alter_table("problem_steps") as batch:
        batch.add_column(sa.Column("source_page", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("source_quote", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("problem_steps") as batch:
        batch.drop_column("source_quote")
        batch.drop_column("source_page")
    with op.batch_alter_table("problems") as batch:
        batch.drop_column("source_quote")
        batch.drop_column("source_page")
    with op.batch_alter_table("norms") as batch:
        batch.drop_column("verification_note")
        batch.drop_column("condition")
        batch.drop_column("name")
    with op.batch_alter_table("nodes") as batch:
        batch.drop_column("source_quote")
        batch.drop_column("source_page")
    with op.batch_alter_table("machine_characteristics") as batch:
        batch.drop_column("human_verification_required")
        batch.drop_column("auto_verified")
