"""Initial schema. This revision uses a frozen copy of the initial model metadata."""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    from migrations.schema_v1 import Base

    Base.metadata.create_all(bind=op.get_bind())


def downgrade():
    from migrations.schema_v1 import Base

    Base.metadata.drop_all(bind=op.get_bind())
