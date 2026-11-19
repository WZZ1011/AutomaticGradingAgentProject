"""Word 报告解析模块（模块 2）。

对外入口只有 ``doc_parsing(doc_id, code_id) -> dict``。内部按三层组织：

- 加载层：把 docx 完整结构读进来（不是只取纯文本）；
- 抽取层：按固定章节切块，代码片段走哨兵标记；
- 校验层：格式/章节/代码一致性结果按规则项计数，**只记录不抛异常**。

只支持官方固定模板；不支持自由格式文档。详细契约与决策见
记忆目录的 ``doc-parsing-plan.md``。

⚠️ 与沙箱门面一样，本文件位于 ``core/`` 但会反向 import ``services`` 取
``Document`` / ``Code`` 记录。实现时保持“入口全异常降级”，不要向上抛。
"""

from __future__ import annotations

import codecs
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from docx import Document as DocxDocument
from docx.oxml.ns import qn
from docx.shared import Length
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from automaticgradingagentproject.core.database import SessionLocal
from automaticgradingagentproject.core.enums.format_error_enum import DocFormatCheck
from automaticgradingagentproject.core.enums.rule_type_enum import RuleTypeEnum
from automaticgradingagentproject.services import marking_service

# 必需章节（宽松口径：全文至少出现一次即可，不要求每个专题都出现）。
_APPENDIX = "附录"
REQUIRED_CHAPTERS: tuple[str, ...] = (
    "1 题目与要求",
    "2 功能设计",
    "3 功能代码",
    "4 调试与测试",
    "5 总结",
    _APPENDIX,
)


def _chapter_regex(canonical: str, *, numbered: bool) -> re.Pattern[str]:
    """由章节全名生成匹配式；``numbered=False`` 时编号可省略（自动编号标题用）。"""
    match = re.match(r"^(\d+)\s+(.+)$", canonical)
    if match:
        number, name = match.groups()
        prefix = rf"{number}\s*[\.、．]?\s*" if numbered else ""
    else:
        name = canonical
        prefix = ""
    if name == _APPENDIX:
        body = r"附\s*录\s*[A-Za-z0-9一二三四五六七八九十]{0,3}"
    else:
        body = r"\s*".join(re.escape(char) for char in name)
    return re.compile(rf"^\s*{prefix}{body}\s*(?:[（(][^）)]*[）)])?\s*$")


# 固定标题匹配：允许 “5总结”“1. 题目与要求”，也允许模板自带的括号说明，
# 例如 “1 题目与要求（左对齐，宋体粗四号）”。
_CHAPTER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (canonical, _chapter_regex(canonical, numbered=True)) for canonical in REQUIRED_CHAPTERS
)
# Word 自动编号不会把编号写进 ``Paragraph.text``；样式命中时省略编号再匹配一次。
_BARE_CHAPTER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (canonical, _chapter_regex(canonical, numbered=False)) for canonical in REQUIRED_CHAPTERS
)

# 代码哨兵：独占一行。模板用 HTML 注释写法；旧样本的方括号写法继续兼容。
CODE_BEGIN_PREFIX = "<!-- CODE_BEGIN -->"
CODE_END_MARKER = "<!-- CODE_END -->"
# 模板自带的占位行：学生没粘代码时按空片段处理，不拿去和源码比对。
CODE_PLACEHOLDER = "请将代码粘贴到此处"

_COMMENT_BEGIN_RE = re.compile(r"^<!--\s*CODE_BEGIN\s*-->\s*$", re.IGNORECASE)
_COMMENT_END_RE = re.compile(r"^<!--\s*CODE_END\s*-->\s*$", re.IGNORECASE)
_CODE_BEGIN_RE = re.compile(r"^\[CODE_BEGIN:([^\]]*)\]\s*$", re.IGNORECASE)
_CODE_END_RE = re.compile(r"^\[CODE_END\]\s*$", re.IGNORECASE)
# 中文输入法可能给出全角括号/冒号，识别前先归一。
_MARKER_TRANSLATION = str.maketrans({"［": "[", "］": "]", "【": "[", "】": "]", "：": ":"})

# 兜底字号：一级标题四号 14pt；任务书里的“附录”用小四 12pt。
_DEFAULT_FALLBACK_SIZES: tuple[float, ...] = (14.0,)
_FALLBACK_SIZES: dict[str, tuple[float, ...]] = {
    _APPENDIX: (12.0, 14.0),
}

# 文件体积上限：防止超大文件/压缩炸弹拖垮批阅进程。
_MAX_DOCX_BYTES = 20 * 1024 * 1024
_MAX_DOCX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
_MAX_XML_PART_BYTES = 32 * 1024 * 1024
_MAX_COMPRESSION_RATIO = 100
_MAX_SOURCE_BYTES = 5 * 1024 * 1024

# 代码片段与源码比对前的全角标点归一。
_FULLWIDTH_TRANSLATION = str.maketrans(
    {
        "，": ",",
        "。": ".",
        "；": ";",
        "：": ":",
        "！": "!",
        "？": "?",
        "（": "(",
        "）": ")",
        "【": "[",
        "】": "]",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "、": ",",
        "　": " ",
    }
)

# 小节标题（1.1 / 2.3.1），用于行距校验时跳过标题段。
_NUMBERED_HEADING_RE = re.compile(r"^\s*\d+(?:[\.、．]\d+)+\s*\S")


@dataclass
class ChapterBlock:
    """抽取层的内部章节块。

    ``contents`` 只取 ``text``；其余字段留给校验层定位与降级提示。
    ``lines`` 保留（正文块序号, 文本），供抽取层剔除代码片段。

    坐标空间：``start_index`` / ``end_index`` 是 ``_iter_body_items`` 扁平化后
    的正文条目 0 基下标（块级内容控件已展开），闭区间；``lines`` 序号同一坐标。
    """

    title: str
    text: str
    start_index: int
    end_index: int
    via_fallback: bool = False  # True = 该章靠“格式兜底”而不是标题样式命中
    lines: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class CodeSnippet:
    """哨兵标记圈出的代码片段。``filename`` 允许为空串。

    ``start_index`` / ``end_index`` 与 ``ChapterBlock`` 使用同一坐标空间
    （扁平化后的正文条目 0 基下标，闭区间），标记行本身计入区间，便于从
    contents 剔除。
    """

    filename: str
    text: str
    start_index: int
    end_index: int


@dataclass
class _ResolvedInputs:
    """从 DB 取出的路径与配对信息（关 Session 前完成取值）。"""

    doc_path: str
    doc_file_name: str
    code_path: str
    doc_user_id: str
    doc_topic_id: int
    code_user_id: str
    code_topic_id: int


_W_P = qn("w:p")
_W_TBL = qn("w:tbl")
_W_SDT = qn("w:sdt")
_W_SDT_CONTENT = qn("w:sdtContent")
_W_R = qn("w:r")
_W_T = qn("w:t")
_W_TAB = qn("w:tab")
_W_BR = qn("w:br")


