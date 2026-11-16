
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from automaticgradingagentproject.core import auth
from automaticgradingagentproject.core.database import Base, engine, get_db
from automaticgradingagentproject.core.exception import TopicNotFound
from automaticgradingagentproject.models.user import User
from automaticgradingagentproject.schemas import markingschemas
from automaticgradingagentproject.services import agent_service

Base.metadata.create_all(bind=engine)
agent_router=APIRouter(prefix="/agent")
ALLOWED_USER_IDS=["1","2","3"]
@agent_router.post("/one_click_marking",response_model=markingschemas.OneClickMarkingResponse)
async def one_click_marking(one_click_marking_request:markingschemas.OneClickMarkingRequest,
                            db:Annotated[Session,Depends(get_db)],
                            current_user: Annotated[User,Depends(auth.get_current_user)]):
    if current_user.user_id not in ALLOWED_USER_IDS:
        raise HTTPException(status_code=403,detail="无权限")
    try:
        return await agent_service.marking_batch(db,one_click_marking_request.topic_id)
    except (TopicNotFound, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@agent_router.get("/get_marking_result",response_model=markingschemas.GetMarkingResultsResponse)
def get_marking_result(db:Annotated[Session,Depends(get_db)],
                       current_user:Annotated[User,Depends(auth.get_current_user)],
                       page:int=Query(1,ge=1,description="页码，从1开始"),
                       size:int=Query(20,ge=1,le=100,description="每页数量"),
                       topic_id:int|None=Query(None,ge=1,description="按专题过滤")):
    if current_user.user_id not in ALLOWED_USER_IDS:
        raise HTTPException(status_code=403,detail="无权限")
    return agent_service.get_marking_result(db,page,size,topic_id)