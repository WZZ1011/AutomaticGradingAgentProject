"""add submission file names

Revision ID: ed6755b0c5b4
Revises: 3c7d9e1a2b4f
Create Date: 2026-11-17 09:32:55.700166

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ed6755b0c5b4'
down_revision: Union[str, Sequence[str], None] = '3c7d9e1a2b4f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # SQLite 原生支持 ADD COLUMN，不需要 batch 重建表。
    op.add_column("document", sa.Column("file_name", sa.String(length=255), nullable=True))
    op.add_column("source_code", sa.Column("file_name", sa.String(length=255), nullable=True))
    # 历史行的原始文件名已在上传时丢弃，保留 NULL，由读取层回退到磁盘 basename。


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("source_code", schema=None) as batch_op:
        batch_op.drop_column("file_name")
    with op.batch_alter_table("document", schema=None) as batch_op:
        batch_op.drop_column("file_name")