def _iter_block_elements(container: Any) -> Iterator[Any]:
    """按文档顺序产出段落/表格元素，递归展开块级内容控件 ``w:sdt``。"""
    for child in container.iterchildren():
        if child.tag in (_W_P, _W_TBL):
            yield child
        elif child.tag == _W_SDT:
            content = child.find(_W_SDT_CONTENT)
            if content is not None:
                yield from _iter_block_elements(content)


def _iter_body_items(document: Any) -> Iterator[Any]:
    """按文档顺序产出正文里的段落和表格（不含页眉页脚）。

    块级 ``w:sdt`` 会展开成 ``w:sdtContent`` 内的条目；所有消费方必须使用
    同一个迭代器，坐标才是同一套扁平化下标。
    """
    for element in _iter_block_elements(document.element.body):
        if element.tag == _W_P:
            yield Paragraph(element, document)
        else:
            yield Table(element, document)


def _paragraph_text(paragraph: Paragraph) -> str:
    """递归收集段落内全部 ``w:t``（含超链接、内联内容控件、修订插入）。"""
    parts: list[str] = []
    for element in paragraph._p.iter():
        if element.tag == _W_T:
            parts.append(element.text or "")
        elif element.tag == _W_TAB:
            parts.append("\t")
        elif element.tag == _W_BR:
            parts.append("\n")
    return "".join(parts)


def _item_text(item: Any) -> str:
    """段落取递归文本；表格按行拼接单元格内各段落的递归文本。"""
    if isinstance(item, Paragraph):
        return _paragraph_text(item)
    if isinstance(item, Table):
        rows = []
        seen_cells: set[Any] = set()
        for row in item.rows:
            cells = []
            for cell in row.cells:
                # 合并单元格会重复返回同一个 w:tc；持有元素本身去重（不能用 id，
                # 代理对象被回收后地址会复用）。
                if cell._tc in seen_cells:
                    continue
                seen_cells.add(cell._tc)
                cells.append("\n".join(_paragraph_text(p) for p in cell.paragraphs))
            rows.append("\t".join(cells))
        return "\n".join(rows)
    return ""


def _iter_text_lines(item: Any) -> Iterator[str]:
    """条目内的文本行：段落一行，表格逐单元格段落展开（哨兵识别用）。"""
    if isinstance(item, Paragraph):
        yield _paragraph_text(item)
    elif isinstance(item, Table):
        seen_cells: set[Any] = set()
        for row in item.rows:
            for cell in row.cells:
                if cell._tc in seen_cells:
                    continue
                seen_cells.add(cell._tc)
                for paragraph in cell.paragraphs:
                    yield _paragraph_text(paragraph)


def _style_chain(style: Any) -> Iterator[Any]:
    """沿 ``base_style`` 回溯；``basedOn`` 成环时安全停止。

    直接持有 ``style.element`` 去重：python-docx 每次跳转都可能新建代理，
    只存 ``id`` 会在代理回收后把合法链误判成环。
    """
    seen: set[Any] = set()
    while style is not None:
        element = style.element
        if element in seen:
            return
        seen.add(element)
        yield style
        style = style.base_style


def _iter_runs(paragraph: Paragraph) -> Iterator[Run]:
    """段落内全部 run，含超链接、``w:ins`` 等包裹层里的 run。"""
    for element in paragraph._p.iter(_W_R):
        yield Run(element, paragraph)


def _run_bold(run: Run) -> bool | None:
    """run 的有效加粗：直排 → 字符样式链；都未声明时返回 None。"""
    if run.bold is not None:
        return bool(run.bold)
    if run.style is not None:
        for style in _style_chain(run.style):
            if style.font.bold is not None:
                return bool(style.font.bold)
    return None


def _run_font_size(run: Run) -> float | None:
    """run 的有效字号（pt）：直排 → 字符样式链。"""
    if run.font.size is not None:
        return run.font.size.pt
    if run.style is not None:
        for style in _style_chain(run.style):
            if style.font.size is not None:
                return style.font.size.pt
    return None


def _paragraph_is_bold(paragraph: Paragraph) -> bool:
    """段落是否加粗：任一非空 run 有效加粗，或段落样式链声明加粗。"""
    for run in _iter_runs(paragraph):
        if run.text.strip() and _run_bold(run):
            return True
    for style in _style_chain(paragraph.style):
        if style.font.bold:
            return True
    return False


def _paragraph_font_size(paragraph: Paragraph) -> float | None:
    """段落主体字号（pt）：按非空 run 文本量取众数，其次段落样式链。"""
    weights: dict[float, int] = {}
    for run in _iter_runs(paragraph):
        text = run.text.strip()
        if not text:
            continue
        size = _run_font_size(run)
        if size is not None:
            weights[size] = weights.get(size, 0) + len(text)
    if weights:
        return max(weights, key=lambda size: (weights[size], size))
    for style in _style_chain(paragraph.style):
        if style.font.size is not None:
            return style.font.size.pt
    return None


def _outline_value(pPr: Any) -> int | None:
    """从 ``w:pPr`` 读 ``w:outlineLvl``（0 表示一级）；没有/非法返回 None。"""
    if pPr is None:
        return None
    element = pPr.find(qn("w:outlineLvl"))
    if element is None:
        return None
    try:
        return int(element.get(qn("w:val")))
    except (TypeError, ValueError):
        return None


_HEADING_STYLE_RE = re.compile(r"^(?:heading|标题)\s*([1-9])\s*$")
_EXPLICIT_NUMBER_RE = re.compile(r"^\s*\d")


def _match_chapter(text: str) -> str | None:
    for canonical, pattern in _CHAPTER_PATTERNS:
        if pattern.match(text):
            return canonical
    return None


def _match_chapter_bare(text: str) -> str | None:
    """编号可省略的章节名匹配（自动编号标题的文本里没有编号）。"""
    for canonical, pattern in _BARE_CHAPTER_PATTERNS:
        if pattern.match(text):
            return canonical
    return None


def _heading_level(paragraph: Paragraph) -> int | None:
    """段落标题级别（1 起）：大纲级别优先，其次 Heading/标题样式；正文为 None。

    只走一遍样式链：每次 ``style.base_style`` 在 python-docx 里都是一次
    全样式表查找，分开查大纲级别和 Heading 名会让开销翻倍。
    """
    outline = _outline_value(paragraph._p.pPr)
    style_level: int | None = None
    for style in _style_chain(paragraph.style):
        if outline is None:
            outline = _outline_value(style.element.find(qn("w:pPr")))
        if style_level is None:
            match = _HEADING_STYLE_RE.match((style.name or "").strip().lower())
            if match:
                style_level = int(match.group(1))
        if outline is not None and style_level is not None:
            break
    if outline is not None and outline < 9:
        return outline + 1
    return style_level


