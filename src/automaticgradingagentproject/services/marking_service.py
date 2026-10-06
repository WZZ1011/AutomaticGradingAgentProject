from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from automaticgradingagentproject.core.exception import RuleNotFound, TopicNotFound
from automaticgradingagentproject.core.hash import _sha256
from automaticgradingagentproject.models import marking
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
        name = name,
        description = description,
        total_score = total_score
    )
    topic = topic_repo.add(topic)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
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
    rule_repo = markingdao.RuleDao(db)

    rule = marking.Rule(
         name = name,
         description = description,
         weight = weight,
         topic_id = topic_id
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
    rule_repo = markingdao.RuleDao(db)
    rule = rule_repo.update(rule_id, **fields)
    if rule is None:
        raise RuleNotFound(rule_id)
    db.commit()
    return rule



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
def get_topic_all_rule(db:Session,page: int = 1, size: int = 20):
    """获取规则列表（分页）

    Args:
        db (Session): 数据库会话
        page (int, optional): 页数. Defaults to 1.
        size (int, optional): 每页条数. Defaults to 20.

    Returns:
        _type_: _description_
    """
    rule_repo = markingdao.RuleDao(db)
    total = rule_repo.count()
    rules = rule_repo.list_all(page,size,marking.Rule.topic_id.desc())
    return {
             "total": total,
             "page":page,
             "size":size,
             "items":rules
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
    topic_repo.delete(topic)
    try:
         db.commit()
    except IntegrityError:
         db.rollback()
    return {"ok":"true"}

