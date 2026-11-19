import pytest

import automaticgradingagentproject.core.llm_invoke as llm_invoke_module
from automaticgradingagentproject.core.enums.format_error_enum import DocFormatCheck
from automaticgradingagentproject.core.enums.meaning_grade_enum import MeaningGradeEnum
from automaticgradingagentproject.core.enums.rule_type_enum import RuleTypeEnum
from automaticgradingagentproject.core.llm_invoke import build_comment_prompt
from automaticgradingagentproject.core.score import score_, score_report
from automaticgradingagentproject.services import agent_service

WEIGHTS = {
    RuleTypeEnum.FORMAT_ERROR: 10,
    RuleTypeEnum.CHAPTER_MISSING: 10,
    RuleTypeEnum.REPORT_CODE_MATCH: 10,
    RuleTypeEnum.AST_ERROR: 15,
    RuleTypeEnum.RUNNING_NO_RESULT: 20,
    RuleTypeEnum.RUNNING_RESULT_ERROR: 25,
    RuleTypeEnum.MEANING_ANALYSIS: 10,
}


def sample_materials():
    word = {
        RuleTypeEnum.FORMAT_ERROR: {
            "count": 2,
            "messages": [],
            DocFormatCheck.FILE_NAMING: {
                "count": 1, "messages": ["文件名不规范"],
            },
            DocFormatCheck.LINE_SPACING: {"count": 1, "messages": []},
        },
        RuleTypeEnum.CHAPTER_MISSING: {
            "count": 1,
            "missing": ["总结"],
            "messages": ["缺少章节：总结"],
            "review": {
                "recovered": ["功能与设计"],
                "confirmed_missing": ["总结"],
                "error_message": None,
            },
        },
        RuleTypeEnum.REPORT_CODE_MATCH: {
            "count": 1,
            "checked": True,
            "messages": [],
        },
        "contents": {
            "功能分析": "SECRET_REPORT_BODY",
            "总结": "SECRET_SUMMARY",
        },
    }
    ast = {
        RuleTypeEnum.AST_ERROR.value: {
            "count": 2,
            "topic1.no_std_sort": {"count": 1},
            "topic1.require_self_heap_sort": {"count": 1},
        }
    }
    sandbox = {
        "running_no_result": {
            "count": 1,
            "compilation_error_message": "",
            "error_message": ["docker unavailable"],
        },
        "running_result_error": {
            "count": 0,
            "pass_rate": "0%",
            "average_run_time": "0ms",
        },
    }
    meaning = {
        RuleTypeEnum.MEANING_ANALYSIS.value: {
            "count": 1,
            MeaningGradeEnum.B.value: {"count": 1},
        }
    }
    return word, ast, sandbox, meaning


def entries_by_rule(report):
    return {entry["rule"]: entry for entry in report["entries"]}


def test_score_report_matches_score_and_records_entries():
    word, ast, sandbox, meaning = sample_materials()

    report = score_report(WEIGHTS, 100, word, ast, sandbox, meaning)

    assert report["final_score"] == score_(WEIGHTS, 100, word, ast, sandbox, meaning)
    assert report["final_score"] == 72.5
    assert report["final_score_text"] == "72.5"
    assert report["total_deduction"] == 27.5
    assert report["total_deduction_text"] == "27.5"

    assert [entry["rule"] for entry in report["entries"]] == [
        rule.value for rule in RuleTypeEnum
    ]

    entries = entries_by_rule(report)
    assert entries["format_error"]["status"] == "deducted"
    assert entries["format_error"]["applied"] == 2
    assert entries["format_error"]["deducted_points_text"] == "6.67"
    assert entries["format_error"]["items"][0]["name"] == "文件命名"

    assert entries["chapter_missing"]["status"] == "deducted"
    assert entries["chapter_missing"]["count"] == 1
    assert entries["chapter_missing"]["review"]["recovered"] == ["功能与设计"]
    assert "复核已恢复" in entries["chapter_missing"]["note"]

    assert entries["report_code_match"]["applied"] == 1
    assert entries["report_code_match"]["checked"] is True

    assert entries["ast_error"]["applied"] == 2
    assert entries["ast_error"]["deducted_points_text"] == "7.5"

    assert entries["running_no_result"]["status"] == "skipped"
    assert entries["running_no_result"]["applied"] == 0
    assert entries["running_no_result"]["deducted_points"] == 0
    assert entries["running_no_result"]["skip_reason"] == "docker unavailable"

    assert entries["running_result_error"]["status"] == "ok"
    assert entries["running_result_error"]["details"]["pass_rate"] == "0%"

    assert entries["meaning_analysis"]["status"] == "deducted"
    assert entries["meaning_analysis"]["grade"] == "B"
    assert entries["meaning_analysis"]["applied"] == 1


def test_score_report_marks_unreported_modules_unavailable():
    report = score_report(WEIGHTS, 100, {}, {}, {}, {})

    assert report["final_score"] == 100
    entries = entries_by_rule(report)
    for rule in RuleTypeEnum:
        entry = entries[rule.value]
        assert entry["status"] == "unavailable"
        assert entry["count"] is None
        assert entry["deducted_points"] == 0
        assert "满分" not in entry["note"]
        assert "未提供该模块结果" in entry["note"]


