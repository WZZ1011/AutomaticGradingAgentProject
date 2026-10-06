from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from automaticgradingagentproject.core import auth
from automaticgradingagentproject.core.database import Base, engine, get_db
from automaticgradingagentproject.core.save_upload_file import save_upload_file
from automaticgradingagentproject.models.user import User
from automaticgradingagentproject.schemas import markingschemas
from automaticgradingagentproject.services import marking_service

Base.metadata.create_all(bind=engine)
marking_router = APIRouter(prefix="/marking")

@marking_router.post("/upload",response_model=markingschemas.UploadResponse)
def upload( db: Annotated[Session, Depends(get_db)],
            current_user: Annotated[User, Depends(auth.get_current_user)],document:Annotated[UploadFile,File()],
            source_code: Annotated[UploadFile, File()],
            topic_id: int = Form(...)):
    doc_path = save_upload_file(document)       
    code_path = save_upload_file(source_code)
    user_id = current_user.user_id
    return marking_service.upload_document_and_code(doc_path,code_path,user_id,topic_id,db) # pyright: ignore[reportArgumentType]


@marking_router.post("/insert_topic",response_model=markingschemas.InsertTopicResponse)
def insert_topic(insert_topic_request:markingschemas.InsertTopicRequest,db:Annotated[Session,Depends(get_db)]
                 , current_user: Annotated[User, Depends(auth.get_current_user)]):
    name=insert_topic_request.name
    description=insert_topic_request.description
    total_score=insert_topic_request.total_score
    return marking_service.insert_topic(db,name,description,total_score)


@marking_router.post("/insert_rule",response_model=markingschemas.InsertTopicRuleResponse)
def insert_rule(insert_rule_request:markingschemas.InsertTopicRuleRequest,db: Annotated[Session, Depends(get_db)]
                , current_user: Annotated[User, Depends(auth.get_current_user)]):
    name = insert_rule_request.name
    description = insert_rule_request.description
    weight = insert_rule_request.weight
    topic_id = insert_rule_request.topic_id
    return marking_service.insert_topic_rule(name,description,weight,topic_id,db)


@marking_router.post("/update_topic/{topic_id}",response_model=markingschemas.TopicResponse)
def update_topic(topic_id:int,update_topic_request:markingschemas.UpdateTopicRequest,db:Annotated[Session,Depends(get_db)]
                 , current_user: Annotated[User, Depends(auth.get_current_user)]):
    data=update_topic_request.model_dump(exclude_none=True)
    return marking_service.update_topic(db,topic_id,**data) 


@marking_router.post("/update_rule/{rule_id}",response_model=markingschemas.RuleResponse)
def update_rule(rule_id:int,update_rule_request:markingschemas.UpdateRuleRequest,db:Annotated[Session,Depends(get_db)]
                , current_user: Annotated[User, Depends(auth.get_current_user)]):
    data = update_rule_request.model_dump(exclude_none=True)
    return marking_service.update_rule(db,rule_id,**data) 


@marking_router.post("/get_page_topic",response_model=markingschemas.TopicListResponse)
def get_page_topic(get_page_topic_request:markingschemas.PaginationRequest,db:Annotated[Session,Depends(get_db)]
                   , current_user: Annotated[User, Depends(auth.get_current_user)]):
    page=get_page_topic_request.page
    size=get_page_topic_request.size
    return marking_service.get_topics(db,page,size)


@marking_router.post("/get_page_rule",response_model=markingschemas.RuleListResponse)
def get_page_rule(get_page_rule_request:markingschemas.PaginationRequest,db:Annotated[Session,Depends(get_db)]
                  , current_user: Annotated[User, Depends(auth.get_current_user)]):
    page=get_page_rule_request.page
    size=get_page_rule_request.size
    return marking_service.get_topic_all_rule(db,page,size)


@marking_router.get("/download_doc/{doc_id}")
def get_doc(doc_id:int,db:Annotated[Session,Depends(get_db)]
            , current_user: Annotated[User, Depends(auth.get_current_user)]):
    doc = marking_service.get_document(db,doc_id)
    file_path = Path(doc.word_doc) # pyright: ignore[reportArgumentType]
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(
        path=file_path,
        filename=file_path.name,         
        media_type="application/octet-stream",  
    )


@marking_router.get("/download_code/{code_id}")
def get_code(code_id:int,db:Annotated[Session,Depends(get_db)]
             , current_user: Annotated[User, Depends(auth.get_current_user)]):
     code = marking_service.get_code(db,code_id)
     file_path = Path(code.source_code) # pyright: ignore[reportArgumentType]
     if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
     return FileResponse(
            path=file_path,
            filename=file_path.name,         
            media_type="application/octet-stream",  
        )
@marking_router.post("/list_doc",response_model=markingschemas.DocListResponse)
def list_doc(list_doc_request:markingschemas.PaginationRequest,db:Annotated[Session,Depends(get_db)],
             current_user: Annotated[User, Depends(auth.get_current_user)]):
    page = list_doc_request.page
    size = list_doc_request.size
    return marking_service.get_page_doc(db,page,size)

@marking_router.post("/list_code",response_model=markingschemas.CodeListResponse)
def list_code(list_code_request:markingschemas.PaginationRequest,db:Annotated[Session,Depends(get_db)],
             current_user: Annotated[User, Depends(auth.get_current_user)]):
    page = list_code_request.page
    size = list_code_request.size
    return marking_service.get_page_code(db,page,size)
@marking_router.post("/delete_doc",response_model=markingschemas.DeleteDocResponse)
def delete_doc(delete_doc_request:markingschemas.DeleteDocRequest,db:Annotated[Session,Depends(get_db)]
               , current_user: Annotated[User, Depends(auth.get_current_user)]):
    return marking_service.delete_document(db,delete_doc_request.doc_id)


@marking_router.post("/delete_code",response_model=markingschemas.DeleteCodeResponse)
def delete_code(delete_code_request:markingschemas.DeleteCodeRequest,db:Annotated[Session,Depends(get_db)]
                , current_user: Annotated[User, Depends(auth.get_current_user)]):
    return marking_service.delete_code(db,delete_code_request.code_id)

  
@marking_router.post("/delete_rule",response_model=markingschemas.DeleteTopicRuleResponse)
def delete_rule(delete_rule_request:markingschemas.DeleteTopicRuleRequest,db:Annotated[Session,Depends(get_db)]
                , current_user: Annotated[User, Depends(auth.get_current_user)]):
   rule_id=delete_rule_request.rule_id
   return marking_service.delete_topic_rule(rule_id,db)


@marking_router.post("/delete_topic",response_model=markingschemas.DeleteTopicResponse)
def delete_topic(delete_topic_request:markingschemas.DeleteTopicRequest,db:Annotated[Session,Depends(get_db)]
                 , current_user: Annotated[User, Depends(auth.get_current_user)]):
    topic_id = delete_topic_request.topic_id
    return marking_service.delete_topic(db,topic_id)

