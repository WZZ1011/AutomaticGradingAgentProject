"""add meaning_analysis scoring rule

Revision ID: 3c7d9e1a2b4f
Revises: 0645e6f03f4e
Create Date: 2026-11-14 00:00:00.000000

新增文档语义校验作为第 7 个顶层评分规则。SQLite 不能直接修改 CHECK 约束，
因此用「新表 + 拷贝 + 改名」的方式重建 rule 表，并更新 ck_rule_name_allowed。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from automaticgradingagentproject.core.enums.rule_type_enum import RuleTypeEnum
from automaticgradingagentproject.core.rule_types import SYSTEM_RULE_NAMES


# revision identifiers, used by Alembic.
revision: str = '3c7d9e1a2b4f'
down_revision: Union[str, Sequence[str], None] = '0645e6f03f4e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MEANING_RULE_VALUE = RuleTypeEnum.MEANING_ANALYSIS.value
MEANING_RULE_NAMES = frozenset(
    {RuleTypeEnum.MEANING_ANALYSIS.name, MEANING_RULE_VALUE}
)


def _rule_name_check_sql(names: set[str] | frozenset[str]) -> str:
    return "name IN ({})".format(
        ", ".join(f"'{name}'" for name in sorted(names))
    )


def _rebuild_rule_table(allowed_names: frozenset[str],
                        skip_names: frozenset[str] = frozenset()) -> None:
    """重建 rule 表；可按 skip_names 排除要删除的行。"""
    op.execute(sa.text("DROP TABLE IF EXISTS rule_new"))

    op.create_table(
        "rule_new",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("topic_id", "name", name="uq_rule_topic_name"),
        sa.CheckConstraint(
            "weight >= 0 AND weight <= 100",
            name="ck_rule_weight_range",
        ),
        sa.CheckConstraint(
            _rule_name_check_sql(allowed_names),
            name="ck_rule_name_allowed",
        ),
    )

    where_sql = ""
    if skip_names:
        quoted = ", ".join(f"'{name}'" for name in sorted(skip_names))
        where_sql = f" WHERE name NOT IN ({quoted})"

    op.execute(
        sa.text(
            "INSERT INTO rule_new "
            "(id, name, description, weight, topic_id, created_at) "
            "SELECT id, name, description, weight, topic_id, created_at "
            f"FROM rule{where_sql}"
        )
    )

    op.drop_table("rule")
    op.rename_table("rule_new", "rule")
    op.create_index("ix_rule_topic_id", "rule", ["topic_id"], unique=False)


def upgrade() -> None:
    """为每个专题补一条语义评分行，并允许新的规则名。

    必须先重建 CHECK 约束，再插入新规则；否则旧约束会拒绝
    meaning_analysis，INSERT OR IGNORE 会把它静默丢弃。
    """
    _rebuild_rule_table(allowed_names=frozenset(SYSTEM_RULE_NAMES))

    conn = op.get_bind()
    topic_ids = [
        row[0] for row in conn.execute(sa.text("SELECT id FROM topic")).all()
    ]
    for topic_id in topic_ids:
        conn.execute(
            sa.text(
                "INSERT OR IGNORE INTO rule "
                "(name, description, weight, topic_id) "
                "VALUES (:name, :description, 0, :topic_id)"
            ),
            {
                "name": MEANING_RULE_VALUE,
                "description": "报告文本的逻辑自洽性，以及与 AST/沙箱反馈的一致性",
                "topic_id": topic_id,
            },
        )


def downgrade() -> None:
    """删除语义评分行，并恢复不含 meaning_analysis 的约束。"""
    legacy_names = frozenset(set(SYSTEM_RULE_NAMES) - MEANING_RULE_NAMES)
    _rebuild_rule_table(
        allowed_names=legacy_names,
        skip_names=MEANING_RULE_NAMES,
    )
