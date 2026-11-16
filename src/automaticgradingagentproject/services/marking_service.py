from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from automaticgradingagentproject.core.enums.rule_type_enum import RuleTypeEnum
from automaticgradingagentproject.core.exception import RuleNotFound, TopicNotFound
from automaticgradingagentproject.core.hash import _sha256
from automaticgradingagentproject.core.rule_types import (
    ALLOWED_RULE_TYPE_VALUES,
    RULE_TYPE_META,
    SYSTEM_RULE_NAMES,
    normalize_rule_type,
    validate_rule_weights,
)
from automaticgradingagentproject.models import marking, user
from automaticgradingagentproject.repositories import markingdao


def upload_document_and_code(document_path:str,source_code_path:str,user_id:str,topic_id:int,db:Session):
    """上传word文档和源代码

    Args:
        document (str): word文档的绝对路径
        source_code (str): 源代码的绝对路径
        user_id (str): 使用者id/学号
        topic_id (int): 对应的专题
        db (Session): 数据库会话

    Returns:
        doc.id(int) code.id(int): 文档id和源码id
    """
    code_hash = _sha256(source_code_path)
    doc_hash = _sha256(document_path)
    
    doc_repo = markingdao.DocumentDao(db)
    code_repo = markingdao.CodeDao(db)

    doc = marking.Document(
     word_doc=document_path,
     content_hash=doc_hash,
     user_id=user_id,
     topic_id=topic_id,
            )
    code = marking.Code(
                source_code=source_code_path,
                content_hash=code_hash,
                user_id=user_id,
                topic_id=topic_id,
            )
    doc_repo.add(doc)
    code_repo.add(code)
    try:
            db.commit()
          
    except IntegrityError:
            db.rollback()
            # 处理重复:可以是返回已存在记录,或抛出自定义异常
            raise

    #往LLMComment里面放入必要的数据，为批阅核心函数做准备
    stmt=select(user.User.name).where(user.User.user_id==user_id)
    student_name=db.execute(stmt).scalar()
    llm_comment_repo=markingdao.LLMCommentDao(db)
    new_LLMComment=marking.LLMComment(
         student_name=student_name,
         student_id=user_id,
         document_id=doc.id,
         code_id=code.id,
         topic_id=topic_id,
         comment=""
    )
    llm_comment_repo.add(new_LLMComment)
    try:            
        db.commit()
    except IntegrityError:
         db.rollback()
         raise 
    return {"doc_id":doc.id,"code_id":code.id}


def insert_topic(db:Session,name:str,description:str|None=None,total_score:int|None=None):
    """添加一个专题

    Args:
        db (Session): 数据库会话
        name (str): 专题名称_
        description (str | None, optional): 专题描述. Defaults to None.
        total_score (int | None, optional): 专题总分. Defaults to None.

    Returns:
        topic.id(int): 创建的专题的id
    """
    topic_repo = markingdao.TopicDao(db)
    topic = marking.Topic(
        name=name,
        description=description,
        total_score=total_score,
    )
    try:
        # add() 会 flush，因此可以拿到自增 id，并在同一事务里生成 6 行固定规则。
        topic = topic_repo.add(topic)
        rule_repo = markingdao.RuleDao(db)
        for rule_type in RuleTypeEnum:
            rule_repo.add(
                marking.Rule(
                    name=rule_type.value,
                    description=str(RULE_TYPE_META[rule_type]["description"]),
                    weight=0,
                    topic_id=topic.id,
                )
            )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise
    return topic

def insert_topic_rule(name:str,description:str,weight:int,topic_id:int,db:Session):
    """在一个专题下创建一条规则

    Args:
        name (str): 规则名
        description (str): 规则描述
        weight (int): 权重
        topic_id (int): 对应的专题id
        db (Session): 数据库会话

    Returns:
        rule_id(int): 规则id
    """
    rule_name = normalize_rule_type(name)
    if not 0 <= weight <= 100:
        raise ValueError("规则权重必须是 0-100 的百分数")

    if markingdao.TopicDao(db).get(topic_id) is None:
        raise TopicNotFound(topic_id)

    existing_rule_id = db.scalar(
        select(marking.Rule.id).where(
            marking.Rule.topic_id == topic_id,
            marking.Rule.name == rule_name,
        )
    )
    if existing_rule_id is not None:
        raise ValueError(f"专题{topic_id}已存在规则 {rule_name}，不能重复配置")

    rule_repo = markingdao.RuleDao(db)
    rule = marking.Rule(
        name=rule_name,
        description=description,
        weight=weight,
        topic_id=topic_id,
    )

    rule_repo.add(rule)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise
    return rule




