from unittest.mock import AsyncMock, MagicMock

import pytest

import automaticgradingagentproject.core.llm_invoke as llm_invoke_module
from automaticgradingagentproject.core.chapter_config import (
    canonical_chapter,
    extract_section_text,
    is_chapter_heading,
    normalize_chapter_title,
    split_chapters,
)
from automaticgradingagentproject.core.chapter_reconsider import (
    apply_chapter_reviews,
)
from automaticgradingagentproject.core.enums.rule_type_enum import RuleTypeEnum
from automaticgradingagentproject.core.llm_invoke import (
    _extract_json_object,
    llm_chapter_reconsider,
    parse_chapter_reviews,
)
from automaticgradingagentproject.services import agent_service

FULL_TEXT = (
    "1 题目与要求\n"
    "本系统要求实现一个学生成绩管理系统。\n"
    "2 功能与设计\n"
    "系统包含录入、查询、排序三个功能。\n"
    "3 功能代码\n"
    "int main() { return 0; }\n"
    "4 调试与测试\n"
    "测试了录入和查询功能。\n"
    "5 总结\n"
    "完成了课程设计。\n"
    "附录\n"
    "源代码见附件。\n"
)

CHAPTER_MISSING = RuleTypeEnum.CHAPTER_MISSING

TEACHER_CONFIG = {
    "total_score": 100,
    "weights": {CHAPTER_MISSING: 10},
}


def chapter_return(missing, count=None, contents=None):
    return {
        CHAPTER_MISSING: {
            "count": len(missing) if count is None else count,
            "missing": list(missing),
            "messages": [f"缺少章节：{name}" for name in missing],
        },
        "contents": {} if contents is None else contents,
    }


# ---------------------------------------------------------------------------
# 章节配置与切分
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("一、功能与设计", "功能与设计"),
        ("2 功能与设计", "功能与设计"),
        ("第3章 功能代码", "功能代码"),
        ("（四）调试与测试", "调试与测试"),
        ("1.2 功能与设计", "功能与设计"),
        ("附录Ａ", "附录a"),
        ("  5 总结  ", "总结"),
    ],
)
def test_normalize_chapter_title(raw, expected):
    assert normalize_chapter_title(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("功能与设计", "功能与设计"),
        ("功能分析", "功能与设计"),
        ("二、功能分析", "功能与设计"),
        ("测试与调试", "调试与测试"),
        ("附录B", "附录"),
        ("结论", "总结"),
        ("本系统功能与设计如下", None),
        ("功能设计说明", None),
    ],
)
def test_canonical_chapter(raw, expected):
    assert canonical_chapter(raw) == expected


def test_is_chapter_heading():
    assert is_chapter_heading("2 功能与设计") == "功能与设计"
    assert is_chapter_heading("功能分析") == "功能与设计"
    assert is_chapter_heading("功能与设计 ........ 3") is None
    assert is_chapter_heading("本系统包含功能与设计。") is None
    assert is_chapter_heading("很长" * 30) is None
    assert is_chapter_heading("") is None


def test_split_chapters_and_extract_section_text():
    chapters = split_chapters(FULL_TEXT)
    assert chapters["功能与设计"] == "2 功能与设计\n系统包含录入、查询、排序三个功能。"
    assert chapters["附录"] == "附录\n源代码见附件。"
    assert extract_section_text(FULL_TEXT, "总结") == "5 总结\n完成了课程设计。"
    assert extract_section_text(FULL_TEXT, "不存在的章节") is None
    assert split_chapters("") == {}


def test_split_chapters_prefers_body_over_table_of_contents():
    full_text = (
        "目录\n"
        "功能与设计........1\n"
        "总结........2\n"
        "2 功能与设计\n"
        "正文内容\n"
        "5 总结\n"
        "总结内容\n"
    )
    chapters = split_chapters(full_text)
    assert "正文内容" in chapters["功能与设计"]
    assert "总结内容" in chapters["总结"]


# ---------------------------------------------------------------------------
# apply_chapter_reviews
# ---------------------------------------------------------------------------

