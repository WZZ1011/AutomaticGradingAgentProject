import io
from pathlib import Path

import pytest
from fastapi import UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from automaticgradingagentproject.core import save_upload_file as save_upload_module
from automaticgradingagentproject.core.database import Base
from automaticgradingagentproject.core.save_upload_file import (
    SavedUpload,
    sanitize_filename,
    save_upload_file,
)
from automaticgradingagentproject.models.marking import Code, Document, LLMComment
from automaticgradingagentproject.models.user import User
from automaticgradingagentproject.schemas.markingschemas import (
    CodeListResponse,
    DocListResponse,
)
from automaticgradingagentproject.services import marking_service


def _engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine


# ---------- 文件名清理与落盘 ----------

@pytest.mark.parametrize(
    "raw, expected",
    [
        (r"..\..\evil\report.docx", "report.docx"),
        ("/etc/passwd", "passwd"),
        (r"C:\Users\a\main.cpp", "main.cpp"),
        ("报告.docx", "报告.docx"),
        ("a\x00b\r\n.docx", "ab.docx"),
    ],
)
def test_sanitize_filename_strips_paths_and_control_chars(raw, expected):
    assert sanitize_filename(raw, fallback="fallback.docx") == expected


@pytest.mark.parametrize("raw", [None, "", "   ", ".", ".."])
def test_sanitize_filename_falls_back_for_empty_names(raw):
    assert sanitize_filename(raw, fallback="abc.docx") == "abc.docx"


def test_sanitize_filename_truncates_but_keeps_extension():
    name = sanitize_filename("长" * 300 + ".docx", fallback="fallback.docx")
    assert len(name) == 255
    assert name.endswith(".docx")


def test_save_upload_file_keeps_original_name(tmp_path, monkeypatch):
    monkeypatch.setattr(save_upload_module, "UPLOAD_DIR", str(tmp_path))
    upload = UploadFile(file=io.BytesIO(b"hello"), filename=r"C:\fake\实验报告.docx")

    saved = save_upload_file(upload)

    assert saved.file_name == "实验报告.docx"
    path = Path(saved.path)
    assert path.parent == tmp_path
    assert path.suffix == ".docx"
    assert len(path.stem) == 32  # uuid4().hex
    assert path.read_bytes() == b"hello"


def test_save_upload_file_falls_back_to_stored_name(tmp_path, monkeypatch):
    monkeypatch.setattr(save_upload_module, "UPLOAD_DIR", str(tmp_path))
    upload = UploadFile(file=io.BytesIO(b"x"), filename=None)

    saved = save_upload_file(upload)

    assert saved.file_name == Path(saved.path).name


# ---------- 上传落库 ----------

def test_upload_document_and_code_persists_file_names(tmp_path):
    engine = _engine()
    try:
        with Session(engine) as db:
            db.add(User(name="张三", user_id="s1"))
            db.commit()

            document = SavedUpload(path=str(tmp_path / "a.docx"), file_name="实验报告.docx")
            source = SavedUpload(path=str(tmp_path / "b.cpp"), file_name="main.cpp")

            result = marking_service.upload_document_and_code(
                document, source, "s1", 1, db
            )

            saved_doc = db.get(Document, result["doc_id"])
            saved_code = db.get(Code, result["code_id"])
            comment = db.query(LLMComment).one()

            assert saved_doc.word_doc == document.path
            assert saved_doc.file_name == "实验报告.docx"
            assert saved_code.source_code == source.path
            assert saved_code.file_name == "main.cpp"
            assert comment.student_name == "张三"
            assert comment.student_id == "s1"
            assert comment.document_id == saved_doc.id
            assert comment.code_id == saved_code.id
    finally:
        engine.dispose()


def test_upload_rolls_back_when_user_missing(tmp_path):
    engine = _engine()
    try:
        with Session(engine) as db:
            document = SavedUpload(path=str(tmp_path / "a.docx"), file_name="a.docx")
            source = SavedUpload(path=str(tmp_path / "b.cpp"), file_name="b.cpp")

            with pytest.raises(ValueError):
                marking_service.upload_document_and_code(document, source, "nobody", 1, db)

            assert db.query(Document).count() == 0
            assert db.query(Code).count() == 0
            assert db.query(LLMComment).count() == 0
    finally:
        engine.dispose()


# ---------- 列表响应 ----------

def test_list_doc_returns_metadata_without_absolute_paths():
    engine = _engine()
    try:
        with Session(engine) as db:
            db.add(User(name="张三", user_id="s1"))
            db.add_all([
                Document(
                    id=1, word_doc=r"d:\marking_uploads\old-uuid.docx",
                    content_hash="h1", user_id="s1", topic_id=1,
                ),
                Document(
                    id=2, word_doc=r"d:\marking_uploads\new-uuid.docx",
                    file_name="实验报告.docx", content_hash="h2", user_id="s1", topic_id=1,
                ),
            ])
            db.commit()

            data = DocListResponse(**marking_service.get_page_doc(db, 1, 20)).model_dump()

            assert [item["id"] for item in data["items"]] == [2, 1]
            assert data["items"][0]["file_name"] == "实验报告.docx"
            # 历史行回退为磁盘 basename
            assert data["items"][1]["file_name"] == "old-uuid.docx"
            assert data["items"][0]["student_name"] == "张三"
            assert data["items"][0]["download_url"] == "/marking/download_doc/2"
            assert "word_doc" not in data["items"][0]
            assert "d:\\marking_uploads" not in str(data)
    finally:
        engine.dispose()


def test_list_code_returns_metadata_without_absolute_paths():
    engine = _engine()
    try:
        with Session(engine) as db:
            db.add(User(name="李四", user_id="s2"))
            db.add(Code(
                id=1, source_code=r"d:\marking_uploads\old-uuid.cpp",
                content_hash="h1", user_id="s2", topic_id=1,
            ))
            db.commit()

            data = CodeListResponse(**marking_service.get_page_code(db, 1, 20)).model_dump()

            item = data["items"][0]
            assert item["file_name"] == "old-uuid.cpp"
            assert item["student_name"] == "李四"
            assert item["download_url"] == "/marking/download_code/1"
            assert "source_code" not in item
            assert "d:\\marking_uploads" not in str(data)
    finally:
        engine.dispose()


def test_list_student_name_is_none_when_user_missing():
    engine = _engine()
    try:
        with Session(engine) as db:
            db.add(Document(
                id=1, word_doc="x.docx", content_hash="h1",
                user_id="ghost", topic_id=1,
            ))
            db.commit()

            data = DocListResponse(**marking_service.get_page_doc(db, 1, 20)).model_dump()

            assert data["items"][0]["student_name"] is None
    finally:
        engine.dispose()


# ---------- 分页稳定 ----------

def test_page_doc_is_ordered_and_stable():
    engine = _engine()
    try:
        with Session(engine) as db:
            db.add_all([
                Document(
                    id=i, word_doc=f"doc{i}.docx", content_hash=f"h{i}",
                    user_id="s1", topic_id=1,
                )
                for i in (1, 2, 3)
            ])
            db.commit()

            first = marking_service.get_page_doc(db, 1, 2)
            second = marking_service.get_page_doc(db, 2, 2)

            assert [item["id"] for item in first["items"]] == [3, 2]
            assert [item["id"] for item in second["items"]] == [1]
    finally:
        engine.dispose()