def _is_fallback_heading(paragraph: Paragraph, canonical: str) -> bool:
    """格式兜底：固定标题文字 + 加粗 + 字号匹配。"""
    if not _paragraph_is_bold(paragraph):
        return False
    size = _paragraph_font_size(paragraph)
    if size is None:
        return False
    allowed = _FALLBACK_SIZES.get(canonical, _DEFAULT_FALLBACK_SIZES)
    return any(abs(size - expected) < 0.1 for expected in allowed)


def _is_numbered_heading(paragraph: Paragraph, text: str) -> bool:
    """形如 “2.1 数据结构定义” 的加粗小节标题。"""
    return bool(_NUMBERED_HEADING_RE.match(text)) and _paragraph_is_bold(paragraph)


def _styles_element(paragraph: Paragraph) -> Any:
    """取文档样式表元素；页脚段落也能访问（StoryPart 的 _document_part）。"""
    document_part = getattr(paragraph.part, "_document_part", None)
    if document_part is None:
        return None
    try:
        return document_part.styles.element
    except AttributeError:
        return None


def _jc_value(pPr: Any) -> str | None:
    """从 ``w:pPr`` 读原始 ``w:jc/@w:val``。"""
    if pPr is None:
        return None
    jc = pPr.find(qn("w:jc"))
    if jc is None:
        return None
    return jc.get(qn("w:val"))


def _normalize_jc(value: str | None) -> str | None:
    """把 OOXML 的 start/end 归一为 left/right。"""
    if value is None:
        return None
    lower = value.lower()
    return {"start": "left", "end": "right"}.get(lower, lower)


def _doc_defaults_line_spacing(paragraph: Paragraph) -> float | None:
    """``w:docDefaults/w:pPrDefault`` 里的行距倍数；固定值返回 None。"""
    styles_element = _styles_element(paragraph)
    if styles_element is None:
        return None
    pPr = styles_element.find(
        f"{qn('w:docDefaults')}/{qn('w:pPrDefault')}/{qn('w:pPr')}"
    )
    if pPr is None:
        return None
    spacing = pPr.find(qn("w:spacing"))
    if spacing is None:
        return None
    if spacing.get(qn("w:lineRule")) not in (None, "auto"):
        return None
    line = spacing.get(qn("w:line"))
    if line is None:
        return None
    try:
        return int(line) / 240.0
    except ValueError:
        return None


def _effective_line_spacing(paragraph: Paragraph) -> float | None:
    """段落有效行距倍数；固定值/最小值返回 None。未设置时按文档默认。"""
    spacing = paragraph.paragraph_format.line_spacing
    if spacing is None:
        for style in _style_chain(paragraph.style):
            spacing = style.paragraph_format.line_spacing
            if spacing is not None:
                break
    if spacing is None:
        return _doc_defaults_line_spacing(paragraph)
    if isinstance(spacing, Length):
        return None
    if isinstance(spacing, (int, float)):
        return float(spacing)
    return None


def _marker_label(filename: str, index: int) -> str:
    return filename or f"第 {index + 1} 个内容块"


def _load_document(path: str) -> Any:
    """打开 docx，返回 python-docx 的完整结构对象。

    约束：
    - 必须保留段落样式、run 格式、页眉页脚、节等结构，不能只取 ``paragraph.text``。
    - 文件不存在、扩展名不对、不是合法 docx → 直接抛异常，由 ``doc_parsing``
      统一降级进 ``document_no_result``。
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError("文档不存在")
    if file_path.suffix.lower() != ".docx":
        raise ValueError("不是 .docx 文件")
    try:
        size = file_path.stat().st_size
    except OSError as exc:
        raise ValueError("文档文件无法访问") from exc
    if size > _MAX_DOCX_BYTES:
        raise ValueError("报告文件超过 20MB，请压缩图片后重新上传")
    try:
        with zipfile.ZipFile(file_path) as archive:
            uncompressed = 0
            for info in archive.infolist():
                uncompressed += info.file_size
                if (
                    info.filename.lower().endswith((".xml", ".rels"))
                    and info.file_size > _MAX_XML_PART_BYTES
                ):
                    raise ValueError(
                        "报告文件解压后单个 XML 部件过大，请压缩图片后重新上传"
                    )
                if (
                    info.compress_size > 0
                    and info.file_size > info.compress_size * _MAX_COMPRESSION_RATIO
                ):
                    raise ValueError("报告文件压缩比异常，请重新导出后上传")
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("不是合法的 .docx 文件") from exc
    if uncompressed > _MAX_DOCX_UNCOMPRESSED_BYTES:
        raise ValueError("报告文件解压后超过 200MB，请压缩后重新上传")
    try:
        return DocxDocument(str(file_path))
    except Exception as exc:
        raise ValueError("不是合法的 .docx 文件") from exc


_BOM_ENCODINGS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def _read_source(path: str) -> str:
    """读源码文本：BOM 检测 → utf-8 → gbk → utf-8(errors="replace")。

    与沙箱门面保持同一口径；不要因编码猜错让整单失败。UTF-16/32 靠 BOM 识别，
    避免被 gbk 静默解成 NUL 交错的乱码后误报“代码不一致”。
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError("源码文件不存在")
    try:
        size = file_path.stat().st_size
    except OSError as exc:
        raise ValueError("源码文件无法访问") from exc
    if size > _MAX_SOURCE_BYTES:
        raise ValueError("源码文件超过 5MB，请精简后重新上传")
    try:
        data = file_path.read_bytes()
    except OSError as exc:
        raise ValueError("源码文件不可读") from exc
    for bom, encoding in _BOM_ENCODINGS:
        if data.startswith(bom):
            return data.decode(encoding)
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _locate_chapters(document: Any) -> list[ChapterBlock]:
    """按固定章节切块，保持文档顺序。

    定位规则：
    1. 优先：Heading 1/2 样式或一级/二级大纲级别（``outlineLvl``）。
       只有一级标题允许省略编号（Word 自动编号不写进文本）；二级标题必须
       自带编号，避免章内名为“总结”“附录”的小节被当成章节；
    2. 兜底：固定标题文字 + 加粗 + 字号（一级四号 ``w:sz=28``，
       二级小四 ``w:sz=24``），只对完全没有标题样式/级别的段落生效；
    3. 兜底命中时置 ``via_fallback=True``，仅作内部标记，不记错误。

    任务书确认一份报告依次写三个专题，同名章节可能多次出现：每次都要保留。
    """
    items = list(_iter_body_items(document))
    located: list[tuple[int, str, bool]] = []
    for index, item in enumerate(items):
        if not isinstance(item, Paragraph):
            continue
        text = _paragraph_text(item).strip()
        if not text:
            continue
        canonical = _match_chapter(text)
        if canonical is None:
            # 文本不像章节名就先不解析样式（样式链查找很贵）；只有裸名命中
            # 且确实是一级标题时才认。
            canonical = _match_chapter_bare(text)
            if canonical is None or _heading_level(item) != 1:
                continue
            located.append((index, canonical, False))
            continue
        level = _heading_level(item)
        if level == 2 and not _EXPLICIT_NUMBER_RE.match(text):
            continue
        if level in (1, 2):
            located.append((index, canonical, False))
        elif level is None and _is_fallback_heading(item, canonical):
            located.append((index, canonical, True))

    blocks: list[ChapterBlock] = []
    for position, (start, canonical, via_fallback) in enumerate(located):
        if position + 1 < len(located):
            end = located[position + 1][0] - 1
        else:
            end = len(items) - 1
        lines: list[tuple[int, str]] = []
        for index in range(start, end + 1):
            text = _item_text(items[index]).strip()
            if text:
                lines.append((index, text))
        blocks.append(
            ChapterBlock(
                title=canonical,
                text="\n".join(text for _, text in lines),
                start_index=start,
                end_index=end,
                via_fallback=via_fallback,
                lines=lines,
            )
        )
    return blocks


