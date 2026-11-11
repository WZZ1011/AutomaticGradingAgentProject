"""沙箱验证引擎（纯引擎，不碰 DB、不碰业务）。

与仓库其余空 __init__.py 的惯例不同，这里做导出，为的是 services 层一行导入：

    from automaticgradingagentproject.core.sandbox import sandbox_run_validation

引擎的入参是**源码字符串**而不是 code_id：
code_id → 源码 的解析属于门面层（services）的事，不放在这里。
好处是引擎可以脱离 DB、脱离 Docker 单独测试。
"""

from .backend import (
    DockerBackend,
    FakeBackend,
    SandboxBackend,
    SandboxInfrastructureError,
)
from .config import SandboxConfig
from .runner import sandbox_run_validation
from .types import CaseResult, CompileResult

__all__ = [
    "sandbox_run_validation",
    "SandboxConfig",
    "SandboxBackend",
    "SandboxInfrastructureError",
    "DockerBackend",
    "FakeBackend",
    "CaseResult",
    "CompileResult",
]
