from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automaticgradingagentproject.core.ast_validation import AST_validation
from automaticgradingagentproject.core.enums.ast_rule_enum import AstRuleEnum as R
from automaticgradingagentproject.models.marking import Code, Rule


def test_configured_rules_follow_submission_topic(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    monkeypatch.setenv("AST_DATABASE_URL", url)
    engine = create_engine(url)
    path = Path(__file__).parent / "fixtures/topic1.cpp"
    c_path = tmp_path / "sort.c"
    c_path.write_text("""
        #include <stdlib.h>
        typedef struct { int data[8]; int length; } List;
        int compare(const void *old, const void *new) {
            return (*(const int *)old > *(const int *)new) - (*(const int *)old < *(const int *)new);
        }
        void sort_list(List *list) { qsort(list->data, list->length, sizeof(int), compare); }
    """, encoding="utf-8")
    try:
        Code.__table__.create(engine)
        Rule.__table__.create(engine)
        with Session(engine) as db:
            for i, topic in enumerate([82, 19], start=1):
                db.add(Code(id=i, source_code=str(path.resolve()), content_hash=str(i), user_id="test", topic_id=topic))
            for name in [R.TOPIC1_NO_STD_SORT.name, R.TOPIC1_NO_STD_SORT.value, "format_error"]:
                db.add(Rule(name=name, weight=1, topic_id=82))
            db.add(Code(id=4, source_code=str(c_path), content_hash="c", user_id="test", topic_id=81))
            for rule in (R.TOPIC1_NO_STD_SORT, R.TOPIC1_REQUIRE_SELF_LINEAR_LIST):
                db.add(Rule(name=rule.value, weight=1, topic_id=81))
            for topic, fixture, rule in [(91, "topic2.cpp", R.TOPIC2_REQUIRE_AVL_NODE_STRUCT),
                                         (67, "topic3.cpp", R.TOPIC3_REQUIRE_SELF_DIJKSTRA)]:
                db.add(Code(id=topic, source_code=str(path.with_name(fixture).resolve()),
                            content_hash=fixture, user_id="test", topic_id=topic))
                db.add(Rule(name=rule.value, weight=1, topic_id=topic))
            db.commit()
        assert AST_validation(1) == {"ast_error": {"count": 0, R.TOPIC1_NO_STD_SORT.value: {"count": 0}}}
        assert AST_validation(2) == {"ast_error": {"count": 0}}
        assert AST_validation(4) == {"ast_error": {
            "count": 1,
            R.TOPIC1_NO_STD_SORT.value: {"count": 1},
            R.TOPIC1_REQUIRE_SELF_LINEAR_LIST.value: {"count": 0},
        }}
        for code_id, rule in [(91, R.TOPIC2_REQUIRE_AVL_NODE_STRUCT), (67, R.TOPIC3_REQUIRE_SELF_DIJKSTRA)]:
            assert AST_validation(code_id) == {"ast_error": {"count": 0, rule.value: {"count": 0}}}
        with pytest.raises(ValueError, match="源码记录不存在"):
            AST_validation(3)
    finally:
        engine.dispose()


def test_missing_database_stays_absent(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    monkeypatch.setenv("AST_DATABASE_URL", f"sqlite:///{path.as_posix()}")
    with pytest.raises(FileNotFoundError):
        AST_validation(1)
    assert not path.exists()

def test_every_shared_rule_has_a_check():
    from automaticgradingagentproject.core.ast_validation.rules import CHECKS

    assert set(CHECKS) == set(R)


@pytest.mark.parametrize(("filename", "content"), [
    ("renamed.docx", b"int main() { return 0; }"),
    ("no_extension", b"int main() { return 0; }"),
    ("broken.cpp", b"int main( {"),
    ("broken.c", b"int main( {"),
    ("cpp_template.c", b"template<class T> T identity(T value) { return value; }"),
    ("encoding.cpp", b"\xff\xfe"),
])
def test_submission_parse_errors_return_result(tmp_path, monkeypatch, filename, content):
    from automaticgradingagentproject.core.ast_validation import repository

    path = tmp_path / filename
    path.write_bytes(content)
    rules = [R.TOPIC1_NO_STD_SORT]
    monkeypatch.setattr(repository, "submission", lambda code_id: (str(path), rules))
    assert AST_validation(1) == {"ast_error": {"count": 0, "error_message": "parse_error"}}

    # 一次提交解析失败后，后续合法提交仍能正常批阅。
    valid_path = tmp_path / "valid.CPP"
    valid_path.write_text("int main() { return 0; }", encoding="utf-8")
    monkeypatch.setattr(repository, "submission", lambda code_id: (str(valid_path), rules))
    assert AST_validation(2) == {"ast_error": {"count": 0, rules[0].value: {"count": 0}}}


def test_unknown_rule_is_not_reported_as_student_parse_error(tmp_path, monkeypatch):
    from automaticgradingagentproject.core.ast_validation import repository

    path = tmp_path / "source.cpp"
    path.write_text("int main() {}", encoding="utf-8")
    monkeypatch.setattr(repository, "submission", lambda code_id: (str(path), ["unknown_rule"]))
    with pytest.raises(ValueError, match="未知 AST 规则"):
        AST_validation(1)
