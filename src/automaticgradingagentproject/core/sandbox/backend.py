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
# 注意：标记与学生程序同 UID，不能当作认证通道；只用于尽力取时间，解析时忽略畸形行。
_ELAPSED_PREFIX = "__SANDBOX_ELAPSED_NS__="
# 编译容器的容器内 timeout 命中 124 时由 wrapper 写到 stderr。
# g++ 的 stderr 已重定向到 /box/compile.err，源码无法伪造这个标记。
_COMPILE_TIMEOUT_MARKER = "__SANDBOX_COMPILE_TIMEOUT__"
# GNU timeout 超时返回 124；部分实现（如 busybox）可能返回 137/143。
# 137 也可能是 OOM/SIGKILL，所以还必须结合 elapsed 判断。
_TIMEOUT_EXIT_CODES = frozenset({124, 137, 143})
# stderr 只保留尾部：计时标记最后写出，不能因为前面的 Docker 噪声被裁掉。
_STDERR_TAIL_LIMIT = 65536
_CLEANUP_TIMEOUT = 10


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
        """释放容器 / 临时文件。runner 必须保证它一定被调到（try/finally）。

        契约：幂等，且**不得抛异常**——它在 finally 里执行，一抛就会盖住
        真正的编译/执行故障。
        """
        ...


def _drain_capped(pipe: BinaryIO, limit: int, sink: bytearray) -> bool:
    """边读边截：只保留前 limit 字节，其余读掉丢弃。

    返回是否发生过截断。读空管子进程会阻塞在写管道上——截断之后必须继续读，
    否则程序“假死”到超时。
    """
    truncated = False
    while True:
        chunk = pipe.read(65536)
        if not chunk:
            return truncated
        room = limit - len(sink)
        if room > 0:
            sink.extend(chunk[:room])
        if len(chunk) > room:
            truncated = True


def _drain_tail(pipe: BinaryIO, limit: int, sink: bytearray) -> None:
    """只保留最后 limit 字节。用于 stderr：计时标记在最后写出。"""
    while True:
        chunk = pipe.read(65536)
        if not chunk:
            return
        sink.extend(chunk)
        overflow = len(sink) - limit
        if overflow > 0:
            del sink[:overflow]


def _parse_elapsed(stderr_text: str) -> float | None:
    """取最后一个合法的容器内计时标记（起始纳秒:结束纳秒）。

    标记形如 ``__SANDBOX_ELAPSED_NS__=123:456``；date 不可用时 wrapper 会写 ``NA:NA``。
    同 UID 的学生程序可以往 ``/proc/1/fd/2`` 写垃圾行，甚至用没有换行的字节把
    wrapper 的真标记粘到垃圾行尾部，因此按行内**最后一次**出现解析，并跳过任何
    畸形、超长或溢出的数字——不能因为一行坏标记就升级成基础设施错误。
    """
    for line in reversed(stderr_text.splitlines()):
        index = line.rfind(_ELAPSED_PREFIX)
        if index < 0:
            continue
        payload = line[index + len(_ELAPSED_PREFIX):].strip()
        if ":" not in payload:
            continue
        start_text, end_text = payload.split(":", 1)
        if not (start_text.isdigit() and end_text.isdigit()):
            continue
        try:
            start = int(start_text)
            end = int(end_text)
        except ValueError:
            # isdigit() 对 '²' 与超过 4300 位的数字也为 True，int() 仍可能失败。
            continue
        if end <= start:
            continue
        try:
            return (end - start) / 1e9
        except OverflowError:
            # 极端数字相减后转 float 可能溢出；按畸形标记跳过。
            continue
    return None


