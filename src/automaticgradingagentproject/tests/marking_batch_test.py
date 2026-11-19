from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import automaticgradingagentproject.core.llm_invoke as llm_invoke_module
from automaticgradingagentproject.core.database import Base
from automaticgradingagentproject.core.enums.meaning_grade_enum import MeaningGradeEnum
from automaticgradingagentproject.core.enums.rule_type_enum import RuleTypeEnum
from automaticgradingagentproject.core.graph_start_imformation import get_basic
from automaticgradingagentproject.core.llm_invoke import (
    llm_meaning_analysis,
    parse_meaning_grade,
)
from automaticgradingagentproject.core.score import score_
from automaticgradingagentproject.models.marking import (
    Code,
    Document,
    LLMComment,
    Rule,
    Topic,
)
from automaticgradingagentproject.schemas.markingschemas import (
    GetMarkingResultsResponse,
)
from automaticgradingagentproject.services import agent_service, marking_service
from automaticgradingagentproject.services.agent_service import (
    get_marking_result,
    marking_batch,
)

TEACHER_CONFIG = {
    "total_score": 100,
    "weights": {
        RuleTypeEnum.FORMAT_ERROR: 10,
        RuleTypeEnum.CHAPTER_MISSING: 10,
        RuleTypeEnum.REPORT_CODE_MATCH: 10,
        RuleTypeEnum.AST_ERROR: 15,
        RuleTypeEnum.RUNNING_NO_RESULT: 20,
        RuleTypeEnum.RUNNING_RESULT_ERROR: 25,
        RuleTypeEnum.MEANING_ANALYSIS: 10,
    },
}


@pytest.mark.asyncio
async def test_marking_batch_normal():
    #Arrange
    db = MagicMock()
    basics=[{
        "id":1,
        "student_name":"test_studnet_name_1",
        "student_id":"test_student_id_1",
        "doc_id":1,
        "code_id":1,
        "topic_id":1
    },
    {
        "id":2,
        "student_name":"test_studnet_name_2",
        "student_id":"test_student_id_2",
        "doc_id":2,
        "code_id":2,
        "topic_id":2
    }]
    teacher_config = TEACHER_CONFIG
    fake_graph_result={
        "basic": {"id":1},
        "teacher_config": {},
        "word_parsing_return": {},
        "ast_validation_return": {},
        "sandbox_validation_return": {},
        "output": {},
        "llm_comment": "1_很好",

    }

    with patch("automaticgradingagentproject.services.agent_service.get_basic",return_value=basics) as gb,\
         patch("automaticgradingagentproject.services.agent_service.get_teacher_config",return_value=teacher_config) as cfg,\
         patch("automaticgradingagentproject.services.agent_service.graph",autospec=True)  as g,\
         patch("automaticgradingagentproject.services.agent_service.LLMCommentDao",autospec=True) as Dao:
        g.ainvoke=AsyncMock(return_value=fake_graph_result)
        repo=Dao.return_value
    #Act
        result = await marking_batch(db, 1)
    #Assert
        assert result== {"ok":"批阅完成",
                         "error_count":0}
        assert g.ainvoke.await_count == 2
        assert repo.update.call_count == 2
        gb.assert_called_once_with(db, 1)
        cfg.assert_called_once_with(db, 1)
        db.commit.assert_called_once()
        db.rollback.assert_not_called()



@pytest.mark.asyncio
async def test_marking_batch_one_error():
    # Arrange
    db = MagicMock()
    basics = [
        {
            "id": 1,
            "student_name": "test_student_name_1",
            "student_id": "test_student_id_1",
            "doc_id": 1,
            "code_id": 1,
            "topic_id": 1,
        },
        {
            "id": 2,
            "student_name": "test_student_name_2",
            "student_id": "test_student_id_2",
            "doc_id": 2,
            "code_id": 2,
            "topic_id": 2,
        },
    ]
    teacher_config = TEACHER_CONFIG
    fake_graph_result = {
        "basic": {"id": 1},
        "teacher_config": {},
        "word_parsing_return": {},
        "ast_validation_return": {},
        "sandbox_validation_return": {},
        "output": {},
        "llm_comment": "1_很好",
    }

    with patch(
        "automaticgradingagentproject.services.agent_service.get_basic",
        return_value=basics,
    ) as gb, patch(
        "automaticgradingagentproject.services.agent_service.get_teacher_config",
        return_value=teacher_config,
    ) as cfg, patch(
        "automaticgradingagentproject.services.agent_service.graph",
        autospec=True,
    ) as g, patch(
        "automaticgradingagentproject.services.agent_service.LLMCommentDao",
        autospec=True,
    ) as Dao:
        # 第一次成功，第二次抛异常
        g.ainvoke = AsyncMock(
            side_effect=[fake_graph_result, RuntimeError("图节点爆炸了")]
        )
        repo = Dao.return_value

        # Act
        result = await marking_batch(db, 1)

        # Assert
        assert result == {"ok": "批阅完成", "error_count": 1}
        assert g.ainvoke.await_count == 2
        assert repo.update.call_count == 1
        gb.assert_called_once_with(db, 1)
        cfg.assert_called_once_with(db, 1)
        db.commit.assert_called_once()
        db.rollback.assert_not_called()

        # 失败条目不写库，保持空评语，便于下次重试
        update_calls = {
            call.args[0]: call.kwargs["comment"]
            for call in repo.update.call_args_list
        }
        assert update_calls == {1: "1_很好"}


