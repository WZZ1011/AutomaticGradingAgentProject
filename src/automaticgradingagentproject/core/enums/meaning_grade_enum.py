from enum import Enum


class MeaningGradeEnum(str, Enum):
    """文档语义校验的三档结果。

    A：逻辑自洽，且与其他校验模块的反馈符合；
    B：逻辑自洽，但与其他模块反馈存在事实上的不符合；
    C：章节之间存在逻辑不自洽。
    """

    A = "meaning_a"
    B = "meaning_b"
    C = "meaning_c"

    @property
    def severity(self) -> int:
        """扣分严重度：A=0，B=1，C=2。"""
        return {"meaning_a": 0, "meaning_b": 1, "meaning_c": 2}[self.value]
