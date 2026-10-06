from pydantic import BaseModel, ConfigDict, Field


class InsertTopicRuleRequest(BaseModel):
    name: str = Field(..., description="规则名称")
    description: str = Field(..., description="规则描述")
    weight: int = Field(..., description="规则权重")
    topic_id: int = Field(..., description="主题ID")

class UpdateRuleRequest(BaseModel):
    name: str | None = Field(None, description="规则名称")
    description: str | None = Field(None, description="规则描述")
    weight: int | None = Field(None, description="规则权重")
    topic_id: int | None = Field(None, description="主题ID")
    
class DeleteTopicRuleRequest(BaseModel):
    rule_id:int = Field(...,description="将会删除的规则ID")

class InsertTopicRequest(BaseModel):
    name: str = Field(..., description="主题名称")
    description: str | None = Field(None, description="主题描述")
    total_score: int = Field(..., description="主题总分")

class UpdateTopicRequest(BaseModel):
    name: str | None = Field(None, description="主题名称")
    description: str | None = Field(None, description="主题描述")
    total_score: int | None = Field(None, description="主题总分")
    
class PaginationRequest(BaseModel):
    page: int = Field(1, ge=1, description="页码，从1开始")
    size: int = Field(20, ge=1, le=100, description="每页数量")

class DeleteTopicRequest(BaseModel):
    topic_id:int = Field(...)
    
class DeleteDocRequest(BaseModel):
    doc_id:int

class DeleteCodeRequest(BaseModel):
    code_id:int




class InsertTopicRuleResponse(BaseModel):
    id: int = Field(..., description="新创建的规则ID")
    model_config = ConfigDict(from_attributes=True)

class UploadResponse(BaseModel):
    doc_id: int
    code_id: int

class DeleteTopicRuleResponse(BaseModel):
    ok:str = Field(..., description="是否被成功删除")
    model_config = ConfigDict(from_attributes=True)

class RuleResponse(BaseModel):
    id: int = Field(..., description="规则ID")
    name: str = Field(..., description="规则名称")
    description: str | None = Field(None, description="规则描述")
    weight: int = Field(..., description="规则权重")
    topic_id: int = Field(..., description="主题ID")
    model_config = ConfigDict(from_attributes=True)

class InsertTopicResponse(BaseModel):
    id: int = Field(..., description="新创建的主题ID")
    model_config = ConfigDict(from_attributes=True) 

class TopicResponse(BaseModel):
    id: int = Field(..., description="主题ID")
    name: str = Field(..., description="主题名称")
    description: str | None = Field(None, description="主题描述")
    total_score: int = Field(..., description="主题总分")
    model_config = ConfigDict(from_attributes=True)

class TopicListResponse(BaseModel):
    total: int = Field(..., description="总数量")
    page: int = Field(..., description="当前页码")
    size: int = Field(..., description="每页数量")
    items: list[TopicResponse] = Field(default_factory=list, description="主题列表")
    model_config = ConfigDict(from_attributes=True)

class RuleListResponse(BaseModel):
    total: int = Field(..., description="总数量")
    page: int = Field(..., description="当前页码")
    size: int = Field(..., description="每页数量")
    items: list[RuleResponse] = Field(default_factory=list, description="规则列表")
    model_config = ConfigDict(from_attributes=True)

class DeleteTopicResponse(BaseModel):
    ok:str = Field(...)
    model_config = ConfigDict(from_attributes=True)
    
class GetDocResponse(BaseModel):
    id:int
    word_doc:str
    user_id:str
    topic_id:int
    model_config = ConfigDict(from_attributes=True)

class GetCodeResponse(BaseModel):   
     id:int
     source_code:str
     user_id:str
     topic_id:int
     model_config = ConfigDict(from_attributes=True)

class DeleteDocResponse(BaseModel):
    ok:str
    model_config = ConfigDict(from_attributes=True)
    
class DeleteCodeResponse(BaseModel):
    ok:str
    model_config = ConfigDict(from_attributes=True)


class DocListResponse(BaseModel):
    total: int = Field(..., description="总数量")
    page: int = Field(..., description="当前页码")
    size: int = Field(..., description="每页数量")
    items: list[GetDocResponse] = Field(default_factory=list, description="规则列表")
    model_config = ConfigDict(from_attributes=True)

class CodeListResponse(BaseModel):
    total: int = Field(..., description="总数量")
    page: int = Field(..., description="当前页码")
    size: int = Field(..., description="每页数量")
    items: list[GetCodeResponse] = Field(default_factory=list, description="规则列表")
    model_config = ConfigDict(from_attributes=True)