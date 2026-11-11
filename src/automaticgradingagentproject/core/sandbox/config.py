"""沙箱模块的可调参数，全部集中在这一个文件里。

规则：本文件只描述"参数是什么"，不实现任何沙箱行为。
"""

import math
import os
from dataclasses import dataclass

# 超时秒数上界：GNU timeout 的 timespec 在约 2^63 秒饱和，Windows 的
# WaitForSingleObject 毫秒参数只有 32 位；1e300 这类有限值会让宿主看门狗
# 立即超时或永不超时。取 24 小时，远低于两个平台的上限。
_MAX_TIMEOUT = 86400.0


@dataclass(frozen=True)
class SandboxConfig:
    """一次沙箱验证要用到的全部旋钮。

    字段名与环境变量名一一对应（大写加 SANDBOX_ 前缀），例如 image ← SANDBOX_IMAGE。
    """

    image: str = ""              # 必填。要求自带 bash、g++、GNU coreutils；
                                 # 必须先 docker pull（容器用 --pull=never）；
                                 # 空串时 from_env() 直接报错
    compile_timeout: float = 30.0
    run_timeout: float = 10.0    # 说明书硬性要求：单任务 10 秒
    memory_limit: str = "256m"   # 内存炸弹
    cpus: str = "0.5"            # 死循环占 CPU
    pids_limit: int = 64         # fork 炸弹
    output_limit: int = 65536    # stdout 读取字节上限，防 while(1) printf 吃满内存
    compile_output_limit: int = 65536  # 编译诊断字节上限，防模板错误刷爆宿主内存

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
        try:
            from dotenv import load_dotenv
        except ImportError:
            # 没装 python-dotenv 时仍然支持真正的环境变量
            pass
        else:
            load_dotenv()

        def _read_float(name: str, default: float) -> float:
            raw = os.getenv(name)
            if raw is None:
                return default
            try:
                value = float(raw)
            except ValueError as exc:
                raise ValueError(f"{name} 必须是数字，当前值：{raw!r}") from exc
            if not math.isfinite(value) or not (0 < value <= _MAX_TIMEOUT):
                raise ValueError(
                    f"{name} 必须是 (0, {_MAX_TIMEOUT:g}] 内的有限秒数，"
                    f"当前值：{raw!r}"
                )
            return value

        def _read_int(name: str, default: int) -> int:
            raw = os.getenv(name)
            if raw is None:
                return default
            try:
                value = int(raw)
            except ValueError as exc:
                raise ValueError(f"{name} 必须是整数，当前值：{raw!r}") from exc
            if value <= 0:
                raise ValueError(f"{name} 必须大于 0，当前值：{raw!r}")
            return value

        def _read_text(name: str, default: str) -> str:
            raw = os.getenv(name)
            if raw is None:
                return default
            value = raw.strip()
            if not value:
                raise ValueError(f"{name} 不能为空")
            return value

        image = os.getenv("SANDBOX_IMAGE", "").strip()
        if not image:
            raise ValueError(
                "SANDBOX_IMAGE 未配置（必须指定自带 bash/g++/GNU coreutils 的镜像）"
            )

        return cls(
            image=image,
            compile_timeout=_read_float("SANDBOX_COMPILE_TIMEOUT", 30.0),
            run_timeout=_read_float("SANDBOX_RUN_TIMEOUT", 10.0),
            memory_limit=_read_text("SANDBOX_MEMORY_LIMIT", "256m"),
            cpus=_read_text("SANDBOX_CPUS", "0.5"),
            pids_limit=_read_int("SANDBOX_PIDS_LIMIT", 64),
            output_limit=_read_int("SANDBOX_OUTPUT_LIMIT", 65536),
            compile_output_limit=_read_int("SANDBOX_COMPILE_OUTPUT_LIMIT", 65536),
        )