def _extract_content(
    blocks: list[ChapterBlock],
    snippets: list[CodeSnippet],
    marker_indices: set[int],
) -> dict[str, str]:
    """生成 ``contents``：章节名 → 该章文本（str），按文档顺序；剔除代码片段。

    ``marker_indices`` 里是未配对（如多余 ``[CODE_END]``）的标记行，同样剔除，
    避免标记文本污染正文。同名章节（三个专题）按顺序用 ``\n`` 续接到同一 key，
    不丢文本。

    六个必需章节里，**没定位到**或**定位到但除标题外没有正文**的，统一补一条
    ``f"chapter_none_{章节名}": "none"``，让下游按章节名取值时永远拿得到键。
    """
    excluded = set(marker_indices)
    for snippet in snippets:
        excluded.update(range(snippet.start_index, snippet.end_index + 1))
    contents: dict[str, str] = {}
    for block in blocks:
        lines = block.lines or [(block.start_index, block.text)]
        kept = [text for index, text in lines if index not in excluded]
        body = [text for text in kept[1:] if text.strip()]  # 首行是章节标题
        has_snippet = any(
            snippet.start_index <= block.end_index
            and snippet.end_index >= block.start_index
            for snippet in snippets
        )
        # 只有标题、又没有代码片段 → 视作“没有内容”
        if not body and not has_snippet:
            continue
        text = "\n".join(kept).strip()
        if not text:
            continue
        # 同章节名重复出现（异常情况）时按顺序续接，不丢文本
        if block.title in contents:
            contents[block.title] += "\n" + text
        else:
            contents[block.title] = text
    for required in REQUIRED_CHAPTERS:
        if required not in contents:
            contents[f"chapter_none_{required}"] = "none"
    return contents


def _match_begin_marker(text: str) -> str | None:
    """识别 BEGIN 哨兵；返回文件名（模板写法没有文件名，返回空串）。"""
    if _COMMENT_BEGIN_RE.match(text):
        return ""
    match = _CODE_BEGIN_RE.match(text)
    if match:
        return match.group(1).strip()
    return None


def _is_end_marker(text: str) -> bool:
    return bool(_COMMENT_END_RE.match(text) or _CODE_END_RE.match(text))


def _full_text_line(raw_line: str) -> str | None:
    """过滤空行、哨兵行、占位行；保留原始行（含代码缩进）。"""
    text = raw_line.strip()
    if not text or text == CODE_PLACEHOLDER:
        return None
    marker_text = text.translate(_MARKER_TRANSLATION)
    if _match_begin_marker(marker_text) is not None or _is_end_marker(marker_text):
        return None
    return raw_line


def _table_full_text_lines(table: Table) -> list[str]:
    """表格按行输出：单元格以制表符分隔，单元格内段落以换行分隔。"""
    rows: list[str] = []
    for row in table.rows:
        cells: list[str] = []
        seen_cells: set[Any] = set()
        for cell in row.cells:
            if cell._tc in seen_cells:
                continue
            seen_cells.add(cell._tc)
            cell_lines: list[str] = []
            for paragraph in cell.paragraphs:
                for raw_line in _paragraph_text(paragraph).splitlines():
                    line = _full_text_line(raw_line)
                    if line is not None:
                        cell_lines.append(line)
            cells.append("\n".join(cell_lines))
        row_text = "\t".join(cells)
        if row_text.strip():
            rows.append(row_text)
    return rows


def _extract_full_text(document: Any) -> str:
    """按文档顺序拼接正文全部文本，供下游大模型使用。

    段落与表格都算；表格按行保留（单元格以制表符分隔）。只过滤空行、代码
    哨兵行与模板占位行；代码片段本身与行首缩进保留。
    """
    lines: list[str] = []
    for item in _iter_body_items(document):
        if isinstance(item, Paragraph):
            for raw_line in _paragraph_text(item).splitlines():
                line = _full_text_line(raw_line)
                if line is not None:
                    lines.append(line)
        else:
            lines.extend(_table_full_text_lines(item))
    return "\n".join(lines)


def _extract_code_snippets(
    document: Any,
    blocks: list[ChapterBlock],
) -> tuple[list[CodeSnippet], list[str], set[int]]:
    """收集 ``<!-- CODE_BEGIN -->`` / ``<!-- CODE_END -->`` 之间的代码块。

    旧样本的 ``[CODE_BEGIN:文件名]`` / ``[CODE_END]`` 写法仍兼容；模板自带的
    占位行 ``请将代码粘贴到此处`` 不计入片段文本（未粘代码 → 报“空片段”）。

    返回 ``(片段列表, 问题消息列表, 标记所在条目下标集合)``；未闭合、空块、
    全文没有标记都只记录问题，不在这里抛异常。表格单元格里的段落参与扫描；
    哨兵区间遇到章节标题时强制截断，避免错位的 ``[CODE_END]`` 吞掉后续章节。
    """
    items = list(_iter_body_items(document))
    chapter_starts = {block.start_index for block in blocks}
    snippets: list[CodeSnippet] = []
    issues: list[str] = []
    marker_indices: set[int] = set()
    open_index: int | None = None
    open_name = ""
    inner_lines: list[str] = []
    saw_marker = False

    for index, item in enumerate(items):
        if open_index is not None and index in chapter_starts:
            issues.append(
                f"代码标记未闭合：{_marker_label(open_name, open_index)} "
                f"在下一章节前截断，缺少 {CODE_END_MARKER}"
            )
            snippets.append(
                CodeSnippet(
                    filename=open_name,
                    text="\n".join(inner_lines),
                    start_index=open_index,
                    end_index=max(open_index, index - 1),
                )
            )
            open_index = None
            open_name = ""
            inner_lines = []

        for line in _iter_text_lines(item):
            text = line.strip()
            marker_text = text.translate(_MARKER_TRANSLATION)
            begin_name = _match_begin_marker(marker_text)
            if begin_name is not None:
                saw_marker = True
                marker_indices.add(index)
                if open_index is not None:
                    issues.append(
                        f"代码标记未闭合：{_marker_label(open_name, open_index)} 缺少 {CODE_END_MARKER}"
                    )
                    snippets.append(
                        CodeSnippet(
                            filename=open_name,
                            text="\n".join(inner_lines),
                            start_index=open_index,
                            end_index=max(open_index, index - 1),
                        )
                    )
                open_index = index
                open_name = begin_name
                inner_lines = []
                continue

            if _is_end_marker(marker_text):
                saw_marker = True
                marker_indices.add(index)
                if open_index is None:
                    issues.append(
                        f"代码标记不配对：第 {index + 1} 个内容块出现多余的 {CODE_END_MARKER}"
                    )
                    continue
                if not any(inner.strip() for inner in inner_lines):
                    issues.append(f"代码片段为空：{_marker_label(open_name, open_index)}")
                snippets.append(
                    CodeSnippet(
                        filename=open_name,
                        text="\n".join(inner_lines),
                        start_index=open_index,
                        end_index=index,
                    )
                )
                open_index = None
                open_name = ""
                inner_lines = []
                continue

            if open_index is not None:
                if text != CODE_PLACEHOLDER:
                    inner_lines.append(line)

    if open_index is not None:
        issues.append(
            f"代码标记未闭合：{_marker_label(open_name, open_index)} 缺少 {CODE_END_MARKER}"
        )
        # 与“下一章节前截断”同口径：已收集的内容按代码处理，区间到文末。
        snippets.append(
            CodeSnippet(
                filename=open_name,
                text="\n".join(inner_lines),
                start_index=open_index,
                end_index=len(items) - 1,
            )
        )
    if not saw_marker:
        issues.append(f"未找到代码标记：{CODE_BEGIN_PREFIX} 与 {CODE_END_MARKER}")
    return snippets, issues, marker_indices


