"""★ 全项目唯一允许接触 docker 的文件。

划这条边界的意义：Docker 装不装、用哪个镜像这类未决问题，
到时都退化成"只改这一个文件"，不会扩散到 runner / compare / report。
"""

import subprocess
import tempfile
import threading
import time
import uuid
from typing import BinaryIO, Protocol

from .config import SandboxConfig
from .types import CaseResult, CompileResult

# 宿主兜底在容器内 timeout 之外多给的宽限（秒）：要覆盖 docker CLI 启动与容器冷启动
_HOST_TIMEOUT_GRACE = 10.0
# 容器内计时标记。学生程序的 stderr 被丢进 /dev/null，stderr 管道只承载这类标记行。
_ELAPSED_PREFIX = "__SANDBOX_ELAPSED_NS__="
_NO_BINARY_MARKER = "__SANDBOX_NO_BINARY__"


class SandboxInfrastructureError(Exception):
    """沙箱基础设施不可用（Docker 没起、镜像拉不到等）。

    这类错误不是学生代码的问题，必须与"编译失败"分开：
    前者进 error_message，后者进 compilation_error_message。
    """
    

class SandboxBackend(Protocol):
    """沙箱后端的能力清单。runner 只依赖这个协议，不认识 Docker。"""

    def compile(self, source: str) -> CompileResult:
        """编译源码，返回编译结果。

        约束：
        - **不要**把宿主目录（比如 d:\\marking_uploads）挂载进容器——
          那等于把宿主文件系统暴露给学生代码。源码读进内存后经 stdin 传入，
          或用 docker cp 送进一次性容器。
        - 基础设施类失败（docker 未安装、守护进程没起、容器启动失败）抛
          SandboxInfrastructureError；`CompileResult(ok=False)` 只表示
          "学生代码编译不过"，两者不能混。
        """
        ...

    def execute(self, stdin_text: str) -> CaseResult:
        """拿 stdin_text 当标准输入，跑一次已编译好的产物。

        约束：
        - stdin 天然就是说明书要的"模拟人输入"。
        - 每个用例开一个**全新容器**（--rm）：某个用例死循环或崩溃，
          不能污染后面的用例，否则一崩全废。
        - elapsed 只反映程序在容器内执行的那段时间，**不得**计入 docker CLI
          启动、容器创建与卷挂载。宿主侧墙钟不能直接拿来当 elapsed。
        - 宿主侧超时必须兜底（容器内限时依赖镜像能力，不可靠）；
          超时后必须 kill 并 `docker rm -f <name>` 清理容器。
          ⚠️ `--stop-timeout` 是 `docker stop` 的善后秒数，**不是运行时限**。
        - stdout 必须在**读取过程中**按字节上限截断。先全量捕获再截断的话，
          `while(1) printf` 会先把宿主内存打爆，截断救不回来。
        - 失败语义：超时 / 崩溃 → 正常返回 CaseResult（timed_out、exit_code
          如实填），不抛异常；基础设施不可用 → 抛 SandboxInfrastructureError。
        """
        ...

    def close(self) -> None:
        """释放容器 / 临时文件。runner 必须保证它一定被调到（try/finally）。"""
        ...


def _drain_capped(pipe: BinaryIO, limit: int, sink: bytearray) -> None:
    """边读边截：只保留前 limit 字节，其余读掉丢弃。

    读空管子进程会阻塞在写管道上——截断之后必须继续读，否则程序“假死”到超时。
    """
    while True:
        chunk = pipe.read(65536)
        if not chunk:
            return
        room = limit - len(sink)
        if room > 0:
            sink.extend(chunk[:room])


def _parse_elapsed(stderr_text: str) -> float | None:
    """从 stderr 里取容器内计时标记（纳秒）；没有就返回 None。"""
    for line in reversed(stderr_text.splitlines()):
        if line.startswith(_ELAPSED_PREFIX):
            digits = line[len(_ELAPSED_PREFIX):]
            return int(digits) / 1e9 if digits.isdigit() else None
    return None