def _run_capped(
    cmd: list[str],
    stdin_bytes: bytes,
    timeout: float,
    stdout_limit: int,
) -> tuple[int, bytes, bytes, bool, bool, float]:
    """启动子进程并边读边限流。

    返回 ``(returncode, stdout, stderr, host_timed_out, stdout_truncated,
    host_elapsed)``。host_elapsed 在 wait 返回/被杀后立即采样，不含 reader join
    与容器清理时间——它只在宿主兜底路径上代替容器内计时。
    宿主超时只做兜底：杀掉的是 docker CLI，调用方还必须按容器名清理容器。
    stderr 只保留尾部，确保计时标记不被 Docker 噪声挤掉。
    """
    stdout_buf = bytearray()
    stderr_buf = bytearray()
    stdout_truncated = [False]

    try:
        stdin_file = tempfile.TemporaryFile()
    except OSError as exc:
        raise SandboxInfrastructureError(f"无法创建临时输入文件：{exc}") from exc

    try:
        try:
            stdin_file.write(stdin_bytes)
            stdin_file.seek(0)
        except OSError as exc:
            raise SandboxInfrastructureError(f"无法写入临时输入文件：{exc}") from exc

        started = time.monotonic()
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
        except OSError as exc:
            raise SandboxInfrastructureError(f"无法启动沙箱进程：{exc}") from exc

        def _read_stdout() -> None:
            stdout_truncated[0] = _drain_capped(proc.stdout, stdout_limit, stdout_buf)

        readers = [
            threading.Thread(target=_read_stdout, daemon=True),
            threading.Thread(
                target=_drain_tail,
                args=(proc.stderr, _STDERR_TAIL_LIMIT, stderr_buf),
                daemon=True,
            ),
        ]
        for reader in readers:
            reader.start()

        try:
            returncode = proc.wait(timeout=timeout)
            host_timed_out = False
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            returncode = -1
            host_timed_out = True

        # 在清理容器和 join 读线程之前采样，避免把善后时间算进 elapsed。
        host_elapsed = time.monotonic() - started

        for reader in readers:
            reader.join(timeout=5.0)

        return (
            returncode,
            bytes(stdout_buf),
            bytes(stderr_buf),
            host_timed_out,
            stdout_truncated[0],
            host_elapsed,
        )
    finally:
        stdin_file.close()


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
        # 异常路径下可能仍有容器存活；close() 依靠它做 best-effort 清理。
        self._containers: set[str] = set()

    def _remove_container(self, name: str) -> bool:
        """尝试删除容器，返回是否确认它已不存在；清理失败不能盖住原始异常。

        ``docker rm -f`` 对已经被 ``--rm`` 删掉的容器返回非 0（No such container），
        这种情形视为成功；否则 close() 会永远重试一个不存在的名字。
        """
        try:
            completed = subprocess.run(
                ["docker", "rm", "-f", name],
                capture_output=True,
                timeout=_CLEANUP_TIMEOUT,
            )
        except Exception:
            return False
        if completed.returncode == 0:
            return True
        stderr = completed.stderr.decode("utf-8", errors="replace").lower()
        return "no such container" in stderr

    def compile(self, source: str) -> CompileResult:
        """编译源码，产物写入共享卷的 /box/out。

        退出码语义：
        - 0：编译成功
        - 124 且带 _COMPILE_TIMEOUT_MARKER：容器内 timeout 命中 → 编译超时
        - 125/126/127：Docker CLI 或镜像缺少 bash/g++/cat → 基础设施错误
        - 137：编译进程被信号杀死（常见于 OOM）→ 基础设施错误
        - 负 returncode / 143：docker CLI 被信号终止 → 基础设施错误
        - 其他非零：学生代码编译失败

        编译超时按 `CompileResult(ok=False)` 返回。

        ⚠️ 镜像必须**预先拉取**（部署时 `docker pull`）。容器用 `--pull=never`：
        缺镜像会立刻以 125 失败并归为基础设施错误；否则 Docker 可能在镜像仓库
        上重试 30 秒以上，先撞上 compile_timeout，被误判成学生编译失败。
        """
        name = f"cc-{uuid.uuid4().hex}"
        # 编译容器刻意以 root 运行：命名卷初始属主是 root，非 root 写不进 /box。
        # 代价是编译阶段 g++ 以 root 执行，靠无网、只读根、cap 全丢兜底。
        # 运行容器会降到 65534；产物显式 chmod 0755，不依赖镜像 umask。
        script = (
            "cat > /box/main.cpp || exit 126; "
            f"timeout -k 1 {self.config.compile_timeout:g} "
            "g++ -O2 -fmax-errors=20 -static -std=c++17 /box/main.cpp -o /box/out "
            "2>/box/compile.err; "
            "rc=$?; "
            "cat /box/compile.err; "
            f"if [ \"$rc\" -eq 124 ]; then echo {_COMPILE_TIMEOUT_MARKER} >&2; fi; "
            "chmod 0755 /box/out 2>/dev/null; "
            "exit $rc"
        )
        cmd = [
            "docker", "run", "--rm",
            "--pull", "never",
            "--name", name,
            "--network", "none",
            "--log-driver", "json-file",
            "--log-opt", "max-size=1m",
            "--log-opt", "max-file=1",
            "--memory", self.config.memory_limit,
            "--memory-swap", self.config.memory_limit,
            "--cpus", self.config.cpus,
            "--pids-limit", str(self.config.pids_limit),
            "--read-only",
            "--tmpfs", "/tmp:size=64m,mode=1777",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--user", "0:0",
            "-v", f"{self._volume}:/box",
            "-i",
            self.config.image,
            "bash", "-c", script,
        ]

        self._containers.add(name)
        completed = False
        try:
            rc, stdout_bytes, stderr_bytes, host_timed_out, _, _ = _run_capped(
                cmd,
                source.encode("utf-8"),
                # 比容器内 timeout 多给宽限，让 124 + marker 先正常收尾；
                # 与 execute 的宿主兜底口径一致。
                self.config.compile_timeout + _HOST_TIMEOUT_GRACE,
                self.config.compile_output_limit,
            )
            completed = True
        finally:
            if completed:
                # CLI 被宿主杀/被信号终止时容器可能还活着，必须显式删；
                # 删不掉就保留名字，让 close() 还能重试。
                abnormal = host_timed_out or rc < 0 or rc == 143
                if abnormal:
                    if self._remove_container(name):
                        self._containers.discard(name)
                else:
                    self._containers.discard(name)

        diagnostics = stdout_bytes.decode("utf-8", errors="replace")
        cli_stderr = stderr_bytes.decode("utf-8", errors="replace")

        if host_timed_out:
            return CompileResult(
                ok=False,
                stderr=f"编译超时（超过 {self.config.compile_timeout:g} 秒）",
            )

        if _COMPILE_TIMEOUT_MARKER in cli_stderr:
            return CompileResult(
                ok=False,
                stderr=f"编译超时（超过 {self.config.compile_timeout:g} 秒）",
            )

        if rc < 0 or rc == 143:
            raise SandboxInfrastructureError(
                f"docker CLI 异常退出（退出码 {rc}），编译过程被中断"
            )

        if rc in (125, 126, 127):
            detail = cli_stderr.strip() or diagnostics.strip() or "无输出"
            raise SandboxInfrastructureError(
                f"Docker/镜像执行失败（退出码 {rc}）：{detail}"
            )

        if rc == 137:
            raise SandboxInfrastructureError(
                "编译进程被信号杀死（可能是内存超限或镜像问题）"
            )

        return CompileResult(ok=rc == 0, stderr=diagnostics)

    def execute(self, stdin_text: str) -> CaseResult:
        """跑 /box/out 一次，返回执行结果。

        实现要点：
        - 同一个 self._volume 以**只读**挂到 /box；每个用例一个新容器（--rm）。
        - 容器内用 ``timeout -k 1`` 限时：先 TERM，1 秒后 KILL；GNU coreutils 返回 124。
        - 宿主侧再加一道兜底；宿主超时杀的是 docker CLI，必须 docker rm -f。
        - elapsed 来自容器内 ``date +%s%N`` 的前后差，不含 CLI 启动与容器冷启动。
        - 学生程序的 stderr 在容器内丢进 /dev/null，stderr 管道只留给标记行；
          但标记与学生程序同 UID，不能当认证通道：产物是否存在靠 rc=125 且
          没有计时标记来判定，而不是靠学生能伪造的字符串。
        - stdout 由读线程边读边截到 config.output_limit。
        - /box/out 不存在 → SandboxInfrastructureError（编译阶段本应已短路）。
        - 超时 / 崩溃属于"该用例判错"：正常返回 CaseResult，不抛异常。
        """
        name = f"run-{uuid.uuid4().hex}"
        script = (
            "if [ ! -x /box/out ]; then exit 125; fi; "
            "start=$(date +%s%N 2>/dev/null || true); "
            f"timeout -k 1 {self.config.run_timeout:g} /box/out 2>/dev/null; rc=$?; "
            "end=$(date +%s%N 2>/dev/null || true); "
            f"echo {_ELAPSED_PREFIX}${{start:-NA}}:${{end:-NA}} >&2; "
            "exit $rc"
        )
        cmd = [
            "docker", "run", "--rm",
            "--pull", "never",
            "--name", name,
            "--network", "none",
            "--log-driver", "json-file",
            "--log-opt", "max-size=1m",
            "--log-opt", "max-file=1",
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

        self._containers.add(name)
        completed = False
        try:
            (
                rc,
                stdout_bytes,
                stderr_bytes,
                host_timed_out,
                truncated,
                host_elapsed,
            ) = _run_capped(
                cmd,
                stdin_text.encode("utf-8"),
                self.config.run_timeout + _HOST_TIMEOUT_GRACE,
                self.config.output_limit,
            )
            completed = True
        finally:
            if completed:
                # CLI 被宿主杀/被信号终止时容器可能还活着，必须显式删；
                # 删不掉就保留名字，让 close() 还能重试。
                abnormal = host_timed_out or rc < 0 or rc == 143
                if abnormal:
                    if self._remove_container(name):
                        self._containers.discard(name)
                else:
                    self._containers.discard(name)

        stderr_text = stderr_bytes.decode("utf-8", errors="replace")

        # 产物是否存在不再依赖学生可写的 stderr 哨兵：
        # wrapper 在 /box/out 缺失时直接 exit 125，且不会写计时标记。
        elapsed = _parse_elapsed(stderr_text)
        # 125 是 docker CLI 自己的失败码（容器没起来、镜像拉不到等）。
        # 容器里的学生程序也可能恰好 return 125，区别在于 wrapper 跑没跑完：
        # 跑完了就一定有计时标记，没有标记才算 CLI 失败。
        if elapsed is None and not host_timed_out and rc == 125:
            raise SandboxInfrastructureError(
                "Docker 执行失败，或容器内 /box/out 丢失（退出码 125）："
                f"{stderr_text.strip() or '无输出'}"
            )

        if elapsed is None:
            if not host_timed_out:
                raise SandboxInfrastructureError(
                    f"无法获取容器内计时标记（退出码 {rc}；"
                    "镜像可能缺少 GNU date，或容器未正常收尾）"
                )
            # 宿主兜底路径没有容器内标记，只能使用实测墙钟；这是异常路径的唯一证据。
            elapsed = host_elapsed

        # 124 既可能是 timeout 触发，也可能是学生程序自己 return 124。
        # 用 elapsed 是否接近 run_timeout 消歧，避免把快速返回 124 误判为超时。
        timed_out = host_timed_out or (
            rc in _TIMEOUT_EXIT_CODES
            and elapsed >= self.config.run_timeout * 0.95
        )
        exit_code = -1 if host_timed_out else (124 if timed_out else rc)

        return CaseResult(
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            elapsed=elapsed,
            exit_code=exit_code,
            timed_out=timed_out,
            truncated=truncated,
        )

    def close(self) -> None:
        """删除本次提交的容器与命名卷。幂等，且**不抛异常**。

        删除失败的容器名保留在 _containers 里，内部做两轮有界重试，
        不依赖调用方再次 close；容器清掉后再删卷，避免卷被占用。
        """
        for attempt in range(2):
            for name in tuple(self._containers):
                if self._remove_container(name):
                    self._containers.discard(name)
            if not self._containers:
                break
            if attempt == 0:
                time.sleep(0.2)

        try:
            subprocess.run(
                ["docker", "volume", "rm", "-f", self._volume],
                capture_output=True,
                timeout=_CLEANUP_TIMEOUT,
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
