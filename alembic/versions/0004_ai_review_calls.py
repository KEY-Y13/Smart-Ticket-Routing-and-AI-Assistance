"""Keep data-review calls in the SAME paid budget ledger; preserve existing draft records."""

import sqlalchemy as sa

from alembic import op

revision = "0004_ai_review_calls"
down_revision = "0003_reviews"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("ai_invocations") as batch:
        batch.add_column(
            sa.Column("purpose", sa.String(24), nullable=False, server_default="DRAFT")
        )
        batch.add_column(sa.Column("data_ref", sa.String(64), nullable=True))
        batch.add_column(sa.Column("review_result", sa.JSON(), nullable=True))
        batch.alter_column(
            "ticket_id",
            existing_type=sa.Integer().with_variant(sa.BigInteger(), "mysql"),
            nullable=True,
        )
        batch.create_unique_constraint(op.f("uq_ai_invocations_purpose"), ["purpose", "data_ref"])
        batch.create_check_constraint(
            op.f("ck_ai_invocations_call_reference"),
            "(purpose = 'DRAFT' AND ticket_id IS NOT NULL AND data_ref IS NULL) OR "
            "(purpose = 'DATA_REVIEW' AND ticket_id IS NULL AND data_ref IS NOT NULL "
            "AND length(data_ref)=64)",
        )


def downgrade():
    raise RuntimeError("No automatic destructive downgrade; preserve paid review evidence")
