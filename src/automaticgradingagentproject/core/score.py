from automaticgradingagentproject.core.enums.format_error_enum import DocFormatCheck
from automaticgradingagentproject.core.enums.meaning_grade_enum import MeaningGradeEnum
from automaticgradingagentproject.core.enums.rule_type_enum import RuleTypeEnum
from automaticgradingagentproject.core.rule_types import RULE_TYPE_META

_MAX_HITS: dict[RuleTypeEnum, int] = {
    RuleTypeEnum.FORMAT_ERROR: 3,
    RuleTypeEnum.CHAPTER_MISSING: 2,
    RuleTypeEnum.REPORT_CODE_MATCH: 3,
    RuleTypeEnum.AST_ERROR: 4,
    RuleTypeEnum.RUNNING_NO_RESULT: 1,
    RuleTypeEnum.RUNNING_RESULT_ERROR: 1,
    RuleTypeEnum.MEANING_ANALYSIS: 2,
}

_FORMAT_CHECK_LABELS = {
    DocFormatCheck.FILE_NAMING.value: "文件命名",
    DocFormatCheck.LINE_SPACING.value: "行间距",
    DocFormatCheck.PAGE_NUMBER_FORMAT.value: "页码格式",
}

_MEANING_GRADES: dict[str, tuple[str, int, str]] = {
    MeaningGradeEnum.A.value: ("A", 0, "逻辑自洽，且与代码/测试反馈一致"),
    MeaningGradeEnum.B.value: ("B", 1, "逻辑自洽，但与代码/测试反馈存在矛盾"),
    MeaningGradeEnum.C.value: ("C", 2, "章节之间存在逻辑不自洽"),
}


def _weight_percent(weight: dict, rule_type: RuleTypeEnum) -> float:
    """读取百分数权重，兼容以枚举或枚举值作为 key 的字典。"""
    value = weight.get(rule_type)
    if value is None:
        value = weight.get(rule_type.value, 0)
    return value or 0


def _report_code_match_de_level(value: dict) -> int:
    """把 report_code_match 的 checked/count 综合映射为扣分等级（0-3）。"""
    # 没有找到代码片段标识，视为未检查/不遵守前置规则，直接扣满。
    if value.get("checked") is not True:
        return 3
    count = value.get("count", 0)
    if not isinstance(count, int) or count < 0:
        count = 0
    if count >= 2:
        return 2
    return count  # 0 -> 0，1 -> 1


def calculation_deduction(deduction: float, de_level: int, max_hits: int,
                          score_proportion: float):
    unit_penalty = score_proportion / max_hits
    if de_level > max_hits:
        deduction += max_hits * unit_penalty
    else:
        deduction += de_level * unit_penalty
    return deduction


def _format_points(value: float | int | None) -> str:
    """把分值格式化为人类可读文本，最多保留两位小数。"""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "0"
    rounded = round(float(value), 2)
    if abs(rounded - round(rounded)) < 1e-9:
        return str(int(round(rounded)))
    return f"{rounded:.2f}".rstrip("0").rstrip(".")