@pytest.mark.asyncio
async def test_llm_comment_node_awaits(monkeypatch):
    async def fake_llm_comment(*args):
        return "测试评语"

    monkeypatch.setattr(agent_service, "llm_comment_", fake_llm_comment)

    state = {
        "word_parsing_return": {},
        "ast_validation_return": {},
        "sandbox_validation_return": {},
        "meaning_validation_return": {},
        "output": {"score": 80},
    }
    result = await agent_service.llm_comment(state)
    assert result["llm_comment"] == "测试评语"


def test_get_marking_result_serializes():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            db.add_all([
                Topic(id=1, name="topic1", total_score=100),
                Topic(id=2, name="topic2", total_score=100),
            ])
            db.add_all([
                Document(
                    id=i, word_doc=f"doc{i}", content_hash=f"doc-hash-{i}",
                    user_id=str(i), topic_id=1 if i < 3 else 2,
                )
                for i in (1, 2, 3)
            ])
            db.add_all([
                Code(
                    id=i, source_code=f"code{i}.cpp", content_hash=f"code-hash-{i}",
                    user_id=str(i), topic_id=1 if i < 3 else 2,
                )
                for i in (1, 2, 3)
            ])
            db.add_all([
                LLMComment(
                    student_name="学生A", student_id="1", document_id=1,
                    code_id=1, topic_id=1, comment="评语A",
                ),
                LLMComment(
                    student_name="学生B", student_id="2", document_id=2,
                    code_id=2, topic_id=1, comment="评语B",
                ),
                LLMComment(
                    student_name="学生C", student_id="3", document_id=3,
                    code_id=3, topic_id=2, comment="评语C",
                ),
            ])
            db.commit()

            data = get_marking_result(db, 1, 20, topic_id=1)
            assert data["page"] == 1
            assert data["size"] == 20
            assert data["total"] == 2

            response = GetMarkingResultsResponse(**data)
            assert [item.llm_comment for item in response.items] == ["评语A", "评语B"]

            assert get_marking_result(db, 1, 20)["total"] == 3
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_marking_batch_persists_comment(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'batch.db').as_posix()}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    monkeypatch.setenv("AST_DATABASE_URL", url)

    fixture = (Path(__file__).parent / "AST/fixtures/topic1.cpp").resolve()

    async def fake_llm_comment(*args):
        return "测试评语"

    monkeypatch.setattr(agent_service, "llm_comment_", fake_llm_comment)

    try:
        with Session(engine) as db:
            topic = marking_service.insert_topic(db, "topic1", "d", 100)
            weights = {
                RuleTypeEnum.FORMAT_ERROR.value: 10,
                RuleTypeEnum.CHAPTER_MISSING.value: 10,
                RuleTypeEnum.REPORT_CODE_MATCH.value: 10,
                RuleTypeEnum.AST_ERROR.value: 15,
                RuleTypeEnum.RUNNING_NO_RESULT.value: 20,
                RuleTypeEnum.RUNNING_RESULT_ERROR.value: 25,
                RuleTypeEnum.MEANING_ANALYSIS.value: 10,
            }
            for rule in db.scalars(
                select(Rule).where(Rule.topic_id == topic.id)
            ).all():
                if rule.name in weights:
                    rule.weight = weights[rule.name]

            for rule_name in (
                "topic1.no_std_sort",
                "topic1.require_self_linear_list",
                "topic1.require_self_quick_sort",
                "topic1.require_self_heap_sort",
            ):
                db.add(Rule(
                    name=rule_name, description="ast", weight=0, topic_id=topic.id
                ))

            db.add(Document(
                id=1,
                word_doc="batch-integration.docx",
                content_hash="batch-integration-doc",
                user_id="u1",
                topic_id=topic.id,
            ))
            db.add(Code(
                id=1,
                source_code=str(fixture),
                content_hash="batch-integration",
                user_id="u1",
                topic_id=topic.id,
            ))
            db.add(LLMComment(
                id=1,
                student_name="学生A",
                student_id="u1",
                document_id=1,
                code_id=1,
                topic_id=topic.id,
                comment="",
            ))
            db.add(LLMComment(
                id=2,
                student_name="学生B",
                student_id="u2",
                document_id=2,
                code_id=2,
                topic_id=topic.id,
                comment="已有评语",
            ))
            db.commit()

            result = await marking_batch(db, topic.id)
            assert result == {"ok": "批阅完成", "error_count": 0}

            db.expire_all()
            pending = db.get(LLMComment, 1)
            already = db.get(LLMComment, 2)
            assert pending is not None and pending.comment == "测试评语"
            assert already is not None and already.comment == "已有评语"
    finally:
        engine.dispose()


