"""评分规则类型的单一事实来源。

教师只能配置 `RuleTypeEnum` 中的顶层类型；`AstRuleEnum` 由 AST 模块内部使用，
不在教师可写范围内。
"""

from automaticgradingagentproject.core.enums.ast_rule_enum import AstRuleEnum
from automaticgradingagentproject.core.enums.rule_type_enum import RuleTypeEnum

# 前端展示用的固定元数据：值、中文名、说明、默认权重（百分数）。
RULE_TYPE_META: dict[RuleTypeEnum, dict[str, str | int]] = {
    RuleTypeEnum.FORMAT_ERROR: {
        "value": RuleTypeEnum.FORMAT_ERROR.value,
        "label": "格式错误",
        "description": "文档格式不符合要求（行距、页码、文件命名等）",
        "default_weight": 0,
    },
    RuleTypeEnum.CHAPTER_MISSING: {
        "value": RuleTypeEnum.CHAPTER_MISSING.value,
        "label": "章节缺失",
        "description": "报告中缺少必要的章节",
        "default_weight": 0,
    },
    RuleTypeEnum.REPORT_CODE_MATCH: {
        "value": RuleTypeEnum.REPORT_CODE_MATCH.value,
        "label": "报告与代码一致性",
        "description": "报告中粘贴的核心代码与上传源码不一致",
        "default_weight": 0,
    },
    RuleTypeEnum.AST_ERROR: {
        "value": RuleTypeEnum.AST_ERROR.value,
        "label": "AST 静态检查",
        "description": "源码未满足专题预设的 AST 规则",
        "default_weight": 0,
    },
    RuleTypeEnum.RUNNING_NO_RESULT: {
        "value": RuleTypeEnum.RUNNING_NO_RESULT.value,
        "label": "运行无结果",
        "description": "编译失败、超时、运行崩溃等无法得到结果",
        "default_weight": 0,
    },
    RuleTypeEnum.RUNNING_RESULT_ERROR: {
        "value": RuleTypeEnum.RUNNING_RESULT_ERROR.value,
        "label": "运行结果错误",
        "description": "程序可运行，但输出或结果不符合预期",
        "default_weight": 0,
    },
    RuleTypeEnum.MEANING_ANALYSIS: {
        "value": RuleTypeEnum.MEANING_ANALYSIS.value,
        "label": "文档语义分析",
        "description": "报告文本的逻辑自洽性，以及与 AST/沙箱反馈的一致性",
        "default_weight": 0,
    },
}

# 教师接口允许的取值集合（RuleTypeEnum 的值）。
ALLOWED_RULE_TYPE_VALUES: frozenset[str] = frozenset(
    rule_type.value for rule_type in RuleTypeEnum
)

RULE_TYPE_BY_VALUE: dict[str, RuleTypeEnum] = {
    rule_type.value: rule_type for rule_type in RuleTypeEnum
}

# 数据库允许出现的系统规则名称（教师顶层类型 + AST 子规则）。
# AST 模块仍通过 Rule.name 读取规则，因此需要保留 AstRuleEnum 的名称和值。
SYSTEM_RULE_NAMES: frozenset[str] = frozenset(
    {rule_type.value for rule_type in RuleTypeEnum}
    | {rule_type.name for rule_type in RuleTypeEnum}
    | {rule.value for rule in AstRuleEnum}
    | {rule.name for rule in AstRuleEnum}
)


def is_allowed_rule_type(name: str) -> bool:
    """判断名称是否属于教师可配置的顶层规则类型。"""
    return name in ALLOWED_RULE_TYPE_VALUES


def normalize_rule_type(name: str | RuleTypeEnum) -> str:
    """把教师传入的规则类型规范化为 RuleTypeEnum 的值。"""
    value = name.value if isinstance(name, RuleTypeEnum) else name
    if value not in ALLOWED_RULE_TYPE_VALUES:
        allowed = "、".join(rule_type.value for rule_type in RuleTypeEnum)
        raise ValueError(f"不支持的规则类型：{value!r}；只能是：{allowed}")
    return value


def rule_type_items() -> list[dict[str, str | int]]:
    """按枚举定义顺序返回前端展示所需的固定规则类型。"""
    return [dict(RULE_TYPE_META[rule_type]) for rule_type in RuleTypeEnum]


def validate_rule_weights(weights: dict[RuleTypeEnum, int]) -> None:
    """校验权重字典是否包含全部顶层类型且合计为 100。"""
    missing = [
        rule_type.value for rule_type in RuleTypeEnum if rule_type not in weights
    ]
    if missing:
        raise ValueError(f"缺少评分规则配置：{'、'.join(missing)}")

    total = sum(weights.values())
    if total != 100:
        raise ValueError(f"评分规则权重合计必须为 100，当前为 {total}")
