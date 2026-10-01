from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.sql import func

from automaticgradingagentproject.core.database import Base


class User(Base):
    __tablename__ = 'user'        # 表名，必须写

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True,nullable=False) 
    user_id = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

