from automaticgradingagentproject.core.enums.rule_type_enum import RuleTypeEnum


def _weight_percent(weight: dict, rule_type: RuleTypeEnum) -> float:
    """读取百分数权重，兼容以枚举或枚举值作为 key 的字典。"""
    value = weight.get(rule_type)
    if value is None:
        value = weight.get(rule_type.value, 0)
    return value or 0


def score_(weight: dict, total_score: int, word_parsing_return: dict,
           ast_validation_return: dict, sandbox_validation_return: dict,
           meaning_validation_return: dict):
    deduction = 0
    for key, value in word_parsing_return.items():
        if key == RuleTypeEnum.FORMAT_ERROR:
            count = value["count"]
            score_proportion = _weight_percent(weight, RuleTypeEnum.FORMAT_ERROR) / 100 * total_score
            max_hits = 3
            deduction = calculation_deduction(deduction, count, max_hits, score_proportion)
        elif key == RuleTypeEnum.CHAPTER_MISSING:
            count = value["count"]
            score_proportion = _weight_percent(weight, RuleTypeEnum.CHAPTER_MISSING) / 100 * total_score
            max_hits = 2
            deduction = calculation_deduction(deduction, count, max_hits, score_proportion)
        elif key == RuleTypeEnum.REPORT_CODE_MATCH:
            count = value["count"]
            score_proportion = _weight_percent(weight, RuleTypeEnum.REPORT_CODE_MATCH) / 100 * total_score
            max_hits = 3
            deduction = calculation_deduction(deduction, count, max_hits, score_proportion)

    for key, value in ast_validation_return.items():
        if key == RuleTypeEnum.AST_ERROR:
            count = value["count"]
            score_proportion = _weight_percent(weight, RuleTypeEnum.AST_ERROR) / 100 * total_score
            max_hits = 4
            deduction = calculation_deduction(deduction, count, max_hits, score_proportion)

    for key, value in sandbox_validation_return.items():
        if key == RuleTypeEnum.RUNNING_NO_RESULT:
            # error_message 非空表示 Docker/配置等基础设施错误，不扣学生分。
            if value.get("error_message"):
                continue
            count = value["count"]
            score_proportion = _weight_percent(weight, RuleTypeEnum.RUNNING_NO_RESULT) / 100 * total_score
            max_hits = 1
            deduction = calculation_deduction(deduction, count, max_hits, score_proportion)
        elif key in (RuleTypeEnum.RUNNING_RESULT_ERROR, "running_result_error"):
            # 沙箱报告使用小写 key；评分统一按 count 扣分，pass_rate 只是展示字段。
            count = value["count"]
            score_proportion = _weight_percent(weight, RuleTypeEnum.RUNNING_RESULT_ERROR) / 100 * total_score
            max_hits = 1
            deduction = calculation_deduction(deduction, count, max_hits, score_proportion)

    for key, value in meaning_validation_return.items():
        if key == RuleTypeEnum.MEANING_ANALYSIS:
            # 语义分析不可用时（缺文本/模型调用失败）不扣学生分。
            if value.get("error_message"):
                continue
            # A=0、B=1、C=2，max_hits=2：B 扣一半，C 扣全部。
            count = value["count"]
            score_proportion = _weight_percent(weight, RuleTypeEnum.MEANING_ANALYSIS) / 100 * total_score
            max_hits = 2
            deduction = calculation_deduction(deduction, count, max_hits, score_proportion)

    answer = total_score - deduction
    return max(0, min(total_score, answer))
                           
         



def calculation_deduction(deduction:float,count:int,max_hits:int,score_proportion:float):
    unit_penalty = score_proportion/max_hits
    if count>max_hits:
        deduction+=max_hits*unit_penalty
    else:
        deduction+=count*unit_penalty
    return deduction