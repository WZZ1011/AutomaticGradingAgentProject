from sqlalchemy import func, select
from sqlalchemy.orm import Session

from automaticgradingagentproject.models.user import Base


class BaseRepository[ModelType:Base]: # pyright: ignore[reportInvalidTypeForm]
    model : type[ModelType]  #表明这是个类模型，而非类实例对象
    def __init__(self,session:Session):
        self.session=session

    def get(self,id_:int)->ModelType|None:
        return self.session.get(self.model,id_)

    def add(self,obj:ModelType)->ModelType:
        self.session.add(obj)
        self.session.flush()
        return obj

    def delete(self,obj:ModelType)->None:
        self.session.delete(obj)
        self.session.flush()

    updatable_fields: set[str] = set()

    def update(self, id_: int, **fields) -> ModelType | None:
        obj = self.get(id_)
        if obj is None:
            return None
        for k, v in fields.items():
            if self.updatable_fields and k not in self.updatable_fields:
                raise ValueError(f"不能更新字段: {k}")
            setattr(obj, k, v)
        self.session.flush()
        return obj
    def list_all(self, page: int = 1, size: int | None = None,
             order_by=None) -> list: 
        stmt = select(self.model)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        if size is not None:
            stmt = stmt.offset((page - 1) * size).limit(size)
        return list(self.session.scalars(stmt).all())
    def count(self) -> int:
        return self.session.scalar(select(func.count()).select_from(self.model)) or 0