class DockerBackend:
    """真后端：用 Docker 容器编译、运行学生代码。

    编译容器与运行容器共用**同一个命名卷**：
    - 编译时挂到 /box，可写，产物是 /box/out
    - 运行时挂到 /box，只读，执行 /box/out

    一个 DockerBackend 实例对应一次提交（一个卷）。
    """

    def __init__(self, config: SandboxConfig) -> None:
        self.config = config
        self._volume = f"sandbox-{uuid.uuid4().hex}"

    def compile(self, source: str) -> CompileResult:
        """编译源码，产物写入共享卷的 /box/out。

        已知缺口（待你裁定，动手修之前先看这里）：
        - 镜像里缺 `bash` / `g++` 时 docker 返回 126/127，当前会落到
          `CompileResult(ok=False)`，被当成学生的编译错误。建议归入
          `SandboxInfrastructureError`，与"代码写错"分开。
        - `capture_output=True` 对编译器 stderr 同样没有上限。
        - `-static` 是否链得上取决于镜像，见 VM 验证清单。
        - 编译超时当前归为 `SandboxInfrastructureError`，但模板爆炸的学生代码
          也可能真的编 30 秒以上——那样会被说成"沙箱不可用"。要不要改成
          `CompileResult(ok=False, stderr="编译超时")`，由你裁定。
        """
        name = f"cc-{uuid.uuid4().hex}"
        # 编译容器刻意以 root 运行：命名卷初始属主是 root，非 root 写不进 /box。
        # 代价是编译阶段 g++ 以 root 执行，靠无网、只读根、cap 全丢兜底。
        # 运行容器会降到 65534；产物默认 0755，仍可执行。
        cmd = [
            "docker", "run", "--rm",
            "--name", name,
            "--network", "none",
            "--memory", self.config.memory_limit,
            "--memory-swap", self.config.memory_limit,
            "--cpus", self.config.cpus,
            "--pids-limit", str(self.config.pids_limit),
            "--read-only",
            "--tmpfs", "/tmp:size=64m,mode=1777",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "-v", f"{self._volume}:/box",
            "-i",
            self.config.image,
            "bash", "-c",
            "cat > /box/main.cpp && g++ -O2 -static -std=c++17 /box/main.cpp -o /box/out",
        ]

        try:
            proc = subprocess.run(
                cmd,
                input=source.encode("utf-8"),
                capture_output=True,
                timeout=self.config.compile_timeout,
            )
        except FileNotFoundError as exc:
            raise SandboxInfrastructureError(
                "找不到 docker 命令：Docker 未安装，或不在 PATH 中"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
            raise SandboxInfrastructureError(
                f"编译超时（>{self.config.compile_timeout} 秒）"
            ) from exc

        stderr = proc.stderr.decode("utf-8", errors="replace")

        if proc.returncode == 125:
            raise SandboxInfrastructureError(f"Docker 执行失败：{stderr}")

        return CompileResult(ok=proc.returncode == 0, stderr=stderr)

    def execute(self, stdin_text: str) -> CaseResult:
        """跑 /box/out 一次，返回执行结果。

        实现要点：
        - 同一个 self._volume 以**只读**挂到 /box；每个用例一个新容器（--rm）。
        - 容器内用 coreutils 的 timeout 限时（退出码 124）；宿主侧再加一道兜底。
          宿主超时杀的是 docker CLI，容器不一定跟着停，所以必须 docker rm -f。
        - elapsed 来自容器内 `date +%s%N` 的前后差，不含 CLI 启动与容器冷启动。
        - 学生程序的 stderr 在容器内丢进 /dev/null，stderr 管道只留给本函数的标记行；
          stdout 由读线程边读边截到 config.output_limit。
        - /box/out 不存在 → SandboxInfrastructureError（编译阶段本应已短路）。
        - 超时 / 崩溃属于"该用例判错"：正常返回 CaseResult，不抛异常。
        """
        name = f"run-{uuid.uuid4().hex}"
        script = (
            f"if [ ! -x /box/out ]; then echo {_NO_BINARY_MARKER} >&2; exit 125; fi; "
            "start=$(date +%s%N); "
            f"timeout -s KILL {self.config.run_timeout:g} /box/out 2>/dev/null; rc=$?; "
            "end=$(date +%s%N); "
            f"echo '{_ELAPSED_PREFIX}'$((end-start)) >&2; "
            "exit $rc"
        )
        cmd = [
            "docker", "run", "--rm",
            "--name", name,
            "--network", "none",
            "--memory", self.config.memory_limit,
            "--memory-swap", self.config.memory_limit,
            "--cpus", self.config.cpus,
            "--pids-limit", str(self.config.pids_limit),
            "--read-only",
            "--tmpfs", "/tmp:size=64m,mode=1777",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--user", "65534:65534",
            "-v", f"{self._volume}:/box:ro",
            "-i",
            self.config.image,
            "bash", "-c", script,
        ]

        stdout_buf = bytearray()
        stderr_buf = bytearray()
        started = time.monotonic()
        # stdin 走临时文件而不是 PIPE：用例输入可能很大，主线程写管道会和
        # 输出读线程抢锁/死锁；文件交给 docker CLI 自己读最省事。
        with tempfile.TemporaryFile() as stdin_file:
            stdin_file.write(stdin_text.encode("utf-8"))
            stdin_file.seek(0)
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=stdin_file,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                raise SandboxInfrastructureError(
                    "找不到 docker 命令：Docker 未安装，或不在 PATH 中"
                ) from exc

            readers = [
                threading.Thread(
                    target=_drain_capped,
                    args=(pipe, self.config.output_limit, sink),
                    daemon=True,
                )
                for pipe, sink in ((proc.stdout, stdout_buf), (proc.stderr, stderr_buf))
            ]
            for reader in readers:
                reader.start()

            try:
                returncode = proc.wait(
                    timeout=self.config.run_timeout + _HOST_TIMEOUT_GRACE
                )
                host_timed_out = False
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                try:
                    # 守护进程本身卡死时这里也会卡，清理是尽力而为，不能再把宿主拖住
                    subprocess.run(
                        ["docker", "rm", "-f", name], capture_output=True, timeout=10
                    )
                except Exception:
                    pass
                returncode = -1
                host_timed_out = True

            for reader in readers:
                reader.join(timeout=5.0)

        host_elapsed = time.monotonic() - started
        stderr_text = stderr_buf.decode("utf-8", errors="replace")

        if _NO_BINARY_MARKER in stderr_text:
            raise SandboxInfrastructureError(
                f"容器内找不到 /box/out（共享卷 {self._volume}），编译产物丢失"
            )

        elapsed = _parse_elapsed(stderr_text)
        # 125 是 docker CLI 自己的失败码（容器没起来、镜像拉不到等）。
        # 容器里的学生程序也可能恰好 return 125，区别在于 wrapper 跑没跑完：
        # 跑完了就一定有计时标记，没有标记才算 CLI 失败。
        if elapsed is None and not host_timed_out and returncode == 125:
            raise SandboxInfrastructureError(
                f"Docker 执行失败：{stderr_text.strip() or '退出码 125'}"
            )

        if elapsed is None:
            # 容器内标记没拿到（容器没正常收尾）：宿主墙钟含冷启动，只作异常路径兜底。
            elapsed = self.config.run_timeout if host_timed_out else host_elapsed

        return CaseResult(
            stdout=stdout_buf.decode("utf-8", errors="replace"),
            elapsed=elapsed,
            timed_out=host_timed_out or returncode == 124,
            exit_code=-1 if host_timed_out else returncode,
        )

    def close(self) -> None:
        """删除本次提交的命名卷。幂等，且**不抛异常**。"""
        try:
            subprocess.run(
                ["docker", "volume", "rm", "-f", self._volume],
                capture_output=True,
                timeout=10,
            )
        except Exception:
            # 在 sandbox_run_validation 的 finally 里，抛出会盖住真实原因
            pass


class FakeBackend:
    """假后端：不碰 Docker，专门用来把 runner / report 测透。

    ★ 本机没装 Docker，这是当前**唯一**能端到端跑通的路径。
    做法：预置两张映射表（源码 → CompileResult、stdin → CaseResult），
    照着 Protocol 原样返回即可，不需要任何容器知识。
    """

    def __init__(
        self,
        compile_map: dict[str, CompileResult],
        run_map: dict[str, CaseResult],
    ) -> None:
        self.compile_map = dict(compile_map)
        self.run_map = dict(run_map)

    def compile(self, source: str) -> CompileResult:
        return self.compile_map[source]

    def execute(self, stdin_text: str) -> CaseResult:
        return self.run_map[stdin_text]

    def close(self) -> None:
        pass