def update_topic(db:Session, topic_id: int, **fields) -> marking.Topic:
    """更新专题的某个字段

    Args:
        db (Session): 数据库会话
        topic_id (int): 专题id

    Raises:
        TopicNotFound: 异常（专题没有找到）

    Returns:
        marking.Topic: 返回整个专题对象
    """
    topic_repo = markingdao.TopicDao(db)
    topic = topic_repo.update(topic_id, **fields)
    if topic is None:
        raise TopicNotFound(topic_id)
    db.commit()
    return topic



def update_rule( db:Session,rule_id: int, **fields) -> marking.Rule:
    """更新一条规则（根据rule_id）

    Args:
        db (Session): 数据库会话
        rule_id (int): 规则id

    Raises:
        RuleNotFound: 异常（数据库内没有对应规则）

    Returns:
        marking.Rule: 返回创建好的规则对象
    """
    if fields.get("weight") is not None and not 0 <= fields["weight"] <= 100:
        raise ValueError("规则权重必须是 0-100 的百分数")

    rule_repo = markingdao.RuleDao(db)
    rule = rule_repo.update(rule_id, **fields)
    if rule is None:
        raise RuleNotFound(rule_id)

    if fields.get("weight") is not None:
        topic_total = db.scalar(
            select(func.coalesce(func.sum(marking.Rule.weight), 0)).where(
                marking.Rule.topic_id == rule.topic_id,
                marking.Rule.name.in_(ALLOWED_RULE_TYPE_VALUES),
            )
        )
        if topic_total is not None and topic_total > 100:
            db.rollback()
            raise ValueError(
                f"专题{rule.topic_id}的评分规则权重合计不能超过 100，当前为 {topic_total}"
            )

    db.commit()
    return rule


def validate_topic_weights(db: Session, topic_id: int) -> dict[RuleTypeEnum, int]:
    """校验专题的 6 项评分权重是否完整且合计为 100。

    返回以 RuleTypeEnum 为 key 的权重字典，供评分阶段使用。
    编辑过程中允许合计小于 100，但开始批阅前必须通过本校验。
    """
    if markingdao.TopicDao(db).get(topic_id) is None:
        raise TopicNotFound(topic_id)

    rows = db.execute(
        select(marking.Rule.name, marking.Rule.weight).where(
            marking.Rule.topic_id == topic_id,
            marking.Rule.name.in_(ALLOWED_RULE_TYPE_VALUES),
        )
    ).all()

    weights: dict[RuleTypeEnum, int] = {}
    for name, weight in rows:
        weights[RuleTypeEnum(name)] = weight

    try:
        validate_rule_weights(weights)
    except ValueError as exc:
        raise ValueError(f"专题{topic_id}：{exc}") from exc
    return weights



def get_document(db:Session,doc_id:int):
    """获取数据库内的文档和源代码（根据id）

    Args:
        db (Session): 数据库会话
        doc_id (int): 文档id

    Raises:
        ValueError: 文档没有找到

    Returns:
        doc(ORM对象): 数据库里面找到的实体 
    """
    doc_repo = markingdao.DocumentDao(db)

    doc = doc_repo.get(doc_id)

    if doc is None :
       raise ValueError(f"文档{doc_id}  没有找到")
    return doc



def get_code(db:Session,code_id:int):
     code_repo = markingdao.CodeDao(db)

     code=code_repo.get(code_id)
     if code is None:
          raise ValueError(f"源码{code_id} 没有找到")
     return code


def get_topics(db:Session,page: int = 1, size: int = 20):
    """获取专题列表（分页）

    Args:
        db (Session): 数据库会话
        page (int, optional): 页数. Defaults to 1.
        size (int, optional): 每页条数. Defaults to 20.

    Returns:
        topics(list): 专题列表 
    """
    topic_repo = markingdao.TopicDao(db)
    total = topic_repo.count()
    topics = topic_repo.list_all(page,size,order_by=marking.Topic.id.desc())
    return {
         "total": total,
         "page":page,
         "size":size,
         "items":topics
    }

def get_page_doc(db:Session,page:int = 1,size:int =20):
     doc_repo = markingdao.DocumentDao(db)
     total = doc_repo.count()
     docs = doc_repo.list_all(page,size)
     return {
            "total": total,
            "page":page,
            "size":size,
            "items":docs
            }
def get_page_code(db:Session,page:int = 1,size:int =20):
     code_repo = markingdao.CodeDao(db)
     total = code_repo.count()
     codes = code_repo.list_all(page,size)
     return {
            "total": total,
            "page":page,
            "size":size,
            "items":codes
            }
