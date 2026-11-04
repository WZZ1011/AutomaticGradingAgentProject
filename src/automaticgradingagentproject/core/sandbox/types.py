"""沙箱引擎的内部数据类型。只放数据，不放逻辑。"""

from dataclasses import dataclass


@dataclass
class CompileResult:
    """一次编译的结果。

    ⚠️ 成败判据是**进程退出码**，不是 "stderr 有没有内容"。
    g++ 的警告也写 stderr，按 stderr 判会把所有带警告的代码误判成编译失败。
    """

    ok: bool
    stderr: str = ""
    # 不放产物路径：共享卷方案下它固定是 /box/out（见 backend.DockerBackend）。
    # 编译耗时下游不消费，要不要留作排障字段由你决定。


@dataclass
class CaseResult:
    """单个测试用例的运行结果。

    注意 elapsed 只计**运行阶段**的墙钟时间，不含编译、不含容器冷启动。
    口径先定死，别让"平均 0.12s"里混进 0.3s 的启动耗时。
    """

    stdout: str        # 已按 output_limit 截断、已解码成 str
    elapsed: float     # 该用例运行耗时（秒）
    timed_out: bool    # 是否被超时掐断
    exit_code: int
