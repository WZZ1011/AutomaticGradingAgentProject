import logging

from langgraph.graph import END, START, StateGraph
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typing_extensions import TypedDict

from automaticgradingagentproject.core.ast_validation.service import AST_validation
from automaticgradingagentproject.core.doc_parsing import doc_parsing as doc_parsing_engine
from automaticgradingagentproject.core.graph_start_imformation import (
    get_basic,
    get_teacher_config,
)
from automaticgradingagentproject.core.llm_invoke import (
    llm_comment_,
    llm_meaning_analysis,
)
from automaticgradingagentproject.core.sandbox.validation_service import (
    sandbox_runing_validation,
)
from automaticgradingagentproject.core.score import score_
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
    answer = doc_parsing_engine(
        state["basic"]["doc_id"], state["basic"]["code_id"]
    )
    return {
        "word_parsing_return": answer["word_parsing_return"],
        "full_text": answer["full_text"],
    }
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
    material = {
        "full_text": state.get("full_text", ""),
        "word_parsing_return": state["word_parsing_return"],
    }
    answer = await llm_meaning_analysis(
        material,
        state["ast_validation_return"],
        state["sandbox_validation_return"],
    )
    return {"meaning_validation_return": answer}
def scoring(state:State):
    answer = score_(state["teacher_config"]["weights"],state["teacher_config"]["total_score"],
                    state["word_parsing_return"],state["ast_validation_return"],
                    state["sandbox_validation_return"],state["meaning_validation_return"])
    return {"output": {"score": answer}}
async def llm_comment(state:State):
    answer=await llm_comment_(state["word_parsing_return"],state["ast_validation_return"],
                              state["sandbox_validation_return"],state["meaning_validation_return"],
                              state["output"]["score"])
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("LLM 未返回有效评语")
    return {"llm_comment": answer}
builder=StateGraph(State)

builder.add_node("doc_parsing",doc_parsing)
builder.add_node("sandbox_running_validation",sandbox_running_validation)
builder.add_node("ast_validation",ast_validation)
builder.add_node("meaning_analysis",meaning_analysis)
builder.add_node("scoring",scoring)
builder.add_node("llm_comment",llm_comment)
builder.add_edge(START,"doc_parsing")
builder.add_edge(START,"sandbox_running_validation")
builder.add_edge(START,"ast_validation")
# 三个校验模块全部完成后，语义模块才能拿到 doc_parsing 的 contents。
builder.add_edge("doc_parsing","meaning_analysis")
builder.add_edge("sandbox_running_validation","meaning_analysis")
builder.add_edge("ast_validation","meaning_analysis")
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



    