def _get_count(payload: dict) -> int | float:
    value = payload.get("count", 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return value


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [str(value)]


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "；".join(str(item) for item in value)
    return str(value)


def _truncate(value: object, limit: int = 300) -> str:
    text = _as_text(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _format_items(payload: dict) -> list[dict]:
    items = []
    for key, value in payload.items():
        if key in ("count", "messages", "review") or not isinstance(value, dict):
            continue
        raw = str(getattr(key, "value", key))
        items.append({
            "name": _FORMAT_CHECK_LABELS.get(raw, raw),
            "count": value.get("count", 0),
            "messages": _as_list(value.get("messages")),
        })
    return items


def _ast_items(payload: dict) -> list[dict]:
    items = []
    for key, value in payload.items():
        if key == "count" or not isinstance(value, dict):
            continue
        raw = str(getattr(key, "value", key))
        items.append({"name": raw, "count": value.get("count", 0)})
    return items


def _chapter_items(payload: dict) -> list[dict]:
    missing = payload.get("missing")
    if not isinstance(missing, list):
        return []
    return [
        {"name": str(name), "count": 1}
        for name in missing
        if isinstance(name, str) and name.strip()
    ]


def _meaning_grade(payload: dict) -> MeaningGradeEnum | None:
    for grade in MeaningGradeEnum:
        if isinstance(payload.get(grade.value), dict):
            return grade
    return None


def _base_entry(rule: RuleTypeEnum, weight: dict, total_score: int) -> dict:
    meta = RULE_TYPE_META.get(rule, {})
    max_hits = _MAX_HITS[rule]
    weight_percent = _weight_percent(weight, rule)
    max_points = weight_percent / 100 * total_score
    unit_points = max_points / max_hits if max_hits else 0
    return {
        "rule": rule.value,
        "label": meta.get("label", rule.value),
        "description": meta.get("description", ""),
        "status": "unavailable",
        "count": None,
        "applied": 0,
        "max_hits": max_hits,
        "weight_percent": weight_percent,
        "max_points": max_points,
        "unit_points": unit_points,
        "unit_points_text": _format_points(unit_points),
        "deducted_points": 0,
        "deducted_points_text": "0",
        "note": "未提供该模块结果，不参与评分",
        "items": [],
        "messages": [],
        "review": None,
        "skip_reason": None,
        "details": {},
        "grade": None,
        "checked": None,
    }


def _format_note(rule: RuleTypeEnum, count: int | float, applied: int,
                 skipped: bool = False) -> str:
    max_hits = _MAX_HITS[rule]
    if skipped:
        return "基础设施错误，不扣学生分"
    if applied <= 0:
        return "未发现该模块错误"
    if count > max_hits:
        return f"共 {count} 项，达到上限，按 {max_hits} 项扣分"
    return f"共 {count} 项，未超过上限 {max_hits} 项"


def score_report(
    weight: dict,
    total_score: int,
    word_parsing_return: dict,
    ast_validation_return: dict,
    sandbox_validation_return: dict,
    meaning_validation_return: dict,
) -> dict:
    """按与 ``score_`` 完全相同的顺序计算分数，并旁路记录逐项扣分。

    ``score_`` 的浮点累加顺序必须保持不变：不同顺序相加可能产生末位差异。
    因此这里复刻原来的四段遍历，只额外记录每次 ``calculation_deduction``
    调用前后的差值。
    """
    entries: dict[RuleTypeEnum, dict] = {}
    deduction = 0

    def entry_for(rule: RuleTypeEnum) -> dict:
        if rule not in entries:
            entries[rule] = _base_entry(rule, weight, total_score)
        return entries[rule]

    def record(rule: RuleTypeEnum, payload: dict, level: int | float, *,
               status: str, note: str, items: list[dict] | None = None,
               messages: list[str] | None = None, review: dict | None = None,
               skip_reason: str | None = None, details: dict | None = None,
               grade: str | None = None, checked: object = None) -> None:
        nonlocal deduction
        entry = entry_for(rule)
        entry["count"] = _get_count(payload)
        entry["applied"] = 0 if status == "skipped" else min(level, _MAX_HITS[rule])
        entry["status"] = status
        entry["note"] = note
        entry["items"] = items or []
        entry["messages"] = messages or []
        entry["review"] = review
        entry["skip_reason"] = skip_reason
        entry["details"] = details or {}
        entry["grade"] = grade
        entry["checked"] = checked

        before = deduction
        if status != "skipped":
            deduction = calculation_deduction(
                deduction, level, _MAX_HITS[rule], entry["max_points"]
            )
        entry["deducted_points"] = deduction - before
        entry["deducted_points_text"] = _format_points(entry["deducted_points"])

    # 与 score_ 相同的遍历顺序：word -> ast -> sandbox -> meaning。
    if isinstance(word_parsing_return, dict):
        for key, value in word_parsing_return.items():
            if key == RuleTypeEnum.FORMAT_ERROR and isinstance(value, dict):
                count = _get_count(value)
                applied = min(count, _MAX_HITS[RuleTypeEnum.FORMAT_ERROR])
                record(
                    RuleTypeEnum.FORMAT_ERROR, value, count,
                    status="deducted" if applied > 0 else "ok",
                    note=_format_note(RuleTypeEnum.FORMAT_ERROR, count, applied),
                    items=_format_items(value),
                    messages=_as_list(value.get("messages")),
                )
            elif key == RuleTypeEnum.CHAPTER_MISSING and isinstance(value, dict):
                count = _get_count(value)
                applied = min(count, _MAX_HITS[RuleTypeEnum.CHAPTER_MISSING])
                review = value.get("review")
                note = _format_note(RuleTypeEnum.CHAPTER_MISSING, count, applied)
                if isinstance(review, dict):
                    recovered = review.get("recovered")
                    if isinstance(recovered, list) and recovered:
                        note += f"；章节复核已恢复：{'、'.join(map(str, recovered))}"
                    error_message = review.get("error_message")
                    if error_message:
                        note += f"；章节复核未完成：{_truncate(error_message)}"
                record(
                    RuleTypeEnum.CHAPTER_MISSING, value, count,
                    status="deducted" if applied > 0 else "ok",
                    note=note,
                    items=_chapter_items(value),
                    messages=_as_list(value.get("messages")),
                    review=review if isinstance(review, dict) else None,
                )
            elif key == RuleTypeEnum.REPORT_CODE_MATCH and isinstance(value, dict):
                level = _report_code_match_de_level(value)
                checked = value.get("checked")
                if checked is not True:
                    note = "未找到可核对的代码片段，按未检查处理，扣满 3/3 级"
                elif level == 0:
                    note = "已检查，未发现报告与代码不一致"
                else:
                    note = (
                        f"已检查，发现 {_get_count(value)} 处不一致，"
                        f"按 {level} 级扣分（checked=true 时最高 2 级）"
                    )
                record(
                    RuleTypeEnum.REPORT_CODE_MATCH, value, level,
                    status="deducted" if level > 0 else "ok",
                    note=note,
                    messages=_as_list(value.get("messages")),
                    checked=checked,
                )

    if isinstance(ast_validation_return, dict):
        for key, value in ast_validation_return.items():
            if key == RuleTypeEnum.AST_ERROR and isinstance(value, dict):
                count = _get_count(value)
                applied = min(count, _MAX_HITS[RuleTypeEnum.AST_ERROR])
                record(
                    RuleTypeEnum.AST_ERROR, value, count,
                    status="deducted" if applied > 0 else "ok",
                    note=_format_note(RuleTypeEnum.AST_ERROR, count, applied),
                    items=_ast_items(value),
                )

    if isinstance(sandbox_validation_return, dict):
        for key, value in sandbox_validation_return.items():
            if not isinstance(value, dict):
                continue
            if key == RuleTypeEnum.RUNNING_NO_RESULT:
                count = _get_count(value)
                error_message = value.get("error_message")
                details = {
                    "compilation_error_message": _truncate(
                        value.get("compilation_error_message")
                    ),
                    "error_message": _as_text(error_message),
                }
                if error_message:
                    record(
                        RuleTypeEnum.RUNNING_NO_RESULT, value, count,
                        status="skipped",
                        note="沙箱基础设施/契约异常，本模块不参与评分、不扣学生分",
                        skip_reason=_as_text(error_message),
                        details=details,
                    )
                else:
                    applied = min(count, _MAX_HITS[RuleTypeEnum.RUNNING_NO_RESULT])
                    record(
                        RuleTypeEnum.RUNNING_NO_RESULT, value, count,
                        status="deducted" if applied > 0 else "ok",
                        note=_format_note(
                            RuleTypeEnum.RUNNING_NO_RESULT, count, applied
                        ),
                        details=details,
                    )
            elif key == RuleTypeEnum.RUNNING_RESULT_ERROR:
                count = _get_count(value)
                applied = min(count, _MAX_HITS[RuleTypeEnum.RUNNING_RESULT_ERROR])
                record(
                    RuleTypeEnum.RUNNING_RESULT_ERROR, value, count,
                    status="deducted" if applied > 0 else "ok",
                    note=(
                        f"共 {count} 项，按 {applied} 次计扣；"
                        f"pass_rate 仅展示，不参与计分"
                    ),
                    details={
                        "pass_rate": value.get("pass_rate"),
                        "average_run_time": value.get("average_run_time"),
                    },
                )

    if isinstance(meaning_validation_return, dict):
        for key, value in meaning_validation_return.items():
            if key == RuleTypeEnum.MEANING_ANALYSIS and isinstance(value, dict):
                count = _get_count(value)
                grade = _meaning_grade(value)
                grade_info = _MEANING_GRADES.get(grade.value) if grade else None
                grade_note = grade_info[2] if grade_info else "语义档位无法识别"
                error_message = value.get("error_message")
                if error_message:
                    record(
                        RuleTypeEnum.MEANING_ANALYSIS, value, count,
                        status="skipped",
                        note=f"语义分析未参与评分：{_truncate(error_message)}",
                        skip_reason=_as_text(error_message),
                        grade=grade_info[0] if grade_info else None,
                    )
                else:
                    applied = min(count, _MAX_HITS[RuleTypeEnum.MEANING_ANALYSIS])
                    record(
                        RuleTypeEnum.MEANING_ANALYSIS, value, count,
                        status="deducted" if applied > 0 else "ok",
                        note=(
                            f"{grade_info[0] if grade_info else '?'} 档：{grade_note}，"
                            f"按 {applied} 级计扣"
                        ),
                        items=(
                            [{"name": grade_info[0], "count": 1,
                              "label": f"{grade_info[0]} 档"}]
                            if grade_info else []
                        ),
                        grade=grade_info[0] if grade_info else None,
                    )

    # 没有上报的规则补占位条目，避免评语把缺失模块当成满分。
    for rule in RuleTypeEnum:
        entry_for(rule)

    ordered_entries = [entries[rule] for rule in RuleTypeEnum]
    final_score = max(0, min(total_score, total_score - deduction))

    contents = (
        word_parsing_return.get("contents")
        if isinstance(word_parsing_return, dict) else None
    )
    chapters = [str(key) for key in contents] if isinstance(contents, dict) else []

    return {
        "total_score": total_score,
        "final_score": final_score,
        "final_score_text": _format_points(final_score),
        "total_deduction": deduction,
        "total_deduction_text": _format_points(deduction),
        "chapters": chapters,
        "entries": ordered_entries,
    }


def score_(weight: dict, total_score: int, word_parsing_return: dict,
           ast_validation_return: dict, sandbox_validation_return: dict,
           meaning_validation_return: dict):
    return score_report(
        weight, total_score, word_parsing_return, ast_validation_return,
        sandbox_validation_return, meaning_validation_return,
    )["final_score"]
