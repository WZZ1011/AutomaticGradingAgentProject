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
    updatable_fields = {"name", "description", "weight", "topic_id"}
