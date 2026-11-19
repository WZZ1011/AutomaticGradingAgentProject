"""必选章节的单一事实来源，以及标题归一化/匹配/切分工具。

“规范章节名”是去掉编号后的名称（如 ``功能与设计``），供评分契约和复核节点
共同使用；``aliases`` 覆盖学生在报告里常见的不同写法。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class ChapterSpec:
    canonical: str
    aliases: tuple[str, ...]


# “附录”按任务书要求是必选章节。
REQUIRED_CHAPTERS: tuple[ChapterSpec, ...] = (
    ChapterSpec(
        "题目与要求",
        (
            "题目与要求",
            "题目要求",
            "题目及要求",
            "题目、要求",
            "任务与要求",
            "题目",
            "问题描述与要求",
            "实验题目与要求",
        ),
    ),
    ChapterSpec(
        "功能与设计",
        (
            "功能与设计",
            "功能设计",
            "功能分析",
            "功能及设计",
            "功能设计方案",
            "需求分析与设计",
            "功能需求与设计",
        ),
    ),
    ChapterSpec(
        "功能代码",
        (
            "功能代码",
            "功能实现",
            "功能代码实现",
            "核心代码",
            "代码实现",
            "主要代码",
        ),
    ),
    ChapterSpec(
        "调试与测试",
        (
            "调试与测试",
            "测试与调试",
            "调试及测试",
            "调试和测试",
            "测试",
            "调试",
            "调试分析",
            "测试分析",
        ),
    ),
    ChapterSpec(
        "总结",
        (
            "总结",
            "总结与展望",
            "总结与体会",
            "心得体会",
            "课程设计总结",
            "结论",
            "小结",
        ),
    ),
    ChapterSpec(
        "附录",
        (
            "附录",
            "附录A",
            "附录B",
            "附录一",
            "附录1",
            "附件",
            "源代码附录",
        ),
    ),
)

REQUIRED_CHAPTER_NAMES: tuple[str, ...] = tuple(
    spec.canonical for spec in REQUIRED_CHAPTERS
)

CHAPTER_ALIASES: dict[str, tuple[str, ...]] = {
    spec.canonical: spec.aliases for spec in REQUIRED_CHAPTERS
}


_CN_NUM = "零〇一二三四五六七八九十百千万两"

_NUMBER_PREFIXES = (
    # 第1章 / 第 3 节 / 第二部分
    re.compile(rf"^第\s*[{_CN_NUM}0-9]+\s*[章节部分篇]\s*"),
    # 一、 / 二. / 三．/ 四)
    re.compile(rf"^[{_CN_NUM}]+\s*[、.．)]\s*"),
    # (1) / （四） / [2]
    re.compile(rf"^[\(\[（]\s*[{_CN_NUM}0-9]+\s*[\)\]）]\s*"),
    # 1 / 1. / 1.2 / 1、 / 1)
    re.compile(r"^[0-9]+(?:\s*[.．]\s*[0-9]+)*\s*[、.．)]?\s*"),
)

_MARKDOWN_PREFIX = re.compile(r"^#{1,6}\s*")
_MARKDOWN_SUFFIX = re.compile(r"\s*#+\s*$")
_TRAILING_PAREN = re.compile(r"\([^()]{0,20}\)\s*$")
_TOC_DOTS = re.compile(r"(?:\.{3,}|…{2,}|·{3,})\s*\d*\s*$")
_SENTENCE_END = ("。", "！", "？", "；", "，", ",", ";", "!", "?")

_EDGE_PUNCT = "：:、.．,，;；-—–()（）[]【】《》<>\"'“”‘’"


def normalize_chapter_title(raw: str) -> str:
    """把标题归一化为可比较的形式：全角转半角、去编号、去空白和边缘标点。"""
    if not isinstance(raw, str):
        return ""
    text = unicodedata.normalize("NFKC", raw).strip()
    text = _MARKDOWN_PREFIX.sub("", text)
    text = _MARKDOWN_SUFFIX.sub("", text)
    # 编号可能叠加（例如“第1章 1 题目与要求”），最多剥离三次。
    for _ in range(3):
        before = text
        for pattern in _NUMBER_PREFIXES:
            text = pattern.sub("", text, count=1)
        if text == before:
            break
    text = _TRAILING_PAREN.sub("", text)
    text = re.sub(r"\s+", "", text)
    text = text.strip(_EDGE_PUNCT)
    return text.lower()


# 导入时把别名展开成“归一化别名 -> 规范名”，避免运行时重复计算。
ALIAS_TO_CANONICAL: dict[str, str] = {}
for _spec in REQUIRED_CHAPTERS:
    for _alias in _spec.aliases:
        ALIAS_TO_CANONICAL[normalize_chapter_title(_alias)] = _spec.canonical


def canonical_chapter(raw: str) -> str | None:
    """返回标题对应的规范章节名；不是已知章节时返回 ``None``。"""
    normalized = normalize_chapter_title(raw)
    if not normalized:
        return None
    return ALIAS_TO_CANONICAL.get(normalized)


def is_chapter_heading(line: str) -> str | None:
    """判断一行是否是已知章节标题，返回规范名。

    目录行（点线引导 + 页码）、正文句子和过长行都会被排除。
    """
    if not isinstance(line, str):
        return None
    stripped = line.strip()
    if not stripped or len(stripped) > 40:
        return None
    if _TOC_DOTS.search(stripped):
        return None
    if stripped.endswith(_SENTENCE_END):
        return None
    return canonical_chapter(stripped)


def split_chapters(full_text: str) -> dict[str, str]:
    """按行切分全文，返回 ``{规范章节名: 章节文本}``。

    同名标题出现多次时（如目录 + 正文），保留文本最长的一次；找不到标题的
    章节不会出现在返回值里。
    """
    if not isinstance(full_text, str) or not full_text.strip():
        return {}

    lines = full_text.splitlines(keepends=True)
    headings: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        name = is_chapter_heading(line)
        if name is not None:
            headings.append((index, name))

    chapters: dict[str, str] = {}
    for position, (start, name) in enumerate(headings):
        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        text = "".join(lines[start:end]).strip()
        if not text:
            continue
        if name not in chapters or len(text) > len(chapters[name]):
            chapters[name] = text
    return chapters


def extract_section_text(full_text: str, canonical: str) -> str | None:
    """从全文中切出指定规范章节的文本；定位失败返回 ``None``。"""
    if not isinstance(canonical, str):
        return None
    return split_chapters(full_text).get(canonical)


def find_existing_contents_key(contents: dict, canonical: str) -> str | None:
    """在 ``contents`` 中找到等价章节 key（兼容别名写法）。"""
    if not isinstance(contents, dict):
        return None
    for key in contents:
        if not isinstance(key, str):
            continue
        if canonical_chapter(key) == canonical:
            return key
    return None
