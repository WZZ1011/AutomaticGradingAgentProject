"""把沙箱跑出来的结果拼成对外返回的 dict。纯函数。"""

from .types import CaseResult, CompileResult
from .compare import diff_one

def pct(passed: int, total: int) -> str:
    """(通过数, 总数) → "50%" 形式的字符串。

    契约：返回值必须是 str。"50%" 是字符串，不是数字 50.0——
    返回数字下游必炸。
    边界：total == 0（说明书的 test_cases 为空）→ 返回 "0%"。

    不做四舍五入，直接截断到整数位。因为 pass_rate 只是给学生看的，
    防止出现199/200被直接判定为100%，而实际上是没完全通过的。

    已确认口径（2026-10-20）：向下截断。代价是 1/200 显示 "0%"，
    这是有意接受的；若要改成"四舍五入后夹在 1~99"，需要重新裁定。
    """
    if total == 0:
        return "0%"
    value = passed * 100 // total
    return f"{value}%"


def fmt_seconds(seconds: float) -> str:
    """转成 "0.12s" 这种格式的字符串。同样是 str 不是 float。"""
    return f"{seconds:.2f}s"


def build_report(
    cases: list[CaseResult],
    expected_outcome: list[str],
    compile_result: CompileResult | None,
    errors: list[str],
) -> dict:
    """拼出最终返回结构。形状是契约，少一个 key 下游就 KeyError：

        {
            "pass_rate": "50%",                    # str
            "average_run_time": "0.12s",           # str
            "compilation_error_message": None,     # list[str] 或 None，只要 GCC 原文
            "differential_comparison": [           # list of dict，不是 dict
                {"expected_outcome": ..., "real_outcome": ..., "true": ...},
            ],
            "error_message": [],                   # list[str]，其他异常降级到此
        }

    约束：
    - 本函数**不主动 raise**，但只接受已归一过的入参（expected_outcome 必须是 list）：
      None → [] 的归一由 sandbox_run_validation 负责，不要在这里再做一次防御。
      对外"不抛异常、异常全部降级进 error_message"的责任在 services 门面层
      （上游 marking_service.get_code 会直接 raise ValueError，门面必须接住）。
    - 三类失败不要混进同一个篮子：
        ① 编译失败                    → compilation_error_message
        ② 某用例超时 / 运行崩溃        → 该用例判错，**不**进 error_message
        ③ 基础设施错误（Docker 没起、
           镜像拉不到）                → error_message
      第③类若混进"编译错误"，学生会以为是自己代码的问题，排障方向完全错。
    - average_run_time 的分母 = **参与比对的用例条数**（与 pass_rate 同一批数据），
      不是全部 test_cases。
    - compile_result is None 是可能出现的（沙箱在编译阶段就挂了），
      别写成 `if not compile_result.ok`，那会 AttributeError。

    对空 cases 的处理原则是：照常产出完整 dict，不做特殊报错。
    报"用例数量不一致"会把真实原因埋掉——只在 cases 非空时才做数量校验。

    长度不一致的**唯一**记录点就是这里（zip 按短的截断 + append 一条错误）：
    runner.run_all 不再重复记这条，避免同一条错误出现两次。
    """
    pairs = list(zip(expected_outcome, cases))
    comparisons = [diff_one(expected, case.stdout) for expected, case in pairs]

    passed = sum(1 for item in comparisons if item["true"])
    pass_rate = pct(passed, len(comparisons))

    if pairs:
        average = sum(case.elapsed for _, case in pairs) / len(pairs)
    else:
        average = 0.0
    average_run_time = fmt_seconds(average)

    if compile_result is None or compile_result.ok:
        compilation_error_message = None
    else:
        compilation_error_message = compile_result.stderr.splitlines() or [
            "编译失败，但编译器没有输出错误信息"
        ]

    error_message = list(errors)
    if cases and len(cases) != len(expected_outcome):
        error_message.append("用例数量与期望输出数量不一致")

    return {
        "pass_rate": pass_rate,
        "average_run_time": average_run_time,
        "compilation_error_message": compilation_error_message,
        "differential_comparison": comparisons,
        "error_message": error_message,
    }