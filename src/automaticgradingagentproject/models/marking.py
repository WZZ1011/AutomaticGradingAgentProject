from sqlalchemy import Column, DateTime, Index, Integer, String, Text
from sqlalchemy.sql import func

from automaticgradingagentproject.core.database import Base


class Document(Base):
    __tablename__ = 'document'        

    id = Column(Integer, primary_key=True, autoincrement=True)
    word_doc = Column(Text,nullable=False) 
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