@pytest.mark.parametrize(
    ("payload", "applied", "status"),
    [
        ({"count": 0, "checked": False}, 3, "deducted"),
        ({"count": 1}, 3, "deducted"),
        ({"count": 0, "checked": True}, 0, "ok"),
        ({"count": 1, "checked": True}, 1, "deducted"),
        ({"count": 2, "checked": True}, 2, "deducted"),
        ({"count": 5, "checked": True}, 2, "deducted"),
    ],
)
def test_score_report_report_code_match_levels(payload, applied, status):
    report = score_report(
        {RuleTypeEnum.REPORT_CODE_MATCH: 30}, 100,
        {RuleTypeEnum.REPORT_CODE_MATCH: payload}, {}, {}, {},
    )
    entry = entries_by_rule(report)["report_code_match"]
    assert entry["applied"] == applied
    assert entry["status"] == status


@pytest.mark.parametrize(
    ("grade", "applied", "letter", "status"),
    [
        (MeaningGradeEnum.A, 0, "A", "ok"),
        (MeaningGradeEnum.B, 1, "B", "deducted"),
        (MeaningGradeEnum.C, 2, "C", "deducted"),
    ],
)
def test_score_report_meaning_grades(grade, applied, letter, status):
    meaning = {
        RuleTypeEnum.MEANING_ANALYSIS.value: {
            "count": grade.severity,
            grade.value: {"count": 1},
        }
    }
    report = score_report(
        {RuleTypeEnum.MEANING_ANALYSIS: 20}, 100, {}, {}, {}, meaning
    )
    entry = entries_by_rule(report)["meaning_analysis"]
    assert entry["applied"] == applied
    assert entry["grade"] == letter
    assert entry["status"] == status


def test_score_report_meaning_error_is_skipped():
    meaning = {
        RuleTypeEnum.MEANING_ANALYSIS.value: {
            "count": 0,
            "error_message": "语义分析调用失败",
        }
    }
    report = score_report(
        {RuleTypeEnum.MEANING_ANALYSIS: 20}, 100, {}, {}, {}, meaning
    )
    entry = entries_by_rule(report)["meaning_analysis"]
    assert entry["status"] == "skipped"
    assert entry["deducted_points"] == 0
    assert "语义分析调用失败" in entry["skip_reason"]


def test_build_comment_prompt_uses_report_and_hides_contents():
    word, ast, sandbox, meaning = sample_materials()
    report = score_report(WEIGHTS, 100, word, ast, sandbox, meaning)

    prompt = build_comment_prompt(
        word, ast, sandbox, meaning, report["final_score"], report
    )

    assert "最终得分：72.5" in prompt
    assert "扣分合计：27.5" in prompt
    assert "格式错误：扣 6.67 分" in prompt
    assert "未参与评分" in prompt
    assert "不得自行计算" in prompt
    assert "章节复核已恢复" in prompt
    assert "缺失章节：总结" in prompt
    assert "功能与设计" in prompt
    assert "SECRET_REPORT_BODY" not in prompt
    assert "SECRET_SUMMARY" not in prompt


def test_build_comment_prompt_weight_zero_is_explained():
    word = {
        RuleTypeEnum.FORMAT_ERROR: {
            "count": 1,
            DocFormatCheck.FILE_NAMING: {"count": 1, "messages": []},
        }
    }
    report = score_report({RuleTypeEnum.FORMAT_ERROR: 0}, 100, word, {}, {}, {})

    prompt = build_comment_prompt(word, {}, {}, {}, report["final_score"], report)

    assert report["entries"][0]["status"] == "deducted"
    assert report["entries"][0]["deducted_points"] == 0
    assert "权重为 0" in prompt


def test_build_comment_prompt_legacy_strips_contents():
    word, ast, sandbox, meaning = sample_materials()

    prompt = build_comment_prompt(word, ast, sandbox, meaning, 80, None)

    assert "他的得分是80" in prompt
    assert "禁止编造" in prompt
    assert "功能与设计" in prompt
    assert "SECRET_REPORT_BODY" not in prompt
    assert "SECRET_SUMMARY" not in prompt


def test_llm_client_temperature_is_zero():
    assert llm_invoke_module.llm.temperature == 0


@pytest.mark.asyncio
async def test_llm_comment_node_passes_report(monkeypatch):
    captured = {}

    async def fake_comment(*args):
        captured["args"] = args
        return "测试评语"

    monkeypatch.setattr(agent_service, "llm_comment_", fake_comment)
    report = {"final_score": 80, "entries": []}
    state = {
        "word_parsing_return": {},
        "ast_validation_return": {},
        "sandbox_validation_return": {},
        "meaning_validation_return": {},
        "output": {"score": 80, "report": report},
    }

    result = await agent_service.llm_comment(state)

    assert result["llm_comment"] == "测试评语"
    assert captured["args"][4] == 80
    assert captured["args"][5] is report


@pytest.mark.asyncio
async def test_llm_comment_node_without_report_passes_none(monkeypatch):
    captured = {}

    async def fake_comment(*args):
        captured["args"] = args
        return "测试评语"

    monkeypatch.setattr(agent_service, "llm_comment_", fake_comment)
    state = {
        "word_parsing_return": {},
        "ast_validation_return": {},
        "sandbox_validation_return": {},
        "meaning_validation_return": {},
        "output": {"score": 80},
    }

    result = await agent_service.llm_comment(state)

    assert result["llm_comment"] == "测试评语"
    assert captured["args"][5] is None
