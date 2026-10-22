"""Classify historical inferred external members by the reported business action."""
from copy import deepcopy

from alembic import op
import sqlalchemy as sa

from migrations.group_classification_v2 import group_records
from migrations.schema_v1 import BusinessEvent

revision = "0002_customer_groups"
down_revision = "0001"
branch_labels = None
depends_on = None


def reclassify(connection):
    table = BusinessEvent.__table__
    after = 0
    while True:
        rows = connection.execute(sa.select(table).where(
            table.c.id > after, table.c.item_key == "group",
            table.c.event_type == "group_change_unknown"
        ).order_by(table.c.id).limit(200)).mappings().all()
        if not rows:
            break
        for row in rows:
            after = row["id"]
            if "_classification_original" in row["snapshot"]:
                continue
            # Keep the pre-upgrade row for a reversible data migration.
            original = {key: deepcopy(row[key]) for key in (
                "event_type", "quantity", "resolution", "enrichment_status", "snapshot")}
            records = group_records(row["snapshot"], row["quantity"], row["occurred_at"], row["group_id"])
            records[0]["snapshot"]["_classification_original"] = original
            connection.execute(sa.update(table).where(table.c.id == row["id"]).values(**records[0]))
            for record in records[1:]:
                # Defensive upsert keeps direct diagnostic re-runs idempotent.
                existing = connection.scalar(sa.select(table.c.id).where(
                    table.c.tenant_id == row["tenant_id"], table.c.callback_id == row["callback_id"],
                    table.c.item_key == record["item_key"]))
                if existing:
                    connection.execute(sa.update(table).where(table.c.id == existing).values(**record))
                else:
                    connection.execute(sa.insert(table).values(tenant_id=row["tenant_id"],
                        callback_id=row["callback_id"], created_at=row["created_at"], **record))


def upgrade():
    reclassify(op.get_bind())


def restore(connection):
    table = BusinessEvent.__table__
    after = 0
    while True:
        rows = connection.execute(sa.select(table).where(
            table.c.id > after, table.c.item_key == "group"
        ).order_by(table.c.id).limit(200)).mappings().all()
        if not rows:
            break
        for row in rows:
            after = row["id"]
            original = row["snapshot"].get("_classification_original")
            if not original:
                continue
            connection.execute(sa.delete(table).where(table.c.tenant_id == row["tenant_id"],
                table.c.callback_id == row["callback_id"], table.c.item_key == "group_other"))
            connection.execute(sa.update(table).where(table.c.id == row["id"]).values(**original))


def downgrade():
    restore(op.get_bind())
