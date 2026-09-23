"""activity_log batch_id

Revision ID: b7c1d2e3f4a5
Revises: 9fafd2ed0549
Create Date: 2026-09-23 14:40:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b7c1d2e3f4a5'
down_revision: Union[str, Sequence[str], None] = '9fafd2ed0549'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('activity_log') as batch_op:
        batch_op.add_column(sa.Column('batch_id', sa.String(length=32), nullable=True))
        batch_op.create_index('ix_activity_log_batch_id', ['batch_id'])


def downgrade() -> None:
    with op.batch_alter_table('activity_log') as batch_op:
        batch_op.drop_index('ix_activity_log_batch_id')
        batch_op.drop_column('batch_id')
