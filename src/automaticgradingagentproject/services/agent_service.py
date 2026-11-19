import logging

from langgraph.graph import END, START, StateGraph
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typing_extensions import TypedDict

from automaticgradingagentproject.core.ast_validation.service import AST_validation
from automaticgradingagentproject.core.chapter_reconsider import (
    apply_chapter_reviews,
)
from automaticgradingagentproject.core.graph_start_imformation import (
    get_basic,
    get_teacher_config,
)
from automaticgradingagentproject.core.llm_invoke import (
    llm_chapter_reconsider,
    llm_comment_,
    llm_meaning_analysis,
)
from automaticgradingagentproject.core.sandbox.validation_service import (
    sandbox_runing_validation,
)
from automaticgradingagentproject.core.score import score_report
from automaticgradingagentproject.models.marking import (
    Code,
    Document,
    LLMComment,
    Topic,
)
from automaticgradingagentproject.repositories.markingdao import LLMCommentDao

logger=logging.getLogger(__name__)

class State(TypedDict):
    basic:dict
    word_parsing_return:dict
    full_text:str
    ast_validation_return:dict
    sandbox_validation_return:dict
    meaning_validation_return:dict
    teacher_config:dict
    output:dict
    llm_comment:str



def doc_parsing(state:State):
    # TODO: 解析 Word 后返回 {"word_parsing_return": {...}, "full_text": "..."}；
    # chapter_missing 需同时提供 count 和 missing（规范章节名）。
    pass


def route_after_doc_parsing(state:State) -> str:
    """只有存在缺失章节且有全文时才进入 LLM 复核。"""
    word_parsing_return = state.get("word_parsing_return") or {}
    chapter = word_parsing_return.get("chapter_missing")
    if not isinstance(chapter, dict):
        return "chapter_review_done"

    count = chapter.get("count")
    missing = chapter.get("missing")
    full_text = state.get("full_text") or ""
    if (
        isinstance(count, int)
        and not isinstance(count, bool)
        and count > 0
        and isinstance(missing, list)
        and missing
        and full_text.strip()
    ):
        return "llm_reconsider"
    return "chapter_review_done"


async def llm_reconsider(state:State) -> dict:
    material_doc = state.get("word_parsing_return") or {}
    full_text = state.get("full_text") or ""
    try:
        result = await llm_chapter_reconsider(full_text, material_doc)
        patched = apply_chapter_reviews(
            material_doc,
            result.get("reviews") or [],
            full_text,
            error_message=result.get("error_message"),
        )
    except Exception:
        # 复核只是补正逻辑，任何意外都不能让整条批阅失败。
        logger.exception("章节复核失败，保持解析器原判")
        return {"word_parsing_return": material_doc}
    return {"word_parsing_return": patched}


def chapter_review_done(state:State) -> dict:
    """文档分支的汇合点：跳过复核时也走到这里，供 barrier 等待。"""
    return {}


def sandbox_running_validation(state:State):
    code_id=state["basic"]["code_id"]
    answer=sandbox_runing_validation(code_id,[],[])
    # 并行节点只返回自己更新的 key，避免同一 superstep 写入相同 state key。
    return {"sandbox_validation_return": answer}
def ast_validation(state:State):
    code_id=state["basic"]["code_id"]
    answer=AST_validation(code_id)
    return {"ast_validation_return": answer}
async def meaning_analysis(state:State):
    answer = await llm_meaning_analysis(
        state["word_parsing_return"],
        state["ast_validation_return"],
        state["sandbox_validation_return"],
    )
    return {"meaning_validation_return": answer}
def scoring(state:State):
    report = score_report(
        state["teacher_config"]["weights"], state["teacher_config"]["total_score"],
        state["word_parsing_return"], state["ast_validation_return"],
        state["sandbox_validation_return"], state["meaning_validation_return"],
    )
    return {"output": {"score": report["final_score"], "report": report}}