def _check_line_spacing(
    document: Any,
    snippets: list[CodeSnippet],
    marker_indices: set[int],
) -> list[str]:
    """检查正文非空段落的行距是否为 1.25 倍（``w:line=300`` + ``auto``）。

    范围从首个「文本命中章节名」的段落一直到文末；一个标题都识别不出时跳过
    检查（缺失由 ``chapter_missing`` 体现，不再回退整篇）。章节标题、任意级别
    标题样式、编号小节标题、代码标记与片段区间、表格、页眉页脚跳过。
    消息按“汇总一条 + 首个位置”粒度，位置同时给正文段落序号与正文条目序号
    （后者与代码标记消息同一坐标）。段落文本与标题级别只算一次，两个循环共用。
    """
    items = list(_iter_body_items(document))
    texts: dict[int, str] = {}
    levels: dict[int, int | None] = {}
    title_indices: set[int] = set()
    for index, item in enumerate(items):
        if not isinstance(item, Paragraph):
            continue
        text = _paragraph_text(item).strip()
        if not text:
            continue
        texts[index] = text
        if _match_chapter(text) is not None:
            title_indices.add(index)
        elif _match_chapter_bare(text) is not None:
            level = levels[index] = _heading_level(item)
            if level in (1, 2):
                title_indices.add(index)
    if not title_indices:
        return []
    scope_start, scope_end = min(title_indices), len(items) - 1
    excluded = set(marker_indices)
    for snippet in snippets:
        excluded.update(range(snippet.start_index, snippet.end_index + 1))

    bad: list[int] = []
    for index, item in enumerate(items):
        if not isinstance(item, Paragraph):
            continue
        if not (scope_start <= index <= scope_end):
            continue
        text = texts.get(index)
        if not text:
            continue
        if index in title_indices or index in excluded:
            continue
        if index in levels:
            level = levels[index]
        else:
            level = levels[index] = _heading_level(item)
        if level is not None or _is_numbered_heading(item, text):
            continue
        spacing = _effective_line_spacing(item)
        if spacing is None or abs(spacing - 1.25) > 0.01:
            bad.append(index)

    if not bad:
        return []
    first = bad[0]
    paragraph_no = sum(
        1
        for index, item in enumerate(items)
        if isinstance(item, Paragraph) and index <= first
    )
    return [
        f"共 {len(bad)} 段行距不是 1.25 倍"
        f"（首个为正文第 {paragraph_no} 段，即第 {first + 1} 个内容块）"
    ]


_PAGE_INSTR_RE = re.compile(r"\bPAGE\b", re.IGNORECASE)
_EXPECTED_PAGE_FONT = "times new roman"
_EXPECTED_PAGE_SIZE_PT = 9.0  # 小五

_JC_LABELS: dict[str, str] = {
    "left": "左对齐",
    "center": "居中",
    "right": "靠右",
    "both": "两端对齐",
    "distribute": "分散对齐",
    "start": "左对齐",
    "end": "右对齐",
}


def _doc_defaults_rpr(paragraph: Paragraph) -> Any:
    """``w:docDefaults/w:rPrDefault/w:rPr``；取不到时返回 None。"""
    styles_element = _styles_element(paragraph)
    if styles_element is None:
        return None
    return styles_element.find(
        f"{qn('w:docDefaults')}/{qn('w:rPrDefault')}/{qn('w:rPr')}"
    )


def _effective_font_name(run: Run | None, paragraph: Paragraph) -> str | None:
    """有效西文字体：run 直排 → 字符样式链 → 段落样式链 → docDefaults。"""
    if run is not None:
        if run.font.name:
            return run.font.name
        if run.style is not None:
            for style in _style_chain(run.style):
                if style.font.name:
                    return style.font.name
    for style in _style_chain(paragraph.style):
        if style.font.name:
            return style.font.name
    rPr = _doc_defaults_rpr(paragraph)
    if rPr is not None:
        fonts = rPr.find(qn("w:rFonts"))
        if fonts is not None:
            return fonts.get(qn("w:ascii")) or fonts.get(qn("w:hAnsi"))
    return None


def _effective_font_size_pt(run: Run | None, paragraph: Paragraph) -> float | None:
    """有效字号（pt）：run 直排 → 字符样式链 → 段落样式链 → docDefaults。"""
    size = _run_font_size(run) if run is not None else None
    if size is not None:
        return size
    for style in _style_chain(paragraph.style):
        if style.font.size is not None:
            return style.font.size.pt
    rPr = _doc_defaults_rpr(paragraph)
    if rPr is not None:
        sz = rPr.find(qn("w:sz"))
        if sz is not None and sz.get(qn("w:val")):
            try:
                return int(sz.get(qn("w:val"))) / 2.0
            except ValueError:
                return None
    return None


def _effective_alignment(paragraph: Paragraph) -> str | None:
    """有效对齐（小写 jc 值）：段落直接设置 → 段落样式链 → docDefaults。

    直接读 ``w:jc/@w:val``，避免 python-docx 对 start/end 抛 ValueError；
    start/end 归一为 left/right。
    """
    value = _jc_value(paragraph._p.pPr)
    if value is None:
        for style in _style_chain(paragraph.style):
            value = _jc_value(style.element.find(qn("w:pPr")))
            if value is not None:
                break
    if value is None:
        styles_element = _styles_element(paragraph)
        if styles_element is not None:
            pPr = styles_element.find(
                f"{qn('w:docDefaults')}/{qn('w:pPrDefault')}/{qn('w:pPr')}"
            )
            value = _jc_value(pPr)
    return _normalize_jc(value)


