class RuleNotFound(Exception):
    def __init__(self, rule_id: int):
        self.rule_id = rule_id
        super().__init__(f"Rule {rule_id} not found")

class TopicNotFound(Exception):
    def __init__(self, topic_id: int):
        self.rule_id = topic_id
        super().__init__(f"Topic {topic_id} not found")