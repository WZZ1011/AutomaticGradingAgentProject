import json
import re

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage

from automaticgradingagentproject.core.enums.ast_rule_enum import AstRuleEnum
from automaticgradingagentproject.core.enums.format_error_enum import DocFormatCheck
from automaticgradingagentproject.core.enums.meaning_grade_enum import MeaningGradeEnum
from automaticgradingagentproject.core.enums.rule_type_enum import RuleTypeEnum
from automaticgradingagentproject.core.enums.sandbox_status_enum import (
    SandboxExecutionStatus,
)

load_dotenv()
llm=init_chat_model("deepseek:deepseek-chat")


async def llm_comment_(material_doc:dict,material_ast:dict,material_sandbox:dict,material_meaning:dict,score:int):
    messages=[
    HumanMessage(content=f"文本材料如下:\n"
                         f"{json.dumps(material_doc,ensure_ascii=False,default=str,indent=2)}\n"
                         f"{json.dumps(material_ast,ensure_ascii=False,default=str,indent=2)}\n"
                         f"{json.dumps(material_sandbox,ensure_ascii=False,default=str,indent=2)}\n"
                         f"{json.dumps(material_meaning,ensure_ascii=False,default=str,indent=2)}\n"
                         f"以上是一个学生的数据结构报告和他各模块的错误反馈，他的得分是{score},请你作为老师来写一段评价\n"
                         f"总要求：评价遵循简洁明了的风格，先说明得分与总体情况，然后就各模块展开分析与建议，最后鼓励并总结\n"
                         f"不要用MarkDown，就用纯文本，用序号和间隔等方式来做分隔"
                         f"其他要求：\n"
                         f"1，要求清晰列出所有扣分点(包括类型和数量)，并提出对应改进意见")
]
    return (await llm.ainvoke(messages)).content #await的优先级低于 .


def _meaning_report(message: str) -> dict:
    """语义分析不可用时返回的错误报告，score_ 会跳过该模块扣分。"""
    return {RuleTypeEnum.MEANING_ANALYSIS.value: {"count": 0, "error_message": message}}


_GRADE_BY_LETTER: dict[str, MeaningGradeEnum] = {
    "A": MeaningGradeEnum.A,
    "B": MeaningGradeEnum.B,
    "C": MeaningGradeEnum.C,
}


def parse_meaning_grade(text: str | None) -> MeaningGradeEnum | None:
    """从模型输出中提取 A/B/C；兼容 "a"、"A档"、"B+"、"答案：C" 等写法。"""
    if not isinstance(text, str):
        return None
    normalized = text.strip().upper()
    if not normalized:
        return None
    if normalized in _GRADE_BY_LETTER:
        return _GRADE_BY_LETTER[normalized]
    match = re.search(r"(?<![A-Z])([ABC])(?![A-Z])", normalized)
    if match is None:
        return None
    return _GRADE_BY_LETTER[match.group(1)]


async def llm_meaning_analysis(material_doc: dict, material_ast: dict,
                               material_sandbox: dict) -> dict:
    """判断报告语义档位，返回评分模块可直接消费的字典。

    - A：逻辑自洽且与其他模块反馈符合（count=0）
    - B：逻辑自洽但与其他模块反馈不符（count=1）
    - C：章节之间存在逻辑不自洽（count=2）
    """
    contents = material_doc.get("contents") if isinstance(material_doc, dict) else None
    if not contents:
        return _meaning_report("缺少文档章节内容，无法进行语义分析")

    prompt = (
        "请判断下面这份学生数据结构报告的逻辑自洽性，只需要输出 A、B、C 中的一个字母。\n\n"
        "判定标准：\n"
        "A：报告各章节逻辑自洽，且与代码/测试反馈的结论一致；\n"
        "B：报告自身逻辑自洽，但与代码静态检查或运行测试的反馈存在事实上的矛盾"
        "（例如报告声称已实现某功能，但检查结果显示未实现）；\n"
        "C：报告不同章节之间存在逻辑上的不自洽"
        "（例如功能设计、功能代码、调试与测试三部分互相矛盾）。\n\n"
        f"报告章节内容：\n{json.dumps(contents, ensure_ascii=False, default=str, indent=2)}\n\n"
        f"文档结构反馈：\n{json.dumps(material_doc, ensure_ascii=False, default=str, indent=2)}\n\n"
        f"代码静态检查反馈：\n{json.dumps(material_ast, ensure_ascii=False, default=str, indent=2)}\n\n"
        f"运行测试反馈：\n{json.dumps(material_sandbox, ensure_ascii=False, default=str, indent=2)}\n\n"
        "只输出一个字母，不要输出任何解释。"
    )

    try:
        answer = await llm.ainvoke([HumanMessage(content=prompt)])
        grade = parse_meaning_grade(getattr(answer, "content", None))
    except Exception as exc:  # 语义分析失败不应让整条批阅失败
        return _meaning_report(f"语义分析调用失败：{exc}")

    if grade is None:
        return _meaning_report("语义分析结果无法识别，未返回 A/B/C")

    return {
        RuleTypeEnum.MEANING_ANALYSIS.value: {
            "count": grade.severity,
            grade.value: {"count": 1},
        }
    }





#简单的测试数据
material_doc={RuleTypeEnum.FORMAT_ERROR:{
    "count":3,
    DocFormatCheck.FILE_NAMING:{
        "count":1
    },
    DocFormatCheck.LINE_SPACING:{
        "count":1
    },
    DocFormatCheck.PAGE_NUMBER_FORMAT:{
        "count":1
    }
},
RuleTypeEnum.CHAPTER_MISSING:{
    "count":1
},
RuleTypeEnum.REPORT_CODE_MATCH:{
    "count":1
},
"contents":{
    "总结":"这是总结"
}}
material_ast={RuleTypeEnum.AST_ERROR:{
    "count":3,
    AstRuleEnum.TOPIC1_NO_STD_SORT:{
        "count":1
    },
    AstRuleEnum.TOPIC1_REQUIRE_SELF_HEAP_SORT:{
        "count":1
    },
    AstRuleEnum.TOPIC1_REQUIRE_SELF_LINEAR_LIST:{
        "count":1
    }
}
}
material_sandbox={RuleTypeEnum.RUNNING_NO_RESULT:{
     SandboxExecutionStatus.TIMEOUT:{
         "count":1
     }
}
}

score=70
