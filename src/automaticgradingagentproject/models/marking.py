from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from automaticgradingagentproject.core.database import Base
from automaticgradingagentproject.core.rule_types import SYSTEM_RULE_NAMES

# 数据库只允许系统定义的规则名：顶层评分类型 + AstRuleEnum 的名称/值。
# AST 模块仍通过 Rule.name 读取规则，因此 AST 行必须继续允许写入。
_RULE_NAME_CHECK_SQL = "name IN ({})".format(
    ", ".join(f"'{name}'" for name in sorted(SYSTEM_RULE_NAMES))
)


class Document(Base):
    __tablename__ = 'document'        

    id = Column(Integer, primary_key=True, autoincrement=True)
    word_doc = Column(Text,nullable=False)
    file_name = Column(String(255), nullable=True)  # 上传时的原始文件名；历史行为 NULL
    content_hash = Column(String(64), unique=True, nullable=False)  # 去重靠 hash
    user_id = Column(String, index=True,nullable=False)
    topic_id = Column(Integer,index=True,nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    __table_args__ = (
        Index('idx_document_user_topic', 'user_id', 'topic_id'),
    )

class Code(Base):
    __tablename__ = 'source_code'        

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_code = Column(Text,nullable=False)
    file_name = Column(String(255), nullable=True)  # 上传时的原始文件名；历史行为 NULL
    content_hash = Column(String(64), unique=True, nullable=False)  # 去重靠 hash
    user_id = Column(String, index=True, nullable=False)
    topic_id =Column(Integer,index=True,nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    __table_args__ = (
        Index('idx_code_user_topic', 'user_id', 'topic_id'),
    )

class Topic(Base):
    __tablename__ = 'topic'        

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True,nullable=False) 
    description = Column(Text, nullable=True)
    total_score = Column(Integer,nullable=False,default=0, server_default='0')
    created_at = Column(DateTime, server_default=func.now())

class Rule(Base):
    __tablename__ = 'rule'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String,nullable=False)
    description = Column(Text, nullable=True)
    weight = Column(Integer,nullable=False)
    topic_id = Column(Integer,index=True,nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("topic_id", "name", name="uq_rule_topic_name"),
        CheckConstraint(
            "weight >= 0 AND weight <= 100",
            name="ck_rule_weight_range",
        ),
        CheckConstraint(_RULE_NAME_CHECK_SQL, name="ck_rule_name_allowed"),
    )


class LLMComment(Base):
    __tablename__= 'llm_comment'

    id=Column(Integer,primary_key=True,autoincrement=True)
    student_name=Column(String,nullable=False)
    student_id=Column(String,nullable=False)
    document_id=Column(Integer,nullable=False)
    code_id=Column(Integer,nullable=False)
    topic_id=Column(Integer,nullable=False)
    comment=Column(String,nullable=False)

    @property
    def llm_comment(self) -> str:
        """API 响应字段名；数据库列仍为 comment。"""
        return self.comment


