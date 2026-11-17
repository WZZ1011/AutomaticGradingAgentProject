from sqlalchemy import select
from sqlalchemy.orm import Session

from automaticgradingagentproject.core import exception
from automaticgradingagentproject.core.enums.rule_type_enum import RuleTypeEnum
from automaticgradingagentproject.core.rule_types import (
    ALLOWED_RULE_TYPE_VALUES,
    validate_rule_weights,
)
from automaticgradingagentproject.models.marking import Code, Document, LLMComment, Rule, Topic
from automaticgradingagentproject.repositories import markingdao

#获取老师配置，那么需要获取权重和总分

def get_teacher_config(db:Session,topic_id:int):
    """_summary_

    Args:
        db (Session): _description_
        topic_id (int): _description_

    Raises:
        exception.TopicNotFound: _description_

    Returns:
        _type_: _description_
    """
    topic_repo = markingdao.TopicDao(db)
    topic = topic_repo.get(topic_id)
    if topic is None:
        raise exception.TopicNotFound(topic_id)

    # 只读取教师可配置的顶层类型，系统 AST 行不参与评分权重。
    rows = db.execute(
        select(Rule.name, Rule.weight).where(
            Rule.topic_id == topic.id,
            Rule.name.in_(ALLOWED_RULE_TYPE_VALUES),
        )
    ).all()
    weights: dict[RuleTypeEnum, int] = {
        RuleTypeEnum(name): weight for name, weight in rows
    }

    try:
        validate_rule_weights(weights)
    except ValueError as exc:
        raise ValueError(f"专题{topic.id}：{exc}") from exc

    return {
        "total_score": topic.total_score,
        "weights": weights,
    }


def get_basic(db: Session, topic_id: int):
    """获取指定专题下尚未批阅的记录（comment 为空）。"""
    stmt = (
        select(
            LLMComment.id,
            LLMComment.student_name,
            LLMComment.student_id,
            LLMComment.document_id,
            LLMComment.code_id,
            LLMComment.topic_id,
        )
        .where(
            LLMComment.topic_id == topic_id,
            LLMComment.comment == "",
            # 关联的作业仍然存在，避免已删除作业的孤儿记录进入批改队列。
            LLMComment.document_id.in_(select(Document.id)),
            LLMComment.code_id.in_(select(Code.id)),
            LLMComment.topic_id.in_(select(Topic.id)),
        )
        .order_by(LLMComment.id)
    )
    basics_tuple = db.execute(stmt).all()
    basics = []
    for basic in basics_tuple:
        basics.append({
              "id":basic[0],
              "student_name":basic[1],
              "student_id":basic[2],
              "doc_id":basic[3],
              "code_id":basic[4],
              "topic_id":basic[5]
        })

    return basics

    