def get_topic_all_rule(db: Session, page: int = 1, size: int = 20,
                       topic_id: int | None = None):
    """获取教师可配置的评分规则列表（分页）。

    只返回 RuleTypeEnum 的 6 个顶层类型；系统 AST 行不暴露给教师。
    指定 topic_id 时只返回该专题的配置。

    Args:
        db (Session): 数据库会话
        page (int, optional): 页数. Defaults to 1.
        size (int, optional): 每页条数. Defaults to 20.
        topic_id (int | None, optional): 专题 id；为空时返回全部专题的评分规则.

    Returns:
        dict: total/page/size/items
    """
    stmt = select(marking.Rule).where(
        marking.Rule.name.in_(ALLOWED_RULE_TYPE_VALUES)
    )
    if topic_id is not None:
        stmt = stmt.where(marking.Rule.topic_id == topic_id)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rules = list(
        db.scalars(
            stmt.order_by(marking.Rule.topic_id, marking.Rule.id)
            .offset((page - 1) * size)
            .limit(size)
        ).all()
    )
    return {
        "total": total,
        "page": page,
        "size": size,
        "items": rules,
    }

def delete_document(db:Session,doc_id:int)->dict:
    """删除一个文档（根据id）

    Args:
        db (Session): 数据库会话
        doc_id (int): 要删除的文档的id

    Raises:
        ValueError: 数据库里没有找打对应文档

    Returns:
        (dict): 删除是否成功
    """
    doc_repo = markingdao.DocumentDao(db)
    doc = doc_repo.get(doc_id)
    if doc is  None:
        raise ValueError(f"文档{doc_id} 没有找到")
    doc_repo.delete(doc)
    # 当前模型没有外键级联，显式清理引用该文档的批阅结果。
    db.execute(
        delete(marking.LLMComment).where(marking.LLMComment.document_id == doc_id)
    )
    db.commit()
    return {"ok":"true"}




def delete_code(db:Session,code_id:int)->dict:
    """删除一个源代码文件

    Args:
        db (Session): 数据库会话
        code_id (int): 源码文件id

    Raises:
        ValueError: 数据库没有找到对应源码文件

    Returns:
        (dict): 删除的源码文件是否成功
    """
    code_repo = markingdao.CodeDao(db)
    code = code_repo.get(code_id)
    if code is None:
        raise ValueError(f"源码{code_id} 没有找打")
    code_repo.delete(code)
    # 当前模型没有外键级联，显式清理引用该源码的批阅结果。
    db.execute(
        delete(marking.LLMComment).where(marking.LLMComment.code_id == code_id)
    )
    db.commit()
    return {"ok":"true"}




def delete_topic_rule(rule_id:int,db:Session):
    """删除一个专题下的规则（需要完整的规则对象）

    Args:
        rule_id(int): 要删除的规则id
        db (Session): 数据库会话

    Returns:
        rule.id: 
    """
    rule_repo = markingdao.RuleDao(db)
    
    rule = rule_repo.get(rule_id)
    if rule is None:
          raise ValueError(f"rule{rule_id} not found")
    if rule.name in SYSTEM_RULE_NAMES:
        raise ValueError(f"规则 {rule.name} 是系统固定规则，不能删除")
    rule_repo.delete(rule)
    try:
             db.commit()
    except IntegrityError:
             db.rollback()
             raise
    return {"ok":"true"}




def delete_topic(db:Session,topic_id:int)->dict:
    """删除一个专题（根据id）

    Args:
        db (Session): 数据库会话
        topic_id (int): 删除的专题的id

    Raises:
        ValueError: 数据库内没有找到这个专题

    Returns:
        dict: 表示删除成功
    """
    topic_repo=markingdao.TopicDao(db)
    topic = topic_repo.get(topic_id)
    if topic is None:
        raise ValueError(f"topic {topic_id} not found")

    # 当前模型没有外键级联，显式清理该专题下的全部规则（含系统 AST 行）。
    rule_repo = markingdao.RuleDao(db)
    rules = list(
        db.scalars(
            select(marking.Rule).where(marking.Rule.topic_id == topic_id)
        ).all()
    )
    for rule in rules:
        rule_repo.delete(rule)

    # 同样显式清理该专题下的批阅结果。
    db.execute(
        delete(marking.LLMComment).where(marking.LLMComment.topic_id == topic_id)
    )

    topic_repo.delete(topic)
    try:
         db.commit()
    except IntegrityError:
         db.rollback()
         raise
    return {"ok":"true"}

