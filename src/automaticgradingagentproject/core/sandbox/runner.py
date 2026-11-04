"""编排层：编译一次 → 逐用例跑 → 交给 compare / report 汇总。

本文件不认识 Docker，也不知道解析器的存在，只跟 SandboxBackend 协议打交道。
"""

from .backend import SandboxBackend, SandboxInfrastructureError
from .report import build_report
from .types import CaseResult, CompileResult


def compile_source(backend: SandboxBackend, source: str) -> CompileResult:
    """编译一次，原样返回 backend.compile 的结果。

    这里**不做**短路判断、也**不吞** SandboxInfrastructureError：
    - 编译失败（ok=False）后是否继续跑用例，由 run_all 依据 compile_result 决定；
    - 基础设施错误由 sandbox_run_validation 捕获降级。
    """
    return backend.compile(source)


def run_all(
    backend: SandboxBackend,
    test_cases: list[str],
    expected_outcome: list[str],
    compile_result: CompileResult | None,
) -> tuple[list[CaseResult], list[str]]:
    """逐用例跑，返回 (每个用例的结果, 降级错误信息列表)。

    约束：
    - compile_result is None 或 ok=False → 不跑任何用例，直接返回 ([], [])。
      编译失败的短路在**这里**做，不在 compile_source。
    - 保证"返回的 cases 与 test_cases 等长"：某个用例中途挂掉要补一条占位结果，
      否则下游按 index 对不上号。
    - 每个用例**单独计时**，不要把总耗时平均下去。
    - expected_outcome 不参与执行，只在 build_report 里参与比对；
      它和 test_cases 长度不一致的报错**由 build_report 负责**，这里不要重复记。
    - backend.execute 抛 SandboxInfrastructureError 时不得冒泡：记一条错误，
      给尚未执行的用例补占位结果；同一个基础设施错误不要按用例重复记 N 次。
    - 这个签名是内部的，可以按你的实现调整；真正对外的是 sandbox_run_validation。
    """
    # 编译失败短路：退出码非零，跑用例只会得到一堆无意义的失败
    if compile_result is None or not compile_result.ok:
        return [], []

    cases: list[CaseResult] = []
    errors: list[str] = []
    infra_down = False

    for stdin_text in test_cases:
        if infra_down:
            # 沙箱已经挂了就不再逐条重试，否则每个用例都要白等一次超时
            cases.append(CaseResult(stdout="", elapsed=0.0, timed_out=False, exit_code=-1))
            continue
        try:
            cases.append(backend.execute(stdin_text))
        except SandboxInfrastructureError as exc:
            # 同一个基础设施故障只记一条，不按用例重复记 N 次
            errors.append(f"沙箱执行阶段不可用：{exc}")
            infra_down = True
            # 补占位，保证 cases 与 test_cases 等长，下游按 index 才对得上
            cases.append(CaseResult(stdout="", elapsed=0.0, timed_out=False, exit_code=-1))

    return cases, errors


def sandbox_run_validation(
    source: str,
    test_cases: list[str],
    expected_outcome: list[str] | None,
    backend: SandboxBackend,
) -> dict:
    """引擎的对外入口。返回结构见 report.build_report 的 docstring。

    ⚠️ backend **故意不给默认值**：引擎层不该知道 Docker 存在，
    等 DockerBackend 落地后由 services 层决定接哪个实现。
    正因如此，现在就能用 FakeBackend 在没有 Docker 的机器上把链路跑通。

    契约：
    - expected_outcome 允许为 None：入口负责归一成 []，下游 build_report 只收 list。
    - 捕获 SandboxInfrastructureError → 记进 error_message，不冒泡。
      其余非预期异常**不吞**：它们属于编程错误，由 services 门面统一兜底
      （对外"任何异常降级进 error_message、不抛异常"的责任在门面层）。
    - 无论成功、编译失败还是基础设施错误，**finally 里必须调 backend.close()**。
      一个 backend 实例只服务一次提交，services 传进来后不得复用。
    - 正常路径与预期失败路径都返回完整 dict；非预期异常按上一条冒泡到门面。
    """
    if expected_outcome is None:
        expected_outcome = []

    errors: list[str] = []
    try:
        try:
            compile_result = compile_source(backend, source)
        except SandboxInfrastructureError as exc:
            # 编译阶段就没起来：留 None，让 build_report 报成 error_message（基础设施问题），
            # 而不是 compilation_error_message（那是学生代码的错）
            compile_result = None
            errors.append(f"沙箱编译阶段不可用：{exc}")

        cases, run_errors = run_all(
            backend, test_cases, expected_outcome, compile_result
        )
        errors.extend(run_errors)

        return build_report(cases, expected_outcome, compile_result, errors)
    finally:
        # 一个 backend 实例只服务一次提交；close 契约要求幂等且不抛
        backend.close()
