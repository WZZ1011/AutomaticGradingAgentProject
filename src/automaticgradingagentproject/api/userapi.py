from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from automaticgradingagentproject.core import auth
from automaticgradingagentproject.core.database import Base, engine, get_db
from automaticgradingagentproject.models.user import User
from automaticgradingagentproject.schemas.userschemas import Token, UserCreate, UserOut
from automaticgradingagentproject.services import userservice

Base.metadata.create_all(bind=engine)
userrouter = APIRouter(prefix="/user")
@userrouter.post("/register", response_model=UserOut, status_code=201)
def register(user_in: UserCreate,db: Annotated[Session, Depends(get_db)]):
    return userservice.register(user_in,db)


@userrouter.post("/login", response_model=Token)
def login(
    form_data: Annotated[OAuth2PasswordRequestForm,Depends()],
    db: Annotated[Session ,Depends(get_db)]
):
   return userservice.login(form_data,db)


@userrouter.get("/me", response_model=UserOut)
def read_me(current_user: Annotated[User,Depends(auth.get_current_user)]):
    return current_user