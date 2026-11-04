"""沙箱模块的可调参数，全部集中在这一个文件里。

规则：本文件只描述"参数是什么"，不实现任何沙箱行为。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SandboxConfig:
    """一次沙箱验证要用到的全部旋钮。

    字段名与环境变量名一一对应（大写加 SANDBOX_ 前缀），例如 image ← SANDBOX_IMAGE。
    """

    image: str = ""              # TODO(zhong): 必填。要求自带 bash、g++、GNU coreutils；
                                 # 空串时 DockerBackend 一调就失败
    compile_timeout: float = 30.0
    run_timeout: float = 10.0    # 说明书硬性要求：单任务 10 秒
    memory_limit: str = "256m"   # 内存炸弹
    cpus: str = "0.5"            # 死循环占 CPU
    pids_limit: int = 64         # fork 炸弹
    output_limit: int = 65536    # stdout 读取字节上限，防 while(1) printf 吃满内存

    @classmethod
    def from_env(cls) -> "SandboxConfig":
        """从环境变量构造配置。

        约束：
        - 数字型环境变量（超时/内存/pids/上限）配错时**直接抛 ValueError**，
          不要静默退回默认值——安全相关的参数猜错，比启动时就报错更糟。
        - .env 已被 .gitignore 排除；要读它得用 python-dotenv。
          注意 pyproject.toml 里声明的包名写的是 `dotenv`（非官方名），
          实际安装的是 `python-dotenv`，导入写法是 `from dotenv import load_dotenv`。
        """
        raise NotImplementedError("TODO(zhong): 由你实现")