def test_apply_recovers_all_and_backfills_contents():
    original = chapter_return(["功能与设计", "总结"])
    reviews = [
        {"name": "功能与设计", "present": True, "evidence": "2 功能与设计"},
        {"name": "总结", "present": True, "evidence": "5 总结"},
    ]

    updated = apply_chapter_reviews(original, reviews, FULL_TEXT)
    chapter = updated[CHAPTER_MISSING]

    assert chapter["count"] == 0
    assert chapter["missing"] == []
    assert chapter["messages"] == []
    assert chapter["review"]["recovered"] == ["功能与设计", "总结"]
    assert "系统包含录入" in updated["contents"]["功能与设计"]
    assert "完成了课程设计" in updated["contents"]["总结"]

    # 入参不能被原地修改。
    assert original[CHAPTER_MISSING]["count"] == 2
    assert original["contents"] == {}


def test_apply_recovers_one():
    updated = apply_chapter_reviews(
        chapter_return(["功能与设计", "总结"]),
        [{"name": "功能与设计", "present": True, "evidence": "2 功能与设计"}],
        FULL_TEXT,
    )
    chapter = updated[CHAPTER_MISSING]
    assert chapter["count"] == 1
    assert chapter["missing"] == ["总结"]
    assert chapter["messages"] == ["缺少章节：总结"]


def test_apply_ignores_unknown_non_bool_and_missing_evidence():
    reviews = [
        {"name": "功能代码", "present": True, "evidence": "3 功能代码"},
        {"name": "功能与设计", "present": "true", "evidence": "2 功能与设计"},
        {"name": "总结", "present": True, "evidence": "原文中不存在的证据"},
    ]
    updated = apply_chapter_reviews(
        chapter_return(["功能与设计", "总结"]), reviews, FULL_TEXT
    )
    chapter = updated[CHAPTER_MISSING]
    assert chapter["count"] == 2
    assert chapter["missing"] == ["功能与设计", "总结"]
    assert chapter["review"]["recovered"] == []


def test_apply_failure_keeps_original_judgement():
    updated = apply_chapter_reviews(
        chapter_return(["功能与设计", "总结"]),
        [],
        FULL_TEXT,
        error_message="章节复核调用失败",
    )
    chapter = updated[CHAPTER_MISSING]
    assert chapter["count"] == 2
    assert chapter["missing"] == ["功能与设计", "总结"]
    assert chapter["review"]["error_message"] == "章节复核调用失败"


def test_apply_count_greater_than_candidate_list_keeps_unexplained():
    updated = apply_chapter_reviews(
        chapter_return(["功能与设计", "总结"], count=5),
        [
            {"name": "功能与设计", "present": True, "evidence": "2 功能与设计"},
            {"name": "总结", "present": True, "evidence": "5 总结"},
        ],
        FULL_TEXT,
    )
    # 3 个未知缺失继续保留。
    assert updated[CHAPTER_MISSING]["count"] == 3


def test_apply_count_smaller_than_candidate_list_never_increases():
    updated = apply_chapter_reviews(
        chapter_return(["功能与设计", "总结", "附录"], count=1),
        [{"name": "功能与设计", "present": True, "evidence": "2 功能与设计"}],
        FULL_TEXT,
    )
    assert updated[CHAPTER_MISSING]["count"] == 1


def test_apply_does_not_duplicate_existing_alias_contents():
    updated = apply_chapter_reviews(
        chapter_return(["功能与设计"], contents={"功能分析": "已有内容"}),
        [{"name": "功能与设计", "present": True, "evidence": "2 功能与设计"}],
        FULL_TEXT,
    )
    assert updated["contents"] == {"功能分析": "已有内容"}


def test_apply_without_missing_list_is_noop():
    word_parsing_return = {CHAPTER_MISSING: {"count": 2}}
    updated = apply_chapter_reviews(
        word_parsing_return,
        [{"name": "总结", "present": True, "evidence": "5 总结"}],
        FULL_TEXT,
    )
    assert updated[CHAPTER_MISSING]["count"] == 2
    assert "missing" not in updated[CHAPTER_MISSING]


def test_apply_without_chapter_missing_is_noop():
    word_parsing_return = {"contents": {"总结": "内容"}}
    assert apply_chapter_reviews(word_parsing_return, [], FULL_TEXT) == word_parsing_return


# ---------------------------------------------------------------------------
# JSON 提取与 LLM 复核
# ---------------------------------------------------------------------------

