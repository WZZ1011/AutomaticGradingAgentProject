from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from automaticgradingagentproject.core.enums.rule_type_enum import RuleTypeEnum


class InsertTopicRuleRequest(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    name: RuleTypeEnum = Field(..., description="规则类型（只能是系统预设的 RuleTypeEnum）")
    description: str = Field(..., description="规则描述")
    weight: int = Field(..., ge=0, le=100, description="规则权重（百分数，0-100）")
    topic_id: int = Field(..., description="主题ID")

class UpdateRuleRequest(BaseModel):
    # 规则类型和所属专题创建后不可修改，只允许调整权重/描述。
    description: str | None = Field(None, description="规则描述")
    weight: int | None = Field(None, ge=0, le=100, description="规则权重（百分数，0-100）")
    model_config = ConfigDict(extra="forbid")

class DeleteTopicRuleRequest(BaseModel):
    rule_id:int = Field(...,description="将会删除的规则ID")

class InsertTopicRequest(BaseModel):
    name: str = Field(..., description="主题名称")
    description: str | None = Field(None, description="主题描述")
    total_score: int = Field(..., gt=0, description="主题总分（必须大于 0）")

class UpdateTopicRequest(BaseModel):
    name: str | None = Field(None, description="主题名称")
    description: str | None = Field(None, description="主题描述")
    total_score: int | None = Field(None, description="主题总分")
    
class PaginationRequest(BaseModel):
    page: int = Field(1, ge=1, description="页码，从1开始")
    size: int = Field(20, ge=1, le=100, description="每页数量")

class GetPageRuleRequest(PaginationRequest):
    topic_id: int | None = Field(
        None, ge=1, description="按专题过滤；为空时返回全部专题的评分规则"
    )

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

class RuleTypeInfo(BaseModel):
    value: str = Field(..., description="规则类型枚举值")
    label: str = Field(..., description="规则中文名")
    description: str = Field(..., description="规则说明")
    default_weight: int = Field(0, ge=0, le=100, description="默认权重（百分数）")

class RuleTypeListResponse(BaseModel):
    items: list[RuleTypeInfo] = Field(
        default_factory=list, description="固定评分规则类型"
    )

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
    file_name: str | None = Field(None, description="上传时的原始文件名")
    user_id:str
    student_name: str | None = Field(None, description="上传者姓名（按学号查 User）")
    topic_id:int
    created_at: datetime | None = None

    @computed_field
    @property
    def download_url(self) -> str:
        return f"/marking/download_doc/{self.id}"

    model_config = ConfigDict(from_attributes=True)

class GetCodeResponse(BaseModel):
     id:int
     file_name: str | None = Field(None, description="上传时的原始文件名")
     user_id:str
     student_name: str | None = Field(None, description="上传者姓名（按学号查 User）")
     topic_id:int
     created_at: datetime | None = None

     @computed_field
     @property
     def download_url(self) -> str:
          return f"/marking/download_code/{self.id}"

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


class MarkingResult(BaseModel):
    id: int = Field(...,description="批阅ID")
    student_name: str = Field(...,description="学生姓名")
    student_id: str = Field(...,description="学生学号")
    document_id: int = Field(...,description="报告ID")
    code_id: int=Field(...,description="源代码ID")
    topic_id: int=Field(...,description="专题ID")
    llm_comment: str=Field(...,description="LLM评价")
    model_config = ConfigDict(from_attributes=True)
class OneClickMarkingResponse(BaseModel):
    ok:str = Field(...,description="批阅完成与否")
    error_count:int = Field(...,description="批阅过程中失败的次数")

class OneClickMarkingRequest(BaseModel):
    topic_id: int = Field(..., ge=1, description="要批阅的专题ID")

class GetMarkingResultsResponse(BaseModel):
    total:int = Field(...,description="总数量")
    page:int = Field(...,description = "当前页码")
    size:int = Field(...,description="每页数量")
    items:list[MarkingResult] = Field(default_factory=list,description="批阅结果列表")
    model_config = ConfigDict(from_attributes=True)