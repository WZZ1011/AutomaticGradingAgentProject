from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func

Base = declarative_base() #当需要创建其他py 的model时将其放入db.py，然后不同py的model统一导入db.py内的Base

class Student(Base):
    __tablename__ = 'student'        # 表名，必须写

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True,nullable=False) 
    student_id = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

class Teacher(Base):
    __tablename__ = 'teacher'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True,nullable=False) 
    use_id = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now())