def test_get_basic_filters_pending_and_topic():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            db.add_all([
                Topic(id=1, name="topic1", total_score=100),
                Topic(id=2, name="topic2", total_score=100),
            ])
            db.add_all([
                Document(
                    id=i, word_doc=f"doc{i}", content_hash=f"doc-hash-{i}",
                    user_id=str(i), topic_id=1 if i < 3 else 2,
                )
                for i in (1, 2, 3)
            ])
            db.add_all([
                Code(
                    id=i, source_code=f"code{i}.cpp", content_hash=f"code-hash-{i}",
                    user_id=str(i), topic_id=1 if i < 3 else 2,
                )
                for i in (1, 2, 3)
            ])
            db.add_all([
                LLMComment(
                    id=1, student_name="A", student_id="1", document_id=1,
                    code_id=1, topic_id=1, comment="",
                ),
                LLMComment(
                    id=2, student_name="B", student_id="2", document_id=2,
                    code_id=2, topic_id=1, comment="已评",
                ),
                LLMComment(
                    id=3, student_name="C", student_id="3", document_id=3,
                    code_id=3, topic_id=2, comment="",
                ),
            ])
            db.commit()

            assert [row["id"] for row in get_basic(db, 1)] == [1]
            assert [row["id"] for row in get_basic(db, 2)] == [3]
            assert get_basic(db, 99) == []
    finally:
        engine.dispose()


def test_score_running_result_error_uses_count():
    weights = {RuleTypeEnum.RUNNING_RESULT_ERROR: 25}
    result = score_(
        weights, 100, {}, {},
        {"running_result_error": {"count": 1, "pass_rate": "50%"}},
        {},
    )
    assert result == 75


def test_score_skips_sandbox_infrastructure_error():
    weights = {RuleTypeEnum.RUNNING_NO_RESULT: 25}
    result = score_(
        weights, 100, {}, {},
        {"running_no_result": {
            "count": 1,
            "error_message": ["docker unavailable"],
        }},
        {},
    )
    assert result == 100


def test_score_applies_running_no_result():
    weights = {RuleTypeEnum.RUNNING_NO_RESULT: 25}
    result = score_(
        weights, 100, {}, {},
        {"running_no_result": {"count": 1, "error_message": []}},
        {},
    )
    assert result == 75


def test_score_meaning_grade_a_no_deduction():
    result = score_(
        {RuleTypeEnum.MEANING_ANALYSIS: 20}, 100, {}, {}, {},
        {"meaning_analysis": {"count": 0, MeaningGradeEnum.A.value: {"count": 1}}},
    )
    assert result == 100


def test_score_meaning_grade_b_deducts_half():
    result = score_(
        {RuleTypeEnum.MEANING_ANALYSIS: 20}, 100, {}, {}, {},
        {"meaning_analysis": {"count": 1, MeaningGradeEnum.B.value: {"count": 1}}},
    )
    assert result == 90


def test_score_meaning_grade_c_deducts_all():
    result = score_(
        {RuleTypeEnum.MEANING_ANALYSIS: 20}, 100, {}, {}, {},
        {"meaning_analysis": {"count": 2, MeaningGradeEnum.C.value: {"count": 1}}},
    )
    assert result == 80


def test_score_skips_meaning_error():
    result = score_(
        {RuleTypeEnum.MEANING_ANALYSIS: 20}, 100, {}, {}, {},
        {"meaning_analysis": {"count": 0, "error_message": "缺少文档内容"}},
    )
    assert result == 100


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a", MeaningGradeEnum.A),
        (" A ", MeaningGradeEnum.A),
        ("A档", MeaningGradeEnum.A),
        ("B+", MeaningGradeEnum.B),
        ("答案：C", MeaningGradeEnum.C),
        ("c", MeaningGradeEnum.C),
    ],
)
def test_parse_meaning_grade(text, expected):
    assert parse_meaning_grade(text) is expected


