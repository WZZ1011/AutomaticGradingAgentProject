"""输出规范化 + 逐用例差分比对。

纯函数，零依赖，最容易先写完写完测透的一个文件。
"""


def normalize_output(text: str) -> str:
    """把输出规范化到可比较的形式。

    契约：只做**肉眼不可见**的等价变换。
      允许 —— 统一换行符（\\r\\n 与 \\n 视为相同）、去每行行尾空白、去末尾空行。
      禁止 —— 去行首空白、压缩行内空格、删中间空行。

    为什么这条线不能越：不规范化会造成"答对的判错"（学生交的末行少个换行就挂），
    规范过头会造成"答错的判对"（"1 2 3" 和 "1  2   3" 被当成一样）——两者都属于放水。
    """
    text = text.replace("\r\n", "\n")     # 第 1 步：CRLF → LF
    lines = text.split("\n")              # 第 2 步：字符串 → 行列表
    lines = [line.rstrip() for line in lines]  # 去每行行尾空白
    while lines and lines[-1] == "":         # 去末尾空行
        lines.pop()
    return "\n".join(lines)


def diff_one(expected: str, real: str) -> dict:
    """比对单个用例，返回：

        {"expected_outcome": str, "real_outcome": str, "true": bool}

    ⚠️ 两个 outcome 字段里放的是**规范化后**的文本，不是入参原文。
    依据是说明书给的契约示例：入参带尾换行，返回里没有尾换行。
    """
    expected_norm = normalize_output(expected)
    real_norm = normalize_output(real)
    return {
        "expected_outcome": expected_norm,
        "real_outcome": real_norm,
        "true": expected_norm == real_norm,
    }