def _jc_label(value: str | None) -> str:
    if not value:
        return "未设置"
    return _JC_LABELS.get(value.lower(), value)


def _page_field_state(paragraph: Paragraph) -> tuple[bool, list[Run], list[Run]]:
    """返回 (是否含 PAGE 域, PAGE 结果 run, PAGE 域自身 run)。

    复杂域用栈跟踪嵌套；``instrText`` 可被拆成多段，累积到 ``separate`` 再判定。
    fldSimple 的 run 同时算结果 run 与域 run。
    """
    has_page = False
    result_runs: list[Run] = []
    code_runs: list[Run] = []

    for simple in paragraph._p.iter(qn("w:fldSimple")):
        if _PAGE_INSTR_RE.search(simple.get(qn("w:instr")) or ""):
            has_page = True
            for element in simple.iter(qn("w:r")):
                run = Run(element, paragraph)
                code_runs.append(run)
                result_runs.append(run)

    fields: list[dict[str, Any]] = []
    for run in _iter_runs(paragraph):
        touched = False
        for child in run._r.iterchildren():
            tag = child.tag
            if tag == qn("w:fldChar"):
                touched = True
                fld_type = child.get(qn("w:fldCharType"))
                if fld_type == "begin":
                    fields.append(
                        {"instr": "", "is_page": False, "phase": "instr", "runs": [run]}
                    )
                elif fields:
                    field = fields[-1]
                    if run not in field["runs"]:
                        field["runs"].append(run)
                    if fld_type == "separate":
                        is_page = _PAGE_INSTR_RE.search(field["instr"]) is not None
                        field["is_page"] = is_page
                        field["phase"] = "result" if is_page else "skip"
                        has_page = has_page or is_page
                    elif fld_type == "end":
                        fields.pop()
                        if not field["is_page"] and _PAGE_INSTR_RE.search(field["instr"]):
                            field["is_page"] = True
                            has_page = True
                        if field["is_page"]:
                            code_runs.extend(field["runs"])
            elif tag == qn("w:instrText"):
                touched = True
                if fields:
                    field = fields[-1]
                    if run not in field["runs"]:
                        field["runs"].append(run)
                    if field["phase"] == "instr":
                        field["instr"] += child.text or ""
            elif tag in (_W_T, _W_TAB, _W_BR):
                touched = True
                if fields:
                    field = fields[-1]
                    if run not in field["runs"]:
                        field["runs"].append(run)
                    if field["phase"] == "result" and field["is_page"]:
                        if run not in result_runs:
                            result_runs.append(run)
        if fields and not touched:
            field = fields[-1]
            if run not in field["runs"]:
                field["runs"].append(run)
            if field["phase"] == "result" and field["is_page"]:
                if run not in result_runs:
                    result_runs.append(run)

    for field in fields:  # 未闭合域
        if _PAGE_INSTR_RE.search(field["instr"]):
            field["is_page"] = True
            has_page = True
        if field["is_page"]:
            code_runs.extend(field["runs"])

    return has_page, result_runs, code_runs


def _paragraph_has_page_field(paragraph: Paragraph) -> bool:
    """段落里是否含 PAGE 域（``NUMPAGES`` / ``PAGEREF`` 不算）。"""
    return _page_field_state(paragraph)[0]


def _page_field_run(paragraph: Paragraph) -> Run | None:
    """承载页码显示的 run：结果 run 优先，其次 PAGE 域自身的域代码 run。

    Word 常把字体/字号只写在显示结果的 run 上；结果 run 即使为空也可能带
    rPr（域未刷新），同样优先于域代码 run。真实样本里也有结果为空、格式挂
    在 instrText run 上的情况，故域代码 run 仅作回退。
    """
    _, result_runs, code_runs = _page_field_state(paragraph)
    for run in result_runs:
        if run.text.strip():
            return run
    for run in result_runs:
        if run._r.find(qn("w:rPr")) is not None:
            return run
    for run in code_runs:
        if run._r.find(qn("w:instrText")) is not None:
            return run
    for run in code_runs:
        if run._r.find(qn("w:fldChar")) is not None:
            return run
    return result_runs[0] if result_runs else (code_runs[0] if code_runs else None)


_FOOTER_ATTRS: dict[str, str] = {
    "default": "footer",
    "first_page": "first_page_footer",
    "even_page": "even_page_footer",
}
_FOOTER_LABELS: dict[str, str] = {
    "default": "默认页脚",
    "first_page": "首页页脚",
    "even_page": "偶数页页脚",
}


def _even_and_odd_headers(document: Any) -> bool:
    """文档是否启用奇偶页不同页脚；settings 异常按未启用处理。"""
    try:
        return bool(document.settings.odd_and_even_pages_header_footer)
    except Exception:
        return False


def _different_first_page_header_footer(section: Any) -> bool:
    """首页页脚开关；非法 titlePg 按关闭处理。"""
    try:
        return bool(section.different_first_page_header_footer)
    except Exception:
        return False


def _section_footers(document: Any, index: int) -> list[str]:
    """本节可能生效的页脚类型：default / first_page / even_page。"""
    section = document.sections[index]
    kinds = ["default"]
    if _different_first_page_header_footer(section):
        kinds.append("first_page")
    if _even_and_odd_headers(document):
        kinds.append("even_page")
    return kinds


def _effective_footer(document: Any, index: int, kind: str) -> Any:
    """沿“链接到上一节”回溯，返回该类型实际生效的页脚；全无定义时返回 None。"""
    attr = _FOOTER_ATTRS[kind]
    for position in range(index, -1, -1):
        footer = getattr(document.sections[position], attr)
        if not footer.is_linked_to_previous:
            return footer
    return None


def _footer_key(footer: Any) -> str:
    """同一页脚定义只校验一次；partname 在同一包内唯一。"""
    try:
        return str(footer.part.partname)
    except (AttributeError, KeyError):
        return str(id(footer._element))


