"""rule constraints and built-in topic seed

Revision ID: 9f2c4a7b1d3e
Revises: 5b53cb96e6f7
Create Date: 2026-11-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9f2c4a7b1d3e"
down_revision: Union[str, Sequence[str], None] = "5b53cb96e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 教师可配置的 6 个顶层评分类型：值 -> 默认说明。
SCORING_RULES: dict[str, str] = {
    "format_error": "文档格式不符合要求（行距、页码、文件命名等）",
    "chapter_missing": "报告中缺少必要的章节",
    "report_code_match": "报告中粘贴的核心代码与上传源码不一致",
    "ast_error": "源码未满足专题预设的 AST 规则",
    "running_no_result": "编译失败、超时、运行崩溃等无法得到结果",
    "running_result_ERROR": "程序可运行，但输出或结果不符合预期",
}

# 系统内置 AST 子规则：专题 id -> ((枚举名称, 枚举值), ...)。
# 这些行由系统维护，教师不可见、不可增删；AST 模块仍通过 Rule.name 读取。
AST_RULES_BY_TOPIC: dict[int, tuple[tuple[str, str], ...]] = {
    1: (
        ("TOPIC1_NO_STD_SORT", "topic1.no_std_sort"),
        ("TOPIC1_REQUIRE_SELF_LINEAR_LIST", "topic1.require_self_linear_list"),
        ("TOPIC1_REQUIRE_SELF_QUICK_SORT", "topic1.require_self_quick_sort"),
        ("TOPIC1_REQUIRE_SELF_HEAP_SORT", "topic1.require_self_heap_sort"),
    ),
    2: (
        ("TOPIC2_REQUIRE_TERM_STRUCT", "topic2.require_term_struct"),
        ("TOPIC2_REQUIRE_BST_NODE_STRUCT", "topic2.require_bst_node_struct"),
        ("TOPIC2_REQUIRE_AVL_NODE_STRUCT", "topic2.require_avl_node_struct"),
        ("TOPIC2_REQUIRE_AVL_BALANCE_MAINTENANCE", "topic2.require_avl_balance_maintenance"),
        ("TOPIC2_NO_STD_MAP_AS_CORE", "topic2.no_std_map_as_core"),
    ),
    3: (
        ("TOPIC3_REQUIRE_ADJACENCY_LIST", "topic3.require_adjacency_list"),
        ("TOPIC3_REQUIRE_SELF_DIJKSTRA", "topic3.require_self_dijkstra"),
        ("TOPIC3_REQUIRE_SELF_QUICK_SORT", "topic3.require_self_quick_sort"),
        ("TOPIC3_REQUIRE_SELF_MERGE_SORT", "topic3.require_self_merge_sort"),
        ("TOPIC3_NO_STD_SORT", "topic3.no_std_sort"),
    ),
}

BUILTIN_TOPIC_NAMES: dict[int, str] = {
    1: "topic1",
    2: "topic2",
    3: "topic3",
}

# 与 models/marking.py 的 ck_rule_name_allowed 保持一致：
# RuleTypeEnum 的名称/值 + AstRuleEnum 的名称/值。
SYSTEM_RULE_NAMES: tuple[str, ...] = tuple(sorted(
    set(SCORING_RULES)
    | {
        "FORMAT_ERROR",
        "CHAPTER_MISSING",
        "REPORT_CODE_MATCH",
        "AST_ERROR",
        "RUNNING_NO_RESULT",
        "RUNNING_RESULT_ERROR",
    }
    | {member for rules in AST_RULES_BY_TOPIC.values() for member, _ in rules}
    | {value for rules in AST_RULES_BY_TOPIC.values() for _, value in rules}
))

_RULE_NAME_IN = ", ".join(f"'{name}'" for name in SYSTEM_RULE_NAMES)


def upgrade() -> None:
    """收紧 rule 表约束，并 seed 三个内置专题及其评分/AST 规则。"""
    conn = op.get_bind()

    # 1) 清理历史自定义规则名（新库无数据，这里是安全的无操作）。
    conn.execute(sa.text(f"DELETE FROM rule WHERE name NOT IN ({_RULE_NAME_IN})"))

    # 2) 同一专题下重复的规则名只保留 id 最小的一条。
    conn.execute(sa.text(
        "DELETE FROM rule WHERE id NOT IN ("
        "SELECT MIN(id) FROM rule GROUP BY topic_id, name)"
    ))

    # 3) 添加约束。SQLite 需要 batch 模式重建表。
    with op.batch_alter_table("rule", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            "uq_rule_topic_name", ["topic_id", "name"]
        )
        batch_op.create_check_constraint(
            "ck_rule_weight_range", "weight >= 0 AND weight <= 100"
        )
        batch_op.create_check_constraint(
            "ck_rule_name_allowed", f"name IN ({_RULE_NAME_IN})"
        )

    # 4) seed 三个内置专题；已存在时跳过。
    for topic_id, topic_name in BUILTIN_TOPIC_NAMES.items():
        conn.execute(
            sa.text(
                "INSERT OR IGNORE INTO topic "
                "(id, name, description, total_score) "
                "VALUES (:id, :name, :description, :total_score)"
            ),
            {
                "id": topic_id,
                "name": topic_name,
                "description": f"{topic_name}（内置专题）",
                "total_score": 100,
            },
        )

    # 5) 为所有已存在专题补齐 6 个评分行；已有权重保持不变。
    topic_ids = [
        row[0] for row in conn.execute(sa.text("SELECT id FROM topic")).all()
    ]
    for topic_id in topic_ids:
        for rule_name, description in SCORING_RULES.items():
            conn.execute(
                sa.text(
                    "INSERT OR IGNORE INTO rule "
                    "(name, description, weight, topic_id) "
                    "VALUES (:name, :description, 0, :topic_id)"
                ),
                {
                    "name": rule_name,
                    "description": description,
                    "topic_id": topic_id,
                },
            )

    # 6) 为三个内置专题 seed 系统 AST 行。
    for topic_id, rules in AST_RULES_BY_TOPIC.items():
        row = conn.execute(
            sa.text("SELECT name FROM topic WHERE id = :id"), {"id": topic_id}
        ).first()
        if row is None or row[0] != BUILTIN_TOPIC_NAMES[topic_id]:
            continue
        for _, rule_name in rules:
            conn.execute(
                sa.text(
                    "INSERT OR IGNORE INTO rule "
                    "(name, description, weight, topic_id) "
                    "VALUES (:name, :description, 0, :topic_id)"
                ),
                {
                    "name": rule_name,
                    "description": f"系统 AST 规则：{rule_name}",
                    "topic_id": topic_id,
                },
            )


def downgrade() -> None:
    """只回退约束；seed 数据保留，避免误删迁移后产生的业务数据。"""
    with op.batch_alter_table("rule", schema=None) as batch_op:
        batch_op.drop_constraint("ck_rule_name_allowed", type_="check")
        batch_op.drop_constraint("ck_rule_weight_range", type_="check")
        batch_op.drop_constraint("uq_rule_topic_name", type_="unique")
