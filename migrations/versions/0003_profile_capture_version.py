"""Order group captures independently of their completed snapshot timestamps."""
from alembic import op
import sqlalchemy as sa

revision = "0003_profile_capture_version"
down_revision = "0002_customer_groups"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("profiles", sa.Column("capture_version", sa.Integer(), nullable=False,
                                       server_default="0"))


def downgrade():
    with op.batch_alter_table("profiles") as batch:
        batch.drop_column("capture_version")