def _check_page_number(document: Any) -> list[str]:
    """页码校验：页脚要有 PAGE 域，且 Times New Roman / 小五（9pt）/ 靠右。

    只校验实际生效的页脚（default；首页不同时含 first_page；奇偶页不同时含
    even_page）。同一页脚定义只报一次（取首个节号）；无页脚的前置节不单独报
    “没有页码”，全文没有任何页脚时记一条。
    """
    messages: list[str] = []
    seen_footers: set[str] = set()
    has_page_number = False
    has_footer = False

    for index in range(len(document.sections)):
        section_no = index + 1
        for kind in _section_footers(document, index):
            footer = _effective_footer(document, index, kind)
            if footer is None:
                continue
            has_footer = True
            key = _footer_key(footer)
            if key in seen_footers:
                continue
            seen_footers.add(key)

            label = _FOOTER_LABELS[kind]
            field_paragraphs: list[Paragraph] = []
            for element in footer._element.iter(qn("w:p")):
                paragraph = Paragraph(element, footer)
                if _paragraph_has_page_field(paragraph):
                    field_paragraphs.append(paragraph)
            if not field_paragraphs:
                messages.append(f"第 {section_no} 节{label}：没有页码")
                continue

            has_page_number = True
            font_message: str | None = None
            size_message: str | None = None
            align_message: str | None = None
            parse_error = False
            for paragraph in field_paragraphs:
                try:
                    run = _page_field_run(paragraph)

                    if font_message is None:
                        font = _effective_font_name(run, paragraph)
                        if font is None or font.strip().lower() != _EXPECTED_PAGE_FONT:
                            font_message = (
                                f"第 {section_no} 节{label}：页码字体不是 Times New Roman"
                                f"（实际 {font or '未设置'}）"
                            )

                    if size_message is None:
                        size = _effective_font_size_pt(run, paragraph)
                        if size is None or abs(size - _EXPECTED_PAGE_SIZE_PT) > 0.01:
                            actual = "未设置" if size is None else f"{size:g} 磅"
                            size_message = (
                                f"第 {section_no} 节{label}：页码字号不是小五 9 磅"
                                f"（实际 {actual}）"
                            )

                    if align_message is None:
                        align = _effective_alignment(paragraph)
                        if (align or "").lower() != "right":
                            align_message = (
                                f"第 {section_no} 节{label}：页码没有靠右"
                                f"（实际 {_jc_label(align)}）"
                            )
                except Exception:
                    parse_error = True
                    continue

                if font_message and size_message and align_message:
                    break

            for message in (font_message, size_message, align_message):
                if message is not None:
                    messages.append(message)
            if parse_error:
                messages.append(f"第 {section_no} 节{label}：页码格式无法解析")

    if not has_page_number and not has_footer:
        messages.append("页脚：没有页码")
    return messages


_FILENAME_RE = re.compile(
    r"^(?P<student_id>[0-9]{8,12})-(?P<name>[一-鿿·]+)"
    r"-专题[一二三四五六七八九十百]+.*\.docx$",
    re.IGNORECASE,
)


def _check_filename(file_name: str) -> list[str]:
    """校验报告文件名：``8–12 位学号-姓名-专题X…docx``。

    ``file_name`` 是数据库里保存的上传原名；只校验命名格式，不比较上传账号
    （``user_id`` 不是学号）。历史记录（``file_name`` 为 NULL）没有原名可查，
    按“无法校验”返回 ``[]``，不制造假错误。
    """
    name = file_name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name:
        return []
    if _FILENAME_RE.match(name) is None:
        return [f"文件名不符合“学号-姓名-专题X.docx”规则（实际：{name}）"]
    return []