VALID_JSON = (
    '{"reviews": ['
    '{"name": "功能与设计", "present": true, "evidence": "2 功能与设计"},'
    '{"name": "总结", "present": false, "evidence": ""}'
    "]}"
)


@pytest.mark.parametrize(
    "text",
    [
        VALID_JSON,
        f"```json\n{VALID_JSON}\n```",
        f"以下是结果：\n{VALID_JSON}\n请查收。",
        f"```\n{VALID_JSON}\n```",
    ],
)
def test_extract_json_object(text):
    payload = _extract_json_object(text)
    assert isinstance(payload, dict)
    assert isinstance(payload["reviews"], list)


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "无法判断",
        '{"reviews": [{"name": "总结"',
        '[{"name": "总结", "present": true}]',
    ],
)
def test_extract_json_object_invalid(text):
    assert _extract_json_object(text) is None


def test_parse_chapter_reviews_filters_invalid_items():
    reviews = parse_chapter_reviews(
        '{"reviews": ['
        '{"name": "功能与设计", "present": true, "evidence": "2 功能与设计"},'
        '{"name": "总结", "present": "true", "evidence": "5 总结"},'
        '{"name": "", "present": true},'
        '"not-a-dict"'
        "]}"
    )
    assert reviews == [
        {"name": "功能与设计", "present": True, "evidence": "2 功能与设计"}
    ]


@pytest.mark.parametrize(
    "text",
    [
        '{"reviews": []}',
        '{"reviews": "bad"}',
        '{"reviews": [{"name": "总结", "present": "true"}]}',
        "not json",
    ],
)
def test_parse_chapter_reviews_rejects_invalid(text):
    assert parse_chapter_reviews(text) is None


@pytest.mark.asyncio
async def test_llm_chapter_reconsider_parses(monkeypatch):
    ainvoke = AsyncMock(
        return_value=MagicMock(content=f"```json\n{VALID_JSON}\n```")
    )
    monkeypatch.setattr(llm_invoke_module, "llm", MagicMock(ainvoke=ainvoke))

    result = await llm_chapter_reconsider(
        FULL_TEXT, chapter_return(["功能与设计", "总结"])
    )

    assert result["error_message"] is None
    assert result["reviews"][0]["present"] is True
    prompt = ainvoke.await_args.args[0][0].content
    assert "功能与设计" in prompt
    assert FULL_TEXT in prompt


@pytest.mark.asyncio
async def test_llm_chapter_reconsider_skips_without_full_text(monkeypatch):
    ainvoke = AsyncMock()
    monkeypatch.setattr(llm_invoke_module, "llm", MagicMock(ainvoke=ainvoke))

    result = await llm_chapter_reconsider("", chapter_return(["总结"]))

    ainvoke.assert_not_awaited()
    assert result["reviews"] == []
    assert result["error_message"]


@pytest.mark.asyncio
async def test_llm_chapter_reconsider_skips_without_candidates(monkeypatch):
    ainvoke = AsyncMock()
    monkeypatch.setattr(llm_invoke_module, "llm", MagicMock(ainvoke=ainvoke))

    result = await llm_chapter_reconsider(FULL_TEXT, chapter_return([]))

    ainvoke.assert_not_awaited()
    assert result["reviews"] == []
    assert result["error_message"]


@pytest.mark.asyncio
async def test_llm_chapter_reconsider_handles_exception(monkeypatch):
    monkeypatch.setattr(
        llm_invoke_module,
        "llm",
        MagicMock(ainvoke=AsyncMock(side_effect=RuntimeError("boom"))),
    )

    result = await llm_chapter_reconsider(
        FULL_TEXT, chapter_return(["功能与设计"])
    )

    assert result["reviews"] == []
    assert "boom" in result["error_message"]


# ---------------------------------------------------------------------------
# 图路由与 barrier
# ---------------------------------------------------------------------------

def graph_state(**overrides):
    state = {
        "basic": {"id": 1, "doc_id": 1, "code_id": 1, "topic_id": 1},
        "teacher_config": TEACHER_CONFIG,
        "word_parsing_return": {},
        "full_text": "",
        "ast_validation_return": {},
        "sandbox_validation_return": {},
        "meaning_validation_return": {},
        "output": {},
        "llm_comment": "",
    }
    state.update(overrides)
    return state