@pytest.mark.parametrize("text", [None, "", "无法判断", "unknown", "AB"])
def test_parse_meaning_grade_rejects_invalid(text):
    assert parse_meaning_grade(text) is None


@pytest.mark.asyncio
async def test_llm_meaning_analysis_without_contents_skips_llm(monkeypatch):
    ainvoke = AsyncMock()
    monkeypatch.setattr(
        llm_invoke_module, "llm", MagicMock(ainvoke=ainvoke)
    )

    result = await llm_meaning_analysis({}, {}, {})

    ainvoke.assert_not_awaited()
    payload = result[RuleTypeEnum.MEANING_ANALYSIS.value]
    assert payload["count"] == 0
    assert payload["error_message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "grade", "severity"),
    [
        ("a", MeaningGradeEnum.A, 0),
        ("B档", MeaningGradeEnum.B, 1),
        ("C", MeaningGradeEnum.C, 2),
    ],
)
async def test_llm_meaning_analysis_parses_grade(monkeypatch, content, grade,
                                                 severity):
    monkeypatch.setattr(
        llm_invoke_module,
        "llm",
        MagicMock(ainvoke=AsyncMock(return_value=MagicMock(content=content))),
    )

    result = await llm_meaning_analysis(
        {"contents": {"总结": "这是总结"}}, {}, {}
    )

    payload = result[RuleTypeEnum.MEANING_ANALYSIS.value]
    assert payload["count"] == severity
    assert payload[grade.value] == {"count": 1}
    assert "error_message" not in payload


@pytest.mark.asyncio
async def test_llm_meaning_analysis_invalid_output(monkeypatch):
    monkeypatch.setattr(
        llm_invoke_module,
        "llm",
        MagicMock(ainvoke=AsyncMock(return_value=MagicMock(content="无法判断"))),
    )

    result = await llm_meaning_analysis(
        {"contents": {"总结": "这是总结"}}, {}, {}
    )

    payload = result[RuleTypeEnum.MEANING_ANALYSIS.value]
    assert payload["count"] == 0
    assert payload["error_message"]


@pytest.mark.asyncio
async def test_graph_runs_meaning_analysis(monkeypatch):
    engine_calls = []
    meaning_calls = []

    async def fake_meaning(doc, ast, sandbox):
        meaning_calls.append(doc)
        return {
            RuleTypeEnum.MEANING_ANALYSIS.value: {
                "count": 1,
                MeaningGradeEnum.B.value: {"count": 1},
            }
        }

    async def fake_comment(*args):
        return "测试评语"

    def fake_engine(doc_id, code_id):
        engine_calls.append((doc_id, code_id))
        return {
            "full_text": "全文",
            "word_parsing_return": {"contents": {"总结": "这是总结"}},
        }

    monkeypatch.setattr(agent_service, "llm_meaning_analysis", fake_meaning)
    monkeypatch.setattr(agent_service, "llm_comment_", fake_comment)
    monkeypatch.setattr(
        agent_service, "AST_validation", lambda code_id: {"ast_error": {"count": 0}}
    )
    monkeypatch.setattr(
        agent_service, "sandbox_runing_validation",
        lambda code_id, test_cases, expected: {},
    )
    monkeypatch.setattr(agent_service, "doc_parsing_engine", fake_engine)

    state = {
        "basic": {"id": 1, "doc_id": 1, "code_id": 1, "topic_id": 1},
        "teacher_config": TEACHER_CONFIG,
        "word_parsing_return": {"contents": {"总结": "这是总结"}},
        "full_text": "",
        "ast_validation_return": {},
        "sandbox_validation_return": {},
        "meaning_validation_return": {},
        "output": {},
        "llm_comment": "",
    }
    result = await agent_service.graph.ainvoke(state)

    payload = result["meaning_validation_return"][
        RuleTypeEnum.MEANING_ANALYSIS.value
    ]
    assert payload["count"] == 1
    assert payload[MeaningGradeEnum.B.value] == {"count": 1}
    # 语义权重 10%，B 档扣一半 → 95 分
    assert result["output"]["score"] == 95
    assert result["llm_comment"] == "测试评语"
    assert engine_calls == [(1, 1)]
    assert meaning_calls == [
        {
            "full_text": "全文",
            "word_parsing_return": {"contents": {"总结": "这是总结"}},
        }
    ]