def _normalize_code_text(text: str) -> str:
    """统一换行、去行尾空白、去全部空白、归一常见全角标点。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip()
    text = text.translate(_FULLWIDTH_TRANSLATION)
    return re.sub(r"\s+", "", text)

def _check_code_consistency(snippets: list[CodeSnippet], source: str) -> list[str]:
    """把报告中的代码片段与源码文本做**宽松**一致性比对。

    口径：
    - 统一换行、去行尾空白、去首尾空行；
    - 去掉全部空白，并归一常见全角标点；
    - 规范化后的片段是源码文本的子串即通过。
    - 仅空白/标点差异视为通过，不记消息；确实对不上才记一条。

    局限（有意接受）：宽松口径可能放过“只差空白或全角标点”的真实错误。
    """
    if not snippets:
        return []
    normalized_source = _normalize_code_text(source)
    labels = [
        _marker_label(snippet.filename, snippet.start_index) for snippet in snippets
    ]
    duplicated = {label for label in labels if labels.count(label) > 1}
    messages: list[str] = []
    for position, snippet in enumerate(snippets, start=1):
        normalized = _normalize_code_text(snippet.text)
        if not normalized:
            continue
        if normalized not in normalized_source:
            label = labels[position - 1]
            if label in duplicated:
                # 同一正文条目里有多个片段（表格内常见）时补片段序号，避免消息重名。
                label = f"{label}（第 {position} 个代码片段）"
            messages.append(f"代码片段与源码不一致：{label}")
    return messages

def _resolve_paths(doc_id: int, code_id: int) -> _ResolvedInputs:
    """取 doc/code 记录，返回路径与配对所需字段。

    内部自建 ``SessionLocal`` 并负责关闭；查不到时抛 ``ValueError``，
    由 ``doc_parsing`` 统一降级。
    """
    db = SessionLocal()
    try:
        document = marking_service.get_document(db, doc_id)
        code = marking_service.get_code(db, code_id)
        return _ResolvedInputs(
            doc_path=str(document.word_doc),
            doc_file_name=str(document.file_name or ""),
            code_path=str(code.source_code),
            doc_user_id=str(document.user_id),
            doc_topic_id=int(document.topic_id),
            code_user_id=str(code.user_id),
            code_topic_id=int(code.topic_id),
        )
    finally:
        db.close()

def _missing_contents() -> dict[str, str]:
    """六个必需章节都标成缺失（加载失败等早期返回用）。"""
    return {f"chapter_none_{required}": "none" for required in REQUIRED_CHAPTERS}


def _record_failure(no_result: dict, stage: str, exc: Exception) -> None:
    """把某个检查阶段的异常记进 ``document_no_result``，不中断其余阶段。"""
    no_result["count"] += 1
    no_result["messages"].append(f"{stage}失败：{exc}")


def _answer(result: dict, full_text: str) -> dict:
    """模块 2 的最终返回：全文与大模型材料分开。"""
    return {"full_text": full_text, "word_parsing_return": result}


def doc_parsing(doc_id: int, code_id: int) -> dict:
    """模块 2 的对外入口。

    返回结构::

        {
            "full_text": str,          # 正文全部文本；加载失败为 ""
            "word_parsing_return": {
                RuleTypeEnum.FORMAT_ERROR: {
                    "count": int,                        # = 各子项 count 之和
                    DocFormatCheck.FILE_NAMING: {"count": int, "messages": list[str]},
                    DocFormatCheck.LINE_SPACING: {"count": int, "messages": list[str]},
                    DocFormatCheck.PAGE_NUMBER_FORMAT: {"count": int, "messages": list[str]},
                },
                RuleTypeEnum.CHAPTER_MISSING: {"count": int, "messages": list[str]},
                RuleTypeEnum.REPORT_CODE_MATCH: {
                    "count": int,
                    "messages": list[str],
                    "checked": bool,     # 是否真的与源码比对过
                },
                "contents": {                            # 章节名 → 文本（str）
                    "5 总结": "...",
                    "chapter_none_附录": "none",          # 缺失或只有标题、没有正文
                },
                "document_no_result": {"count": int, "messages": list[str]},
            },
        }

    契约：
    - 顶层只有 ``full_text`` 与 ``word_parsing_return`` 两个键；``full_text`` 恒在。
    - 规则项平铺在 ``word_parsing_return`` 里，键直接用枚举成员；枚举继承 ``str``，
      JSON 序列化后就是 ``format_error`` 等小写字符串，按枚举或字符串查找均可。
    - 叶子规则项的 ``count`` 恒等于 ``len(messages)``；聚合项 ``format_error`` 只给
      ``count``。``contents`` 与 ``document_no_result`` 恒在。
    - ``format_error.count`` 是派生值，恒等于 ``DocFormatCheck`` 各子项之和。
    - ``contents`` 是 ``dict[str, str]``（章节名 → 该章文本，段间 ``\n``）；六个必需
      章节键总在：没定位到、或定位到但除标题外没有正文的，补
      ``"chapter_none_章节名": "none"``。同名章节重复出现时按顺序用 ``\n`` 续接。
    - ``full_text``：正文全部文本（段落与表格按文档顺序，表格保留行结构），
      保留代码内容与行首缩进，只过滤空行、代码哨兵行与模板占位行；加载失败时为 ``""``。
    - **任何异常都不外抛**：加载/配对/阶段失败 → ``document_no_result``；
      学生问题 → 对应规则项的 ``count``。
    - 加载失败时 ``contents`` 只剩六个 ``chapter_none_*``、``full_text`` 为 ``""``、
      各规则项 count 为 0、``document_no_result.count=1``。
    - ``doc.user_id`` / ``topic_id`` 与 ``code`` 不一致：``document_no_result`` 记一条；
      消息不回显 ``user_id``（本系统里它就是登录密码）。``contents`` 仍返回，
      跳过一致性比对（``checked`` 为 False），但文档自身的哨兵问题（未找到标记、
      片段为空等）照常记进 ``report_code_match``。
    - ``report_code_match.checked`` 只有真正与源码比对过才是 True；配对失败、源码读不到、
      该阶段异常都是 False。``count=0`` 时看它区分“全对”与“没比”。
    - 各检查阶段独立降级：某阶段异常只往 ``document_no_result`` 记一条带阶段名的消息，
      其余阶段的结果照常返回。
    - 文件名校验：上传原名需符合 ``8–12 位学号-姓名-专题X…docx``，只校验格式，
      不比较上传账号；历史记录没有保存原名（``file_name`` 为 NULL）时无法校验，
      ``count=0``。页码校验真跑（页脚 PAGE 域 + Times New Roman + 小五 9 磅 + 靠右），
      同一有效页脚最多三条消息，全文没有页脚记一条。
    """
    result: dict = {
        RuleTypeEnum.FORMAT_ERROR: {
            "count": 0,
            **{check: {"count": 0, "messages": []} for check in DocFormatCheck},
        },
        RuleTypeEnum.CHAPTER_MISSING: {"count": 0, "messages": []},
        RuleTypeEnum.REPORT_CODE_MATCH: {"count": 0, "messages": [], "checked": False},
        "contents": {},
        "document_no_result": {"count": 0, "messages": []},
    }
    full_text = ""
    contents = result["contents"]
    format_error = result[RuleTypeEnum.FORMAT_ERROR]
    no_result = result["document_no_result"]

    try:
        resolved = _resolve_paths(doc_id, code_id)
    except Exception as exc:
        no_result["count"] = 1
        no_result["messages"].append(f"读取文档/源码记录失败：{exc}")
        contents.update(_missing_contents())
        return _answer(result, "")

    try:
        document = _load_document(resolved.doc_path)
    except Exception as exc:
        no_result["count"] = 1
        no_result["messages"].append(f"加载 Word 文档失败：{exc}")
        contents.update(_missing_contents())
        return _answer(result, "")

    source: str | None = None
    try:
        source = _read_source(resolved.code_path)
    except Exception as exc:
        no_result["count"] += 1
        no_result["messages"].append(f"读取源码文件失败：{exc}")

    pairable = True
    if (
        resolved.doc_user_id != resolved.code_user_id
        or resolved.doc_topic_id != resolved.code_topic_id
    ):
        pairable = False
        no_result["count"] += 1
        no_result["messages"].append(
            "文档与源码不匹配：上传账号或专题不一致"
            f"（文档 topic_id={resolved.doc_topic_id}，"
            f"源码 topic_id={resolved.code_topic_id}）"
        )

    blocks: list[ChapterBlock] = []
    snippets: list[CodeSnippet] = []
    marker_indices: set[int] = set()
    code_issues: list[str] = []

    blocks_ok = True
    try:
        blocks = _locate_chapters(document)
    except Exception as exc:
        blocks_ok = False
        _record_failure(no_result, "章节定位", exc)

    try:
        snippets, code_issues, marker_indices = _extract_code_snippets(document, blocks)
    except Exception as exc:
        _record_failure(no_result, "代码标记提取", exc)

    try:
        contents.update(_extract_content(blocks, snippets, marker_indices))
    except Exception as exc:
        _record_failure(no_result, "内容抽取", exc)
        contents.update(_missing_contents())

    try:
        full_text = _extract_full_text(document)
    except Exception as exc:
        _record_failure(no_result, "全文抽取", exc)

    if blocks_ok:
        present = {block.title for block in blocks}
        missing = [required for required in REQUIRED_CHAPTERS if required not in present]
        result[RuleTypeEnum.CHAPTER_MISSING] = {
            "count": len(missing),
            "messages": [f"缺少章节：{required}" for required in missing],
        }

    try:
        line_spacing = _check_line_spacing(document, snippets, marker_indices)
        format_error[DocFormatCheck.LINE_SPACING] = {
            "count": len(line_spacing),
            "messages": line_spacing,
        }
    except Exception as exc:
        _record_failure(no_result, "行距校验", exc)

    try:
        page_number = _check_page_number(document)
        format_error[DocFormatCheck.PAGE_NUMBER_FORMAT] = {
            "count": len(page_number),
            "messages": page_number,
        }
    except Exception as exc:
        _record_failure(no_result, "页码校验", exc)

    try:
        filename = _check_filename(resolved.doc_file_name)
        format_error[DocFormatCheck.FILE_NAMING] = {
            "count": len(filename),
            "messages": filename,
        }
    except Exception as exc:
        _record_failure(no_result, "文件名校验", exc)

    try:
        code_messages = list(code_issues)
        if pairable and source is not None:
            code_messages.extend(_check_code_consistency(snippets, source))
        result[RuleTypeEnum.REPORT_CODE_MATCH] = {
            "count": len(code_messages),
            "messages": code_messages,
            "checked": pairable and source is not None,
        }
    except Exception as exc:
        _record_failure(no_result, "代码一致性校验", exc)

    format_error["count"] = sum(
        format_error[check]["count"] for check in DocFormatCheck
    )
    return _answer(result, full_text)


__all__ = ["doc_parsing"]