def patch_graph_dependencies(monkeypatch, meaning_calls):
    async def fake_meaning(doc, ast, sandbox):
        meaning_calls.append(doc)
        return {}

    async def fake_comment(*args):
        return "测试评语"

    monkeypatch.setattr(agent_service, "llm_meaning_analysis", fake_meaning)
    monkeypatch.setattr(agent_service, "llm_comment_", fake_comment)
    monkeypatch.setattr(
        agent_service, "AST_validation", lambda code_id: {"ast_error": {"count": 0}}
    )
    monkeypatch.setattr(
        agent_service, "sandbox_runing_validation", lambda *args: {}
    )


@pytest.mark.asyncio
async def test_graph_reconsider_recovers_chapter_and_runs_meaning_once(monkeypatch):
    meaning_calls = []
    patch_graph_dependencies(monkeypatch, meaning_calls)

    async def fake_reconsider(full_text, material_doc):
        return {
            "reviews": [
                {"name": "功能与设计", "present": True,
                 "evidence": "2 功能与设计"},
            ],
            "error_message": None,
        }

    monkeypatch.setattr(agent_service, "llm_chapter_reconsider", fake_reconsider)

    result = await agent_service.graph.ainvoke(
        graph_state(
            word_parsing_return=chapter_return(["功能与设计", "总结"]),
            full_text=FULL_TEXT,
        )
    )

    chapter = result["word_parsing_return"][CHAPTER_MISSING]
    assert chapter["count"] == 1
    assert chapter["missing"] == ["总结"]
    assert "系统包含录入" in result["word_parsing_return"]["contents"]["功能与设计"]
    # 权重 10%，max_hits=2，1 个缺失扣 5 分。
    assert result["output"]["score"] == 95
    assert result["output"]["report"]["final_score"] == result["output"]["score"]
    # barrier 生效：语义分析只执行一次，且拿到复核后的契约。
    assert len(meaning_calls) == 1
    assert meaning_calls[0][CHAPTER_MISSING]["count"] == 1
    assert result["llm_comment"] == "测试评语"


@pytest.mark.asyncio
async def test_graph_skips_reconsider_when_no_missing_chapter(monkeypatch):
    meaning_calls = []
    patch_graph_dependencies(monkeypatch, meaning_calls)
    reconsider = AsyncMock()
    monkeypatch.setattr(agent_service, "llm_chapter_reconsider", reconsider)

    result = await agent_service.graph.ainvoke(
        graph_state(
            word_parsing_return=chapter_return([], count=0),
            full_text=FULL_TEXT,
        )
    )

    reconsider.assert_not_awaited()
    assert result["output"]["score"] == 100
    assert len(meaning_calls) == 1


@pytest.mark.asyncio
async def test_graph_skips_reconsider_without_full_text(monkeypatch):
    meaning_calls = []
    patch_graph_dependencies(monkeypatch, meaning_calls)
    reconsider = AsyncMock()
    monkeypatch.setattr(agent_service, "llm_chapter_reconsider", reconsider)

    result = await agent_service.graph.ainvoke(
        graph_state(
            word_parsing_return=chapter_return(["功能与设计", "总结"]),
            full_text="",
        )
    )

    reconsider.assert_not_awaited()
    # count=2 达到 max_hits=2，扣满 10 分。
    assert result["output"]["score"] == 90
    assert len(meaning_calls) == 1


@pytest.mark.asyncio
async def test_graph_reconsider_failure_keeps_score(monkeypatch):
    meaning_calls = []
    patch_graph_dependencies(monkeypatch, meaning_calls)

    async def fake_reconsider(full_text, material_doc):
        return {"reviews": [], "error_message": "模型超时"}

    monkeypatch.setattr(agent_service, "llm_chapter_reconsider", fake_reconsider)

    result = await agent_service.graph.ainvoke(
        graph_state(
            word_parsing_return=chapter_return(["功能与设计", "总结"]),
            full_text=FULL_TEXT,
        )
    )

    chapter = result["word_parsing_return"][CHAPTER_MISSING]
    assert chapter["count"] == 2
    assert chapter["review"]["error_message"] == "模型超时"
    assert result["output"]["score"] == 90
    assert len(meaning_calls) == 1