async def llm_comment(state:State):
    output = state.get("output") or {}
    answer=await llm_comment_(
        state.get("word_parsing_return") or {},
        state.get("ast_validation_return") or {},
        state.get("sandbox_validation_return") or {},
        state.get("meaning_validation_return") or {},
        output.get("score", 0),
        output.get("report"),
    )
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("LLM 未返回有效评语")
    return {"llm_comment": answer}
builder=StateGraph(State)

builder.add_node("doc_parsing",doc_parsing)
builder.add_node("sandbox_running_validation",sandbox_running_validation)
builder.add_node("ast_validation",ast_validation)
builder.add_node("llm_reconsider",llm_reconsider)
builder.add_node("chapter_review_done",chapter_review_done)
builder.add_node("meaning_analysis",meaning_analysis)
builder.add_node("scoring",scoring)
builder.add_node("llm_comment",llm_comment)
builder.add_edge(START,"doc_parsing")
builder.add_edge(START,"sandbox_running_validation")
builder.add_edge(START,"ast_validation")
# 文档分支：有缺失章节且存在全文时先复核，否则直接汇合。
builder.add_conditional_edges(
    "doc_parsing",
    route_after_doc_parsing,
    {
        "llm_reconsider": "llm_reconsider",
        "chapter_review_done": "chapter_review_done",
    },
)
builder.add_edge("llm_reconsider","chapter_review_done")
# barrier：等待文档分支、沙箱、AST 三路全部完成，避免复核被绕过或语义分析提前执行。
builder.add_edge(
    ["sandbox_running_validation","ast_validation","chapter_review_done"],
    "meaning_analysis",
)
builder.add_edge("meaning_analysis","scoring")
builder.add_edge("scoring","llm_comment")
builder.add_edge("llm_comment",END)

graph = builder.compile()



async def marking_batch(db:Session, topic_id:int):
    basics=get_basic(db,topic_id)
    if not basics:
        return {"ok": "批阅完成", "error_count": 0}

    # 同一专题共用一份配置；配置不完整时直接抛出，由 API 层转成 400。
    teacher_config=get_teacher_config(db,topic_id)

    error_count=0
    results=[]
    for basic in basics:
        try:
            result = await graph.ainvoke({
        "basic": basic,
        "teacher_config": teacher_config,
        "word_parsing_return": {},
        "full_text": "",
        "ast_validation_return": {},
        "sandbox_validation_return": {},
        "meaning_validation_return": {},
        "output": {},
        "llm_comment": "",
        })
            results.append({
                "id":basic["id"], #这里的id不从result的basic key取，保证来源的确定性
                "llm_comment":result["llm_comment"]
                })
        except Exception:    #调用graph.ainvoke(),图的任意节点都可能抛异常
            # 不写兜底文案，保持 comment 为空，下次点击可以重试。
            error_count=error_count+1
            logger.exception("批阅失败，basic_id=%s",basic["id"])
    try:
        llm_comment_repo = LLMCommentDao(db)
        for result in results:
            llm_comment_repo.update(result["id"], comment=result["llm_comment"])
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"ok": "批阅完成", "error_count": error_count}
   

def get_marking_result(db: Session, page: int, size: int,
                       topic_id: int | None = None):

    # 只返回关联作业仍然存在的批阅结果，避免删除文档/源码后出现孤儿记录。
    exists_filters = (
        LLMComment.document_id.in_(select(Document.id)),
        LLMComment.code_id.in_(select(Code.id)),
        LLMComment.topic_id.in_(select(Topic.id)),
    )
    stmt = select(LLMComment).where(*exists_filters)
    count_stmt = (
        select(func.count()).select_from(LLMComment).where(*exists_filters)
    )
    if topic_id is not None:
        stmt = stmt.where(LLMComment.topic_id == topic_id)
        count_stmt = count_stmt.where(LLMComment.topic_id == topic_id)

    items = list(
        db.scalars(
            stmt.order_by(LLMComment.id)
            .offset((page - 1) * size)
            .limit(size)    
        ).all()
    )
    total = db.scalar(count_stmt) or 0
    return {
        "total": total,
        "page": page,
        "size": size,
        "items": items,
    }



    






