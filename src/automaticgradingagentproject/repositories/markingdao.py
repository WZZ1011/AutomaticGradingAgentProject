from automaticgradingagentproject.models import marking
from automaticgradingagentproject.repositories.basedao import BaseRepository


class DocumentDao(BaseRepository[marking.Document]):
    model = marking.Document

class CodeDao(BaseRepository[marking.Code]):
    model = marking.Code

class TopicDao(BaseRepository[marking.Topic]):
    model = marking.Topic
    updatable_fields = {"name", "description", "total_score"}

class RuleDao(BaseRepository[marking.Rule]):
    model = marking.Rule
    # 规则类型和所属专题创建后不可修改，只允许调整权重/描述。
    updatable_fields = {"weight", "description"}

class LLMCommentDao(BaseRepository[marking.LLMComment]):
    model=marking.LLMComment
    updatable_fields = {"comment"}
