from pydantic import BaseModel


class UserCreate(BaseModel):
    name: str
    user_id:str
   
class UserOut(BaseModel):
    name:str
    user_id:str



class Config:
    from_attributes = True #允许从 ORM 对象直接构造这个模型


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"