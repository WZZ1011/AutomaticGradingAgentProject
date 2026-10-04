from pydantic import BaseModel, ConfigDict


class UserCreate(BaseModel):
    name: str
    user_id:str
   
class UserOut(BaseModel):
    name:str
    user_id:str
    model_config = ConfigDict(from_attributes=True)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"