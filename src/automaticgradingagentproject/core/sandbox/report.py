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
            "running_no_result": {
                "count": 0,                         # int，恒为第一个 key
                "compilation_error_message": None,  # list[str] 或 None，只要 GCC 原文
                "error_message": [],                # list[str]，基础设施/契约异常
            },
            "running_result_error": {
                "count": 0,                         # int，恒为第一个 key
                "pass_rate": "50%",                 # str
                "average_run_time": "0.12s",        # str
                "differential_comparison": [        # list of dict，按较短者对齐的账本
                    {"expected_outcome": ..., "real_outcome": ..., "true": ...},
                ],
            },
        }

    两个节点**恒在**，即使 count 为 0 也不删——下游按固定 key 取
    pass_rate / differential_comparison，节点消失就是 KeyError。

    count 的口径（单位是"事件"，不是"用例"）：
    - running_no_result.count：编译失败或编译阶段不可用（compile_result 为 None）
      记 1 次；每个未执行（executed=False）、timed_out 或 exit_code != 0 的用例
      再记 1 次。
    - running_result_error.count：只数"拿到了可用结果、但比对不上"的用例
      （not timed_out and exit_code == 0 and 比对 false）。超时/崩溃的用例
      归 no_result，不在这里重复计数。
    所以 running_result_error.count 可能**小于** differential_comparison 里
    true=False 的条数——后者是历史遗留的账本，逐条保留，不按桶裁剪。

    未被执行的占位结果（runner 在基础设施故障后补的 CaseResult(executed=False)）
    保留在 differential_comparison 中并强制 true=False，以维持 index 对齐；
    它们计入 running_no_result.count，但不进 pass_rate / average_run_time 的分母。

    约束：
    - 本函数**不主动 raise**，但只接受已归一过的入参（expected_outcome 必须是 list）：
      None → [] 的归一由 sandbox_run_validation 负责，不要在这里再做一次防御。
      对外"不抛异常、异常全部降级进 error_message"的责任在 services 门面层
      （上游 marking_service.get_code 会直接 raise ValueError，门面必须接住）。
    - 三类失败不要混进同一个篮子：
        ① 编译失败                    → running_no_result.compilation_error_message
        ② 某用例超时 / 运行崩溃        → 计入 running_no_result.count，
                                        但**不**进 error_message
        ③ 基础设施错误（Docker 没起、
           镜像拉不到）                → running_no_result.error_message
      第③类若混进"编译错误"，学生会以为是自己代码的问题，排障方向完全错。
      error_message 非空表示存在需要人工确认的基础设施/契约异常；
      其中基础设施错误意味着这份报告**不可评分**，它不是学生错误。
    - pass_rate / average_run_time 仍按**整份提交**统计，不是节点内局部指标；
      两者的分母都只算**真正执行过**且参与比对的用例（executed=True），
      未执行的占位结果不计入，也不是全部 test_cases。
    - compile_result is None 是可能出现的（沙箱在编译阶段就挂了），
      别写成 `if not compile_result.ok`，那会 AttributeError。

    已知边界（有意保留）：超时用例的 stdout 若恰好等于期望输出，旧口径的
    pass_rate 会把它算作通过，但 count 仍归入 running_no_result。要消除这个
    不一致就得改 pass_rate 的旧值，所以这里只记录、不修正。

    对空 cases 的处理原则是：照常产出完整 dict，不做特殊报错。
    报"用例数量不一致"会把真实原因埋掉——只在 cases 非空时才做数量校验。

    长度不一致的**唯一**记录点就是这里（zip 按短的截断 + append 一条错误）：
    runner.run_all 不再重复记这条，避免同一条错误出现两次。
    cases 比 expected_outcome 多出的用例不参与比对（也无法比对），
    只在 no_result_count 中按退出状态计数。
    """
    pairs = list(zip(expected_outcome, cases))

    comparisons: list[dict] = []
    passed = 0
    gradable_total = 0
    gradable_elapsed = 0.0
    for expected, case in pairs:
        item = diff_one(expected, case.stdout)
        if case.executed:
            gradable_total += 1
            gradable_elapsed += case.elapsed
            if item["true"]:
                passed += 1
        else:
            # 未执行的占位结果没有真实 stdout：保留条目维持 index 对齐，但强制判错；
            # 它也不进 pass_rate / average_run_time 的分母，避免“没跑过却 100%”。
            item["true"] = False
        comparisons.append(item)

    pass_rate = pct(passed, gradable_total)
    average = gradable_elapsed / gradable_total if gradable_total else 0.0
    average_run_time = fmt_seconds(average)

    if compile_result is None or compile_result.ok:
        compilation_error_message = None
    else:
        # 只过滤纯空白行：g++ 的诊断原文（含缩进的代码片段）必须保留。
        lines = [line for line in compile_result.stderr.splitlines() if line.strip()]
        compilation_error_message = lines or [
            "编译失败，但编译器没有输出错误信息"
        ]

    error_message = list(errors)
    if cases and len(cases) != len(expected_outcome):
        error_message.append("用例数量与期望输出数量不一致")

    # 只统计真正参与比对的用例（cases 可能比 expected_outcome 长）；
    # 未执行的占位结果没有真实 stdout，也不算截断。
    truncated_count = sum(
        1 for case in cases[: len(comparisons)] if case.executed and case.truncated
    )
    if truncated_count:
        error_message.append(
            f"{truncated_count} 个用例的 stdout 超过输出上限被截断，比较结果可能不可靠"
        )

    no_result_count = 0
    # compile_result 为 None 表示编译阶段基础设施不可用，同样拿不到任何结果。
    if compile_result is None or not compile_result.ok:
        no_result_count += 1

    result_error_count = 0
    for index, case in enumerate(cases):
        if not case.executed or case.timed_out or case.exit_code != 0:
            no_result_count += 1
        elif index < len(comparisons) and not comparisons[index]["true"]:
            result_error_count += 1

    return {
        "running_no_result": {
            "count": no_result_count,
            "compilation_error_message": compilation_error_message,
            "error_message": error_message,
        },
        "running_result_error": {
            "count": result_error_count,
            "pass_rate": pass_rate,
            "average_run_time": average_run_time,
            "differential_comparison": comparisons,
        },
    }
