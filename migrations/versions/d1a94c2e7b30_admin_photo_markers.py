"""Associate hand-placed admin markers with their source photo."""
from alembic import op
import sqlalchemy as sa

revision = "d1a94c2e7b30"
down_revision = "cf2974df6541"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("nodes") as batch_op:
        batch_op.add_column(sa.Column("marker_photo_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_nodes_marker_photo_id_photos",
            "photos",
            ["marker_photo_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_nodes_marker_photo_id", ["marker_photo_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("nodes") as batch_op:
        batch_op.drop_index("ix_nodes_marker_photo_id")
        batch_op.drop_constraint("fk_nodes_marker_photo_id_photos", type_="foreignkey")
        batch_op.drop_column("marker_photo_id")
