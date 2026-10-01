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

