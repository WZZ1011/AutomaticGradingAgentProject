"""doc_parsing 的返回契约与关键校验回归。"""

from __future__ import annotations

import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import nsdecls, qn
from docx.oxml.parser import parse_xml
from docx.shared import Pt
from docx.text.paragraph import Paragraph

from automaticgradingagentproject.core import doc_parsing as dp
from automaticgradingagentproject.core import llm_invoke
from automaticgradingagentproject.core.enums.rule_type_enum import RuleTypeEnum
from automaticgradingagentproject.core.score import score_


def _add_page_field(paragraph, font="Times New Roman", size=9.0, align="right"):
    """在段落里放一个标准复杂 PAGE 域；格式只写在显示结果的 run 上。"""
    paragraph.style = "Normal"
    if align == "right":
        paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    elif align == "center":
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = paragraph._p
    p.append(parse_xml(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="begin"/></w:r>'))
    p.append(
        parse_xml(
            f'<w:r {nsdecls("w")}><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
        )
    )
    p.append(parse_xml(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="separate"/></w:r>'))
    run = paragraph.add_run("1")
    if font:
        run.font.name = font
    if size:
        run.font.size = Pt(size)
    p.append(parse_xml(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="end"/></w:r>'))


def _set_doc_defaults(doc, font="Times New Roman", size_half_points=18, jc="right"):
    """把字体/字号/对齐放进 docDefaults，模拟“只靠文档默认格式”的模板。"""
    styles = doc.styles.element
    rpr = styles.find(f"{qn('w:docDefaults')}/{qn('w:rPrDefault')}/{qn('w:rPr')}")
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = parse_xml(f'<w:rFonts {nsdecls("w")}/>')
        rpr.insert(0, rfonts)
    rfonts.set(qn("w:ascii"), font)
    rfonts.set(qn("w:hAnsi"), font)
    sz = rpr.find(qn("w:sz"))
    if sz is None:
        sz = parse_xml(f'<w:sz {nsdecls("w")}/>')
        rpr.append(sz)
    sz.set(qn("w:val"), str(size_half_points))

    doc_defaults = styles.find(qn("w:docDefaults"))
    ppr_default = doc_defaults.find(qn("w:pPrDefault"))
    if ppr_default is None:
        ppr_default = parse_xml(f'<w:pPrDefault {nsdecls("w")}/>')
        doc_defaults.append(ppr_default)
    ppr = ppr_default.find(qn("w:pPr"))
    if ppr is None:
        ppr = parse_xml(f'<w:pPr {nsdecls("w")}/>')
        ppr_default.append(ppr)
    jc_element = ppr.find(qn("w:jc"))
    if jc_element is None:
        jc_element = parse_xml(f'<w:jc {nsdecls("w")}/>')
        ppr.append(jc_element)
    jc_element.set(qn("w:val"), jc)


def _page_messages(tmp_path, name, build):
    path = tmp_path / name
    doc = Document()
    build(doc)
    doc.save(path)
    return dp._check_page_number(dp._load_document(str(path)))


# ---------- 文件名 ----------

@pytest.mark.parametrize(
    "name",
    [
        "2504310107-钟学杨-专题二 树及其应用.docx",
        "2504310107-钟学杨-专题二（树及其应用）.docx",
        "2504310107-钟学杨-专题二-树及其应用.docx",
        "2504310107-钟学杨-专题二.docx",
        "20230001-张三-专题一 线性结构.docx",
        "123456789012-张三-专题四.DOCX",
    ],
)
def test_filename_accepts_lenient_names(name):
    assert dp._check_filename(name) == []


@pytest.mark.parametrize(
    "name",
    [
        "1234567-张三-专题一.docx",
        "2504310107-钟学杨.docx",
        "2504310107-钟学杨-专题abc.docx",
        "2504310107-钟学杨-专题二.pdf",
    ],
)
def test_filename_rejects_invalid_names(name):
    assert dp._check_filename(name) != []


def test_filename_empty_means_unverifiable():
    assert dp._check_filename("") == []


# ---------- 页码 ----------

def test_page_number_uses_visible_result_run(tmp_path):
    messages = _page_messages(
        tmp_path,
        "result.docx",
        lambda doc: _add_page_field(doc.sections[0].footer.paragraphs[0]),
    )
    assert messages == []


def test_page_number_falls_back_to_doc_defaults(tmp_path):
    def build(doc):
        _set_doc_defaults(doc)
        _add_page_field(
            doc.sections[0].footer.paragraphs[0], font=None, size=None, align=None
        )

    assert _page_messages(tmp_path, "defaults.docx", build) == []


def test_page_number_accepts_jc_end(tmp_path):
    def build(doc):
        paragraph = doc.sections[0].footer.paragraphs[0]
        _add_page_field(paragraph, align=None)
        paragraph._p.get_or_add_pPr().append(
            parse_xml(f'<w:jc {nsdecls("w")} w:val="end"/>')
        )

    assert _page_messages(tmp_path, "jc-end.docx", build) == []


def test_page_number_uses_first_page_footer(tmp_path):
    def build(doc):
        doc.add_paragraph("正文")
        section = doc.sections[0]
        section.different_first_page_header_footer = True
        _add_page_field(section.first_page_footer.paragraphs[0])

    assert _page_messages(tmp_path, "first-page.docx", build) == []


def test_page_number_cover_section_without_footer_is_not_reported(tmp_path):
    def build(doc):
        doc.add_paragraph("封面")
        doc.add_section()
        _add_page_field(doc.sections[1].footer.paragraphs[0])

    assert _page_messages(tmp_path, "cover.docx", build) == []


def test_page_number_messages_capped_per_footer(tmp_path):
    def build(doc):
        section = doc.sections[0]
        _add_page_field(section.footer.paragraphs[0], font="Arial", size=10)
        _add_page_field(section.footer.add_paragraph(), font="Calibri", size=12)

    messages = _page_messages(tmp_path, "capped.docx", build)
    assert len(messages) == 2
    assert "字体" in messages[0] and "Arial" in messages[0]
    assert "字号" in messages[1] and "10 磅" in messages[1]


def test_page_number_centered_is_reported(tmp_path):
    messages = _page_messages(
        tmp_path,
        "center.docx",
        lambda doc: _add_page_field(
            doc.sections[0].footer.paragraphs[0], align="center"
        ),
    )
    assert len(messages) == 1
    assert "没有靠右" in messages[0]


def test_page_number_empty_result_run_keeps_format(tmp_path):
    def build(doc):
        paragraph = doc.sections[0].footer.paragraphs[0]
        paragraph.style = "Normal"
        paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p = paragraph._p
        p.append(parse_xml(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="begin"/></w:r>'))
        p.append(
            parse_xml(
                f'<w:r {nsdecls("w")}><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
            )
        )
        p.append(parse_xml(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="separate"/></w:r>'))
        p.append(
            parse_xml(
                f'<w:r {nsdecls("w")}><w:rPr>'
                f'<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>'
                f'<w:sz w:val="18"/></w:rPr></w:r>'
            )
        )
        p.append(parse_xml(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="end"/></w:r>'))

    assert _page_messages(tmp_path, "empty-result.docx", build) == []


def test_page_number_ignores_text_outside_field(tmp_path):
    def build(doc):
        paragraph = doc.sections[0].footer.paragraphs[0]
        paragraph.style = "Normal"
        paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        paragraph.add_run("第 ")
        p = paragraph._p
        p.append(parse_xml(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="begin"/></w:r>'))
        p.append(
            parse_xml(
                f'<w:r {nsdecls("w")}><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
            )
        )
        p.append(parse_xml(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="separate"/></w:r>'))
        run = paragraph.add_run("1")
        run.font.name = "Times New Roman"
        run.font.size = Pt(9)
        p.append(parse_xml(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="end"/></w:r>'))
        paragraph.add_run(" 页")

    assert _page_messages(tmp_path, "mixed.docx", build) == []


def test_page_number_shared_footer_reported_once(tmp_path):
    def build(doc):
        doc.add_paragraph("正文")
        doc.add_section()
        doc.add_section()
        _add_page_field(doc.sections[0].footer.paragraphs[0], font="Arial", size=10)

    messages = _page_messages(tmp_path, "shared.docx", build)
    assert len(messages) == 2
    assert "字体" in messages[0]
    assert "字号" in messages[1]


def test_even_and_odd_settings_error_is_safe():
    class BadSettings:
        @property
        def odd_and_even_pages_header_footer(self):
            raise RuntimeError("boom")

    class BadDocument:
        settings = BadSettings()

    assert dp._even_and_odd_headers(BadDocument()) is False


def test_page_number_split_instruction_is_detected(tmp_path):
    def build(doc):
        paragraph = doc.sections[0].footer.paragraphs[0]
        paragraph.style = "Normal"
        paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p = paragraph._p
        p.append(parse_xml(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="begin"/></w:r>'))
        p.append(
            parse_xml(
                f'<w:r {nsdecls("w")}><w:instrText xml:space="preserve"> PA</w:instrText></w:r>'
            )
        )
        p.append(
            parse_xml(
                f'<w:r {nsdecls("w")}><w:instrText xml:space="preserve">GE </w:instrText></w:r>'
            )
        )
        p.append(parse_xml(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="separate"/></w:r>'))
        run = paragraph.add_run("1")
        run.font.name = "Times New Roman"
        run.font.size = Pt(9)
        p.append(parse_xml(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="end"/></w:r>'))

    assert _page_messages(tmp_path, "split.docx", build) == []


def test_page_number_ignores_other_field_format(tmp_path):
    def build(doc):
        _set_doc_defaults(doc)
        paragraph = doc.sections[0].footer.paragraphs[0]
        paragraph.style = "Normal"
        p = paragraph._p
        p.append(parse_xml(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="begin"/></w:r>'))
        p.append(
            parse_xml(
                f'<w:r {nsdecls("w")}><w:rPr>'
                f'<w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:sz w:val="40"/></w:rPr>'
                f'<w:instrText xml:space="preserve"> NUMPAGES </w:instrText></w:r>'
            )
        )
        p.append(parse_xml(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="separate"/></w:r>'))
        p.append(parse_xml(f'<w:r {nsdecls("w")}><w:t>99</w:t></w:r>'))
        p.append(parse_xml(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="end"/></w:r>'))
        p.append(parse_xml(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="begin"/></w:r>'))
        p.append(
            parse_xml(
                f'<w:r {nsdecls("w")}><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
            )
        )
        p.append(parse_xml(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="separate"/></w:r>'))
        p.append(parse_xml(f'<w:r {nsdecls("w")}/>'))
        p.append(parse_xml(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="end"/></w:r>'))

    assert _page_messages(tmp_path, "other-field.docx", build) == []


def test_page_number_empty_fldsimple_does_not_use_outside_text(tmp_path):
    doc = Document()
    _set_doc_defaults(doc)
    paragraph = doc.sections[0].footer.paragraphs[0]
    paragraph.style = "Normal"
    paragraph.add_run("NOFORMAT")
    paragraph._p.append(parse_xml(f'<w:fldSimple {nsdecls("w")} w:instr=" PAGE "/>'))
    path = tmp_path / "empty-fld.docx"
    doc.save(path)

    loaded = dp._load_document(str(path))
    footer = loaded.sections[0].footer
    para = next(
        Paragraph(element, footer)
        for element in footer._element.iter(qn("w:p"))
    )
    run = dp._page_field_run(para)

    assert run is None or run.text != "NOFORMAT"


def test_nested_field_keeps_outer_result(tmp_path):
    doc = Document()
    paragraph = doc.sections[0].footer.paragraphs[0]
    p = paragraph._p

    def add(inner):
        p.append(parse_xml(f'<w:r {nsdecls("w")}>{inner}</w:r>'))

    add('<w:fldChar w:fldCharType="begin"/>')
    add('<w:instrText xml:space="preserve"> PAGE </w:instrText>')
    add('<w:fldChar w:fldCharType="separate"/>')
    add("<w:t>4</w:t>")
    add('<w:fldChar w:fldCharType="begin"/>')
    add('<w:instrText xml:space="preserve"> NUMPAGES </w:instrText>')
    add('<w:fldChar w:fldCharType="separate"/>')
    add("<w:t>99</w:t>")
    add('<w:fldChar w:fldCharType="end"/>')
    add("<w:t>5</w:t>")
    add('<w:fldChar w:fldCharType="end"/>')
    path = tmp_path / "nested.docx"
    doc.save(path)

    loaded = dp._load_document(str(path))
    footer = loaded.sections[0].footer
    para = next(
        Paragraph(element, footer)
        for element in footer._element.iter(qn("w:p"))
    )
    _, result_runs, _ = dp._page_field_state(para)

    assert [run.text for run in result_runs if run.text] == ["4", "5"]


def test_invalid_title_pg_does_not_raise(tmp_path):
    doc = Document()
    doc.sections[0]._sectPr.append(
        parse_xml(f'<w:titlePg {nsdecls("w")} w:val="maybe"/>')
    )
    path = tmp_path / "bad-titlepg.docx"
    doc.save(path)

    messages = dp._check_page_number(dp._load_document(str(path)))

    assert isinstance(messages, list)


def test_invalid_size_reports_parse_error(tmp_path):
    def build(doc):
        paragraph = doc.sections[0].footer.paragraphs[0]
        paragraph.style = "Normal"
        paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p = paragraph._p
        p.append(parse_xml(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="begin"/></w:r>'))
        p.append(
            parse_xml(
                f'<w:r {nsdecls("w")}><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
            )
        )
        p.append(parse_xml(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="separate"/></w:r>'))
        p.append(
            parse_xml(
                f'<w:r {nsdecls("w")}><w:rPr>'
                f'<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/>'
                f'<w:sz w:val="abc"/></w:rPr><w:t>1</w:t></w:r>'
            )
        )
        p.append(parse_xml(f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="end"/></w:r>'))

    messages = _page_messages(tmp_path, "bad-sz.docx", build)

    assert "无法解析" in "".join(messages)


# ---------- full_text ----------

def test_full_text_keeps_indentation_and_filters_table_sentinels(tmp_path):
    doc = Document()
    doc.add_paragraph("3 功能代码")
    doc.add_paragraph().add_run("    int x = 1;")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "<!-- CODE_BEGIN -->"
    table.cell(0, 1).text = "int y = 2;"
    path = tmp_path / "full.docx"
    doc.save(path)

    text = dp._extract_full_text(dp._load_document(str(path)))

    assert "    int x = 1;" in text
    assert "int y = 2;" in text
    assert "CODE_BEGIN" not in text


# ---------- 返回契约 ----------

def _resolved_inputs(tmp_path):
    docx_path = tmp_path / "2504310107-钟学杨-专题一.docx"
    doc = Document()
    doc.add_paragraph("1 题目与要求")
    doc.add_paragraph("正文")
    doc.save(docx_path)
    code_path = tmp_path / "main.cpp"
    code_path.write_text("int main(){}", encoding="utf-8")
    return dp._ResolvedInputs(
        doc_path=str(docx_path),
        doc_file_name=docx_path.name,
        code_path=str(code_path),
        doc_user_id="20230001",
        doc_topic_id=1,
        code_user_id="20230001",
        code_topic_id=1,
    )


def test_output_is_wrapped(tmp_path, monkeypatch):
    resolved = _resolved_inputs(tmp_path)
    monkeypatch.setattr(dp, "_resolve_paths", lambda doc_id, code_id: resolved)

    answer = dp.doc_parsing(1, 1)

    assert set(answer) == {"full_text", "word_parsing_return"}
    inner = answer["word_parsing_return"]
    assert "full_text" not in inner
    assert "contents" in inner
    assert "document_no_result" in inner
    assert inner["document_no_result"]["count"] == 0
    assert inner["format_error"]["count"] == sum(
        inner["format_error"][check]["count"] for check in dp.DocFormatCheck
    )
    assert "正文" in answer["full_text"]


def test_resolve_failure_still_returns_wrapper(monkeypatch):
    def fail(doc_id, code_id):
        raise ValueError("db down")

    monkeypatch.setattr(dp, "_resolve_paths", fail)

    answer = dp.doc_parsing(1, 1)

    assert answer["full_text"] == ""
    inner = answer["word_parsing_return"]
    assert inner["document_no_result"]["count"] == 1
    missing = [key for key in inner["contents"] if key.startswith("chapter_none_")]
    assert len(missing) == len(dp.REQUIRED_CHAPTERS)


# ---------- 体积防护 ----------

def test_docx_size_limit_is_student_readable(tmp_path, monkeypatch):
    path = tmp_path / "big.docx"
    path.write_bytes(b"x" * 32)
    monkeypatch.setattr(dp, "_MAX_DOCX_BYTES", 16)

    with pytest.raises(ValueError) as exc:
        dp._load_document(str(path))

    assert "20MB" in str(exc.value)
    assert str(path) not in str(exc.value)


def test_compression_ratio_guard(tmp_path, monkeypatch):
    path = tmp_path / "bomb.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "A" * 10000)
    monkeypatch.setattr(dp, "_MAX_COMPRESSION_RATIO", 2)

    with pytest.raises(ValueError, match="压缩比"):
        dp._load_document(str(path))


def test_source_size_limit_is_student_readable(tmp_path, monkeypatch):
    path = tmp_path / "big.cpp"
    path.write_bytes(b"x" * 32)
    monkeypatch.setattr(dp, "_MAX_SOURCE_BYTES", 16)

    with pytest.raises(ValueError) as exc:
        dp._read_source(str(path))

    assert "5MB" in str(exc.value)
    assert str(path) not in str(exc.value)


def test_fallback_sizes_are_known_chapters():
    assert set(dp._FALLBACK_SIZES) <= set(dp.REQUIRED_CHAPTERS)


# ---------- llm_invoke 消费新形态 ----------

def test_rule_material_drops_large_text():
    material = {
        "full_text": "全文",
        "word_parsing_return": {
            "contents": {"5 总结": "..."},
            "format_error": {"count": 0},
        },
    }

    assert llm_invoke._rule_material(material) == {"format_error": {"count": 0}}


@pytest.mark.asyncio
async def test_meaning_analysis_accepts_wrapped_document(monkeypatch):
    ainvoke = AsyncMock(return_value=MagicMock(content="A"))
    monkeypatch.setattr(llm_invoke, "llm", MagicMock(ainvoke=ainvoke))

    result = await llm_invoke.llm_meaning_analysis(
        {
            "full_text": "全文",
            "word_parsing_return": {"contents": {"5 总结": "这是总结"}},
        },
        {},
        {},
    )

    ainvoke.assert_awaited_once()
    assert result[llm_invoke.RuleTypeEnum.MEANING_ANALYSIS.value]["count"] == 0


def test_missing_document_message_has_no_path(tmp_path):
    path = tmp_path / "missing.docx"

    with pytest.raises(FileNotFoundError) as exc:
        dp._load_document(str(path))

    assert str(path) not in str(exc.value)


@pytest.mark.parametrize(
    "entry", ["word/document.xml", "word/_rels/document.xml.rels"]
)
def test_xml_part_limit(tmp_path, monkeypatch, entry):
    path = tmp_path / "bigxml.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(entry, "A" * 2000)
    monkeypatch.setattr(dp, "_MAX_XML_PART_BYTES", 1000)
    monkeypatch.setattr(dp, "_MAX_COMPRESSION_RATIO", 10000)

    with pytest.raises(ValueError, match="XML"):
        dp._load_document(str(path))


def test_extraction_failure_keeps_chapter_none_keys(tmp_path, monkeypatch):
    resolved = _resolved_inputs(tmp_path)
    monkeypatch.setattr(dp, "_resolve_paths", lambda doc_id, code_id: resolved)

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(dp, "_extract_content", boom)

    inner = dp.doc_parsing(1, 1)["word_parsing_return"]

    missing = [key for key in inner["contents"] if key.startswith("chapter_none_")]
    assert len(missing) == len(dp.REQUIRED_CHAPTERS)
    assert inner["document_no_result"]["count"] >= 1


def test_pairing_failure_hides_accounts_and_unchecks(tmp_path, monkeypatch):
    resolved = replace(_resolved_inputs(tmp_path), code_user_id="someone-else")
    monkeypatch.setattr(dp, "_resolve_paths", lambda doc_id, code_id: resolved)

    inner = dp.doc_parsing(1, 1)["word_parsing_return"]

    assert inner["report_code_match"]["checked"] is False
    messages = "".join(inner["document_no_result"]["messages"])
    assert "不匹配" in messages
    assert "20230001" not in messages
    assert "someone-else" not in messages


def test_score_accepts_wrapped_word_parsing():
    weight = {RuleTypeEnum.FORMAT_ERROR: 30}
    inner = {RuleTypeEnum.FORMAT_ERROR: {"count": 3}}
    wrapped = {"full_text": "x", "word_parsing_return": inner}

    assert score_(weight, 100, inner, {}, {}, {}) == 70
    assert score_(weight, 100, wrapped, {}, {}, {}) == 70


def test_doc_parts_flat_dict_keeps_full_text():
    payload, full_text = llm_invoke._doc_parts(
        {"contents": {}, "full_text": "abc"}
    )

    assert payload["contents"] == {}
    assert full_text == "abc"


def test_truncate_limits_text():
    text = "a" * (llm_invoke._MAX_FULL_TEXT_CHARS + 10)

    truncated = llm_invoke._truncate(text)

    assert len(truncated) <= llm_invoke._MAX_FULL_TEXT_CHARS + 20
    assert "已截断" in truncated


@pytest.mark.asyncio
async def test_meaning_analysis_skips_chapter_none_contents(monkeypatch):
    ainvoke = AsyncMock()
    monkeypatch.setattr(llm_invoke, "llm", MagicMock(ainvoke=ainvoke))

    result = await llm_invoke.llm_meaning_analysis(
        {
            "full_text": "",
            "word_parsing_return": {"contents": dp._missing_contents()},
        },
        {},
        {},
    )

    ainvoke.assert_not_awaited()
    payload = result[RuleTypeEnum.MEANING_ANALYSIS.value]
    assert payload["error_message"]


def test_line_spacing_message_has_both_units(tmp_path):
    doc = Document()
    doc.add_paragraph("1 题目与要求")
    body = doc.add_paragraph("正文")
    body.paragraph_format.line_spacing = 1.5
    path = tmp_path / "spacing.docx"
    doc.save(path)

    messages = dp._check_line_spacing(dp._load_document(str(path)), [], set())

    assert len(messages) == 1
    assert "正文第 2 段" in messages[0]
    assert "第 2 个内容块" in messages[0]


@pytest.mark.asyncio
async def test_meaning_prompt_prefers_full_text(monkeypatch):
    ainvoke = AsyncMock(return_value=MagicMock(content="A"))
    monkeypatch.setattr(llm_invoke, "llm", MagicMock(ainvoke=ainvoke))

    await llm_invoke.llm_meaning_analysis(
        {
            "full_text": "全文",
            "word_parsing_return": {"contents": {"5 总结": "这是总结"}},
        },
        {},
        {},
    )

    prompt = ainvoke.await_args.args[0][0].content
    assert "报告全文" in prompt
    assert "报告章节内容" not in prompt


@pytest.mark.asyncio
async def test_meaning_prompt_falls_back_to_contents(monkeypatch):
    ainvoke = AsyncMock(return_value=MagicMock(content="A"))
    monkeypatch.setattr(llm_invoke, "llm", MagicMock(ainvoke=ainvoke))

    await llm_invoke.llm_meaning_analysis(
        {
            "full_text": "",
            "word_parsing_return": {"contents": {"5 总结": "这是总结"}},
        },
        {},
        {},
    )

    prompt = ainvoke.await_args.args[0][0].content
    assert "报告章节内容" in prompt
    assert "5 总结" in prompt


@pytest.mark.asyncio
async def test_meaning_analysis_calls_llm_when_only_full_text(monkeypatch):
    ainvoke = AsyncMock(return_value=MagicMock(content="A"))
    monkeypatch.setattr(llm_invoke, "llm", MagicMock(ainvoke=ainvoke))

    await llm_invoke.llm_meaning_analysis(
        {
            "full_text": "全文",
            "word_parsing_return": {"contents": dp._missing_contents()},
        },
        {},
        {},
    )

    ainvoke.assert_awaited_once()


def test_non_docx_message_has_no_path(tmp_path):
    path = tmp_path / "report.pdf"
    path.write_bytes(b"x")

    with pytest.raises(ValueError) as exc:
        dp._load_document(str(path))

    assert "不是 .docx" in str(exc.value)
    assert str(path) not in str(exc.value)


def test_invalid_docx_message_has_no_path(tmp_path):
    path = tmp_path / "broken.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("hello.txt", "x")

    with pytest.raises(ValueError) as exc:
        dp._load_document(str(path))

    assert "不是合法的 .docx 文件" in str(exc.value)
    assert str(path) not in str(exc.value)


def test_source_read_error_has_no_path(tmp_path, monkeypatch):
    path = tmp_path / "main.cpp"
    path.write_text("int main(){}", encoding="utf-8")

    def boom(self):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "read_bytes", boom)

    with pytest.raises(ValueError) as exc:
        dp._read_source(str(path))

    assert str(path) not in str(exc.value)
