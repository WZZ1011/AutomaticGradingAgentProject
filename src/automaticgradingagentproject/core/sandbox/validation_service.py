"""沙箱验证的服务门面。

职责只有两件：
1. code_id → 源码字符串（DAO + 文件读取）；
2. 把沙箱引擎的所有异常降级成一份 error_message 报告。

引擎（core.sandbox）的入参是源码字符串，不认识 code_id、不认识 DB；
code_id 的解析与异常兜底都放在这里。

⚠️ 对外函数名 `sandbox_runing_validation` 按说明书保留（running 少一个 n），
不要"顺手纠正"成 running——调用方按这个名字导入。
"""

from pathlib import Path

from automaticgradingagentproject.core.database import SessionLocal
from automaticgradingagentproject.core.sandbox import (
    DockerBackend,
    SandboxBackend,
    SandboxConfig,
    sandbox_run_validation,
)
from automaticgradingagentproject.core.sandbox.report import build_report
from automaticgradingagentproject.services import marking_service


def _read_source(path_text: str) -> str:
    """读源码文件：utf-8 → gbk → utf-8(errors=replace)。

    `Code.source_code` 存的是磁盘绝对路径，不是源码正文。
    """
    raw = Path(path_text).read_bytes()
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _error_report(message: str) -> dict:
    """把门面层的失败降级成一份不可评分的报告。"""
    return build_report([], [], None, [message])


def _make_backend(config: SandboxConfig) -> SandboxBackend:
    """单独抽出来，测试时可以 monkeypatch 成 FakeBackend。"""
    return DockerBackend(config)


def sandbox_runing_validation(
    code_id: int,
    test_cases: list[str],
    expected_outcome: list[str] | None,
) -> dict:
    """按 code_id 取源码并跑沙箱验证。

    数据库会话由函数内部创建并关闭，调用方不需要传入。

    Args:
        code_id: 源码记录 id。
        test_cases: 每个用例的标准输入（原样作为程序 stdin）。
        expected_outcome: 与 test_cases 对齐的期望输出；允许为 None。

    Returns:
        report.build_report 的嵌套 dict。**任何异常都不外抛**：
        源码/配置/基础设施问题都会变成 error_message，报告仍可按固定 key 读取。
    """
    try:
        session = SessionLocal()
    except Exception as exc:  # 连会话都建不出来时也只能降级
        return _error_report(f"无法创建数据库会话：{exc}")

    try:
        try:
            code = marking_service.get_code(session, code_id)
        except ValueError as exc:
            return _error_report(f"无法读取源码：{exc}")
        except Exception as exc:
            return _error_report(f"查询源码失败：{exc}")

        source_path = code.source_code
        try:
            source = _read_source(source_path)
        except FileNotFoundError:
            return _error_report(f"源码文件不存在：{source_path}")
        except OSError as exc:
            return _error_report(f"读取源码失败：{exc}")
        except Exception as exc:
            return _error_report(f"读取源码失败：{exc}")

        try:
            config = SandboxConfig.from_env()
        except Exception as exc:
            return _error_report(f"沙箱配置不可用：{exc}")

        try:
            backend = _make_backend(config)
        except Exception as exc:
            return _error_report(f"沙箱后端初始化失败：{exc}")

        try:
            return sandbox_run_validation(source, test_cases, expected_outcome, backend)
        except Exception as exc:
            # 引擎只应抛出编程错误；门面对外仍然承诺不抛
            return _error_report(f"沙箱验证失败：{exc}")
    finally:
        session.close()
