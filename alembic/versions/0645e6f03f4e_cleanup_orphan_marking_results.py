"""cleanup orphan marking results

Revision ID: 0645e6f03f4e
Revises: 77feb9b24000
Create Date: 2026-11-13 18:14:16.591433

llm_comment 没有外键约束，历史上删除文档/源码/专题后不会清理对应的批阅结果。
本迁移删除关联作业已经不存在的孤儿记录；今后的删除由服务层显式清理。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0645e6f03f4e'
down_revision: Union[str, Sequence[str], None] = '77feb9b24000'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """删除关联文档、源码或专题已不存在的批阅结果。"""
    op.execute(
        sa.text(
            "DELETE FROM llm_comment "
            "WHERE document_id NOT IN (SELECT id FROM document) "
            "   OR code_id NOT IN (SELECT id FROM source_code) "
            "   OR topic_id NOT IN (SELECT id FROM topic)"
        )
    )


def downgrade() -> None:
    """已删除的批阅结果无法恢复。"""
    pass
