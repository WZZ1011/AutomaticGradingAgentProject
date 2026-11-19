"""把 LLM 的章节复核结果安全地写回 ``word_parsing_return``。

这里只做纯数据变换，不调用 LLM、不访问数据库，方便单独测试。
"""

from __future__ import annotations

from automaticgradingagentproject.core.chapter_config import (
    canonical_chapter,
    extract_section_text,
    find_existing_contents_key,
)
from automaticgradingagentproject.core.enums.rule_type_enum import RuleTypeEnum

_MISSING_CONTENTS = object()


def _find_chapter_missing(
    word_parsing_return: dict,
) -> tuple[object, dict] | None:
    """兼容枚举 key 和字符串 key，找到 chapter_missing 的 payload。"""
    for key in (
        RuleTypeEnum.CHAPTER_MISSING,
        RuleTypeEnum.CHAPTER_MISSING.value,
    ):
        value = word_parsing_return.get(key)
        if isinstance(value, dict):
            return key, value
    return None


def _valid_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _normalize_candidates(raw: object) -> list[str]:
    """把解析器给出的缺失名单规范化为去重后的规范章节名。"""
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            continue
        name = canonical_chapter(item) or item.strip()
        if name not in result:
            result.append(name)
    return result


def _normalize_for_match(text: str) -> str:
    return "".join(str(text).split())


def _evidence_in_text(evidence: object, full_text: object) -> bool:
    """证据必须是原文的连续片段，作为防幻觉/提示词注入的硬校验。"""
    if not isinstance(evidence, str) or not evidence.strip():
        return False
    if not isinstance(full_text, str) or not full_text:
        return False
    return _normalize_for_match(evidence) in _normalize_for_match(full_text)


def _filter_messages(
    messages: object, candidates: list[str], remaining: list[str]
) -> object:
    """名单与 messages 一一对应时同步裁剪；否则保持原样，不虚构消息。"""
    if isinstance(messages, list) and len(messages) == len(candidates):
        keep = set(remaining)
        return [msg for msg, name in zip(messages, candidates) if name in keep]
    return messages


def _backfill_contents(
    updated: dict,
    recovered: list[str],
    full_text: str,
) -> list[str]:
    """把复核确认存在的章节从全文切回 ``contents``。

    返回实际回填的章节名。已有等价 key（含别名）时不覆盖；定位不到标题时
    跳过该章节，但仍保留 count 修正。
    """
    stored = updated.get("contents", _MISSING_CONTENTS)
    if stored is not _MISSING_CONTENTS and not isinstance(stored, dict):
        return []

    contents = dict(stored) if isinstance(stored, dict) else {}
    backfilled: list[str] = []
    for name in recovered:
        if find_existing_contents_key(contents, name) is not None:
            continue
        text = extract_section_text(full_text, name)
        if not text:
            continue
        contents[name] = text
        backfilled.append(name)

    if backfilled:
        updated["contents"] = contents
    return backfilled


def apply_chapter_reviews(
    word_parsing_return: dict,
    reviews: list[dict] | None,
    full_text: str,
    error_message: str | None = None,
) -> dict:
    """返回修正后的 ``word_parsing_return`` 新对象，不原地修改入参。

    修正规则：
    - 只把候选名单中 ``present is True`` 且证据能在全文中找到的章节判为存在；
    - 只允许减少缺失数量，``count`` 由代码重算，绝不采信模型给的数字；
    - 复核失败、名单缺失或没有可确认项时，保持原判；
    - 确认存在的章节尝试从 ``full_text`` 确定性切分并补回 ``contents``。
    """
    updated = dict(word_parsing_return) if isinstance(word_parsing_return, dict) else {}

    found = _find_chapter_missing(updated)
    if found is None:
        return updated

    key, payload = found
    chapter = dict(payload)

    original_count = chapter.get("count")
    candidates = _normalize_candidates(chapter.get("missing"))
    audit = {
        "recovered": [],
        "confirmed_missing": candidates,
        "error_message": error_message,
    }

    if (
        not candidates
        or not _valid_count(original_count)
        or original_count <= 0
        or not isinstance(reviews, list)
        or not reviews
    ):
        chapter["review"] = audit
        updated[key] = chapter
        return updated

    recovered: list[str] = []
    candidate_set = set(candidates)
    for item in reviews:
        if not isinstance(item, dict):
            continue
        raw_name = item.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            continue
        name = canonical_chapter(raw_name) or raw_name.strip()
        if name not in candidate_set:
            # 模型不得新增缺失项，也不得复核名单外的章节。
            continue
        if item.get("present") is not True:
            continue
        if not _evidence_in_text(item.get("evidence"), full_text):
            # 没有可核对的原文证据时不退分。
            continue
        if name not in recovered:
            recovered.append(name)

    if not recovered:
        chapter["review"] = audit
        updated[key] = chapter
        return updated

    recovered_set = set(recovered)
    remaining = [name for name in candidates if name not in recovered_set]

    # 解析器的 count 可能大于名单长度（剩余未知缺失继续算作缺失）；
    # count 小于名单长度时用 min 兜底，保证结果只减不增。
    unexplained = max(0, original_count - len(candidates))
    new_count = min(original_count, unexplained + len(remaining))

    chapter["count"] = new_count
    chapter["missing"] = remaining
    chapter["messages"] = _filter_messages(
        chapter.get("messages"), candidates, remaining
    )
    chapter["review"] = {
        "recovered": recovered,
        "confirmed_missing": remaining,
        "error_message": error_message,
    }
    updated[key] = chapter

    _backfill_contents(updated, recovered, full_text)
    return updated
