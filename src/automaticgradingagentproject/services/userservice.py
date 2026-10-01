from fastapi import HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from automaticgradingagentproject.core import auth
from automaticgradingagentproject.models.user import User
from automaticgradingagentproject.schemas.userschemas import UserCreate


def register(user_in:UserCreate,db:Session):
    # 检查用户名 
    if db.query(User).filter(User.name == user_in.name).first():
        raise HTTPException(status_code=400, detail="用户名已被注册")
   
    user = User(
        name=user_in.name,
        user_id=auth.hash_password(user_in.user_id),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

def login(form_data: OAuth2PasswordRequestForm,
    db: Session ):
    user = db.query(User).filter(User.name == form_data.username).first()
    if not user or not auth.verify_password(form_data.password, user.user_id): # pyright: ignore[reportArgumentType]
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    token = auth.create_access_token({"sub": user.name})
    return {"access_token": token, "token_type": "bearer"}
