"""fix running_result_error case

Revision ID: 77feb9b24000
Revises: 9f2c4a7b1d3e
Create Date: 2026-11-12 17:05:20.802874

历史迁移把评分规则名写成了 ``running_result_ERROR``（大写 ERROR），而
``RuleTypeEnum.RUNNING_RESULT_ERROR`` 的值已经是小写 ``running_result_error``。
``ck_rule_name_allowed`` 又只允许旧的大写写法，导致既不能直接改名、也不能
插入正确的小写规则。

本迁移：
1. 删除同专题下重复的旧行（如果已经存在小写行）；
2. 重建 rule 表，把旧行改名为小写；
3. 按当前 ``SYSTEM_RULE_NAMES`` 重建 ``ck_rule_name_allowed`` 约束。

SQLite 不支持直接修改 CHECK 约束，因此采用「新表 + 拷贝 + 改名」的方式重建。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from automaticgradingagentproject.core.rule_types import SYSTEM_RULE_NAMES


# revision identifiers, used by Alembic.
revision: str = '77feb9b24000'
down_revision: Union[str, Sequence[str], None] = '9f2c4a7b1d3e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


LEGACY_RULE_NAME = "running_result_ERROR"
FIXED_RULE_NAME = "running_result_error"


def _rule_name_check_sql(names: set[str] | frozenset[str]) -> str:
    return "name IN ({})".format(
        ", ".join(f"'{name}'" for name in sorted(names))
    )


def _rebuild_rule_table(rename_sql: str, allowed_names: frozenset[str]) -> None:
    """重建 rule 表；拷贝数据时按 `rename_sql` 转换 name。"""
    # 保证迁移中断后重试时不会因为残留表失败
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

    op.execute(
        sa.text(
            "INSERT INTO rule_new "
            "(id, name, description, weight, topic_id, created_at) "
            f"SELECT id, {rename_sql}, description, weight, topic_id, created_at "
            "FROM rule"
        )
    )

    op.drop_table("rule")
    op.rename_table("rule_new", "rule")
    op.create_index("ix_rule_topic_id", "rule", ["topic_id"], unique=False)


def upgrade() -> None:
    """把历史数据与约束统一为小写 running_result_error。"""
    # 若同专题同时存在两种写法，保留正确的小写行
    op.execute(
        sa.text(
            f"DELETE FROM rule WHERE name = '{LEGACY_RULE_NAME}' AND EXISTS ("
            "  SELECT 1 FROM rule AS fixed "
            "  WHERE fixed.topic_id = rule.topic_id "
            f"  AND fixed.name = '{FIXED_RULE_NAME}'"
            ")"
        )
    )

    _rebuild_rule_table(
        rename_sql=(
            f"CASE WHEN name = '{LEGACY_RULE_NAME}' "
            f"THEN '{FIXED_RULE_NAME}' ELSE name END"
        ),
        allowed_names=frozenset(SYSTEM_RULE_NAMES),
    )


def downgrade() -> None:
    """回滚到旧的大写写法（会重新引入大小写不一致的问题，仅用于回滚）。"""
    legacy_names = frozenset(
        (set(SYSTEM_RULE_NAMES) - {FIXED_RULE_NAME}) | {LEGACY_RULE_NAME}
    )

    _rebuild_rule_table(
        rename_sql=(
            f"CASE WHEN name = '{FIXED_RULE_NAME}' "
            f"THEN '{LEGACY_RULE_NAME}' ELSE name END"
        ),
        allowed_names=legacy_names,
    )
