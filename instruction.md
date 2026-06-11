# 8. Agent 运行时治理：异步并发、超时重试与 Token 预算控制 - 实验手册

## 实验主题

前序实验已经让同学们理解了 Agent 工具调用、Skill 能力沉淀和 Harness 安全护栏。本实验进一步解决 Agent 系统运行时的工程问题：当多个请求同时到来时，系统如何限制并发；当模型调用变慢或临时失败时，系统如何超时、重试和降级；当 Token 消耗接近预算时，系统应该排队、拒绝还是继续执行。

本实验把“异步并发”和“Token 成本控制”放到同一个运行时控制器中。这样做的原因是：真实 Agent 系统的慢、贵、不稳定往往同时出现，不能只写一个 `asyncio.gather` 就认为系统可上线，也不能只统计总 Token 而不决定超预算时的行为。

## 前后课程关系

本章承接第7章的 Harness 思想：第7章负责判断“这个工具调用能不能执行”，第8章负责判断“这个请求什么时候执行、执行多久、失败后重试几次、预算不够时怎么办”。下一章第9章会讨论多 Agent 状态机编排，但第9章不会重新实现并发、超时和预算控制，而是把这些能力视为运行时基础设施。

可以把三章关系理解为：

```text
第7章 Harness：守住能力边界
    ↓
第8章 Runtime：控制执行过程
    ↓
第9章 Workflow：编排多个 Agent 节点
    ↓
第10章 API：把能力交付给外部调用方
```

## 实验目标

完成本实验后，同学们应能够：

1. 区分同步调用、异步并发、任务排队和请求拒绝。
2. 使用 `asyncio.Semaphore` 控制同时执行的模型调用数量。
3. 使用 `asyncio.wait_for` 实现单次请求超时。
4. 实现有限重试、指数退避和可解释失败结果。
5. 设计轻量 Token 估算器和短窗口预算控制器。
6. 理解预算超限时“等待”和“拒绝”两种策略的适用场景。
7. 将每次请求结果写入 JSONL 日志，便于后续 AgentOps 分析。
8. 使用 pytest 验证默认流程不会长时间阻塞。
9. 使用 conda 和 Docker 两种方式复现实验。
10. 能把运行时失败反馈写回文档或配置，而不是只在终端里临时处理。

## 课程概览

本实验建议安排 300 分钟。如果课堂时间压缩为 240 分钟，可将 Docker 和真实 API 接入作为课后扩展。

| 时间段 | 教学环节 | 核心目标 | 关键技术栈 |
| :-- | :-- | :-- | :-- |
| 0-25' | 运行时治理导入 | 理解慢、贵、不稳定的来源 | Runtime, Budget |
| 25-55' | 环境与目标契约 | 建立可复现实验目录和成功标准 | conda, pytest |
| 55-100' | 请求与结果协议 | 定义 AgentRequest、AgentResult | dataclass |
| 100-145' | 并发、超时与重试 | 控制并发上限和失败恢复 | asyncio |
| 145-190' | Token 预算控制 | 实现短窗口预算和拒绝策略 | Token Meter |
| 190-225' | JSONL 日志与压测 | 观察耗时、重试、拒绝和成本 | JSONL |
| 225-260' | 自动化测试 | 验证默认实验可快速跑通 | pytest |
| 260-285' | Docker 复现 | 用容器验证环境一致性 | Docker |
| 285-300' | 复盘与扩展 | 讨论真实 API、排队模式和服务化 | AgentOps |

## 实验安全注意事项

1. 默认实验使用模拟模型函数，不调用真实在线 API，不产生真实费用。
2. 如果改接真实 API，必须先设置小并发、短超时和明确预算上限。
3. 不要把 API Key 写入代码、报告、日志或截图。
4. 不要把预算超限后的行为写成“无限等待”；课堂默认路径必须能在短时间内跑完。
5. 压测时只使用教学样本，不要对公共服务或学校网络发起高并发请求。
6. JSONL 日志只记录请求摘要、状态、耗时、Token 估算和错误类型，不记录敏感正文。

## 环境准备与验证

### 1. 创建实验目录

下面这组命令用于创建独立实验目录。macOS 与 Linux 终端可以直接使用：

```bash
mkdir agent_runtime_lab
cd agent_runtime_lab
conda create -n agent-runtime-lab python=3.11 -y
conda activate agent-runtime-lab
python --version
```

如果同学们没有 conda，也可以使用 Python 自带虚拟环境：

```bash
python -m venv .venv
source .venv/bin/activate
python --version
```

Windows PowerShell 使用下面的写法：

```powershell
mkdir agent_runtime_lab
cd agent_runtime_lab
conda create -n agent-runtime-lab python=3.11 -y
conda activate agent-runtime-lab
python --version
```

如果 Windows 已安装 Miniconda，但 PowerShell 提示无法识别 `conda`，可以改用 Anaconda Prompt；也可以执行 `& "$env:USERPROFILE\miniconda3\Scripts\conda.exe" init powershell` 后重新打开 PowerShell。实际安装目录不同的同学需要相应调整路径。

如果使用 Windows 的 `venv`：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python --version
```

### 2. 安装依赖

本实验主体只需要标准库，自动化测试使用 pytest：

```bash
pip install pytest
```

### 3. 建议文件结构

```text
agent_runtime_lab/
├── runtime_controller.py
├── demo_benchmark.py
├── requirements.txt
├── Dockerfile
├── logs/
└── tests/
    └── test_runtime_controller.py
```

## 第一阶段：定义运行时目标契约

### 目标

先把本实验的运行目标写清楚。运行时治理不是“让程序尽量跑”，而是让程序在明确边界内运行：并发不超过上限、单次调用有超时、失败有有限重试、预算不够时有确定策略、最终结果可观察。

本实验默认成功标准如下：

| 目标 | 默认值 | 教学含义 |
| :-- | :-- | :-- |
| 最大并发数 | 3 | 避免一次性压垮模型或 API |
| 单次请求超时 | 0.8 秒 | 慢请求不能无限占用资源 |
| 最大重试次数 | 2 | 临时失败可以恢复，但不能无限重试 |
| Token 预算窗口 | 6 秒 | 课堂演示用短窗口，避免等待 60 秒 |
| 超预算策略 | reject | 默认直接拒绝，保证课堂流程可跑完 |
| 默认压测耗时 | 小于 10 秒 | 文档必须能被完整复现 |

真实生产环境常使用 60 秒或更长窗口，本实验使用 6 秒窗口是为了让课堂验证可控。后面会说明如何切换到排队等待策略。

## 第二阶段：实现运行时控制器

### 目标

这一阶段创建 `runtime_controller.py`。该文件包含请求协议、结果协议、Token 估算、预算状态、JSONL 日志和运行时控制器。代码较长，但它们属于同一个运行时边界，放在一个文件中便于课堂观察。

创建 `runtime_controller.py`：

```python
import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal


class TransientModelError(Exception):
    """模拟模型服务的临时失败，用于验证重试逻辑。"""


@dataclass
class RuntimePolicy:
    """运行时策略集中保存，便于课堂中调整并观察效果。"""

    max_concurrency: int = 3
    request_timeout: float = 0.8
    retries: int = 2
    retry_base_delay: float = 0.1
    token_budget: int = 240
    token_window_seconds: float = 6.0
    over_budget: Literal["reject", "queue"] = "reject"


@dataclass
class AgentRequest:
    """进入运行时控制器的最小请求协议。"""

    prompt: str
    request_id: str
    metadata: dict = field(default_factory=dict)


@dataclass
class AgentResult:
    """运行时返回给调用方的结构化结果。"""

    request_id: str
    ok: bool
    status: str
    answer: str
    attempts: int
    elapsed_ms: int
    prompt_tokens: int
    completion_tokens: int
    cost: float
    error: str | None = None


def estimate_tokens(text: str) -> int:
    """使用粗略规则估算 Token，课堂重点是预算流程而不是真实 tokenizer。"""

    if not text:
        return 0
    chinese_chars = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other_chars = len(text) - chinese_chars
    return chinese_chars + max(1, other_chars // 4)


def estimate_cost(total_tokens: int, price_per_1k: float = 0.002) -> float:
    """根据模拟单价计算成本，真实项目应读取模型供应商价格。"""

    return round(total_tokens / 1000 * price_per_1k, 6)


class BudgetState:
    """短窗口 Token 预算控制器，支持拒绝或排队等待。"""

    def __init__(self, policy: RuntimePolicy):
        self.policy = policy
        self.events: list[tuple[float, int]] = []
        self._lock = asyncio.Lock()

    def _cleanup(self, now: float) -> None:
        window = self.policy.token_window_seconds
        self.events = [(ts, tokens) for ts, tokens in self.events if now - ts <= window]

    def current_tokens(self) -> int:
        now = time.monotonic()
        self._cleanup(now)
        return sum(tokens for _, tokens in self.events)

    async def reserve(self, tokens: int) -> tuple[bool, str | None]:
        """为请求预留预算；默认 reject 策略不会长时间阻塞课堂实验。"""

        if tokens > self.policy.token_budget:
            return False, "single_request_over_budget"

        async with self._lock:
            while True:
                now = time.monotonic()
                self._cleanup(now)
                used = sum(item_tokens for _, item_tokens in self.events)
                if used + tokens <= self.policy.token_budget:
                    self.events.append((now, tokens))
                    return True, None

                if self.policy.over_budget == "reject":
                    return False, "token_budget_exceeded"

                oldest_ts = self.events[0][0]
                wait_seconds = max(0.05, self.policy.token_window_seconds - (now - oldest_ts) + 0.01)
                await asyncio.sleep(min(wait_seconds, 0.25))


class JsonlLogger:
    """把每次运行结果写入 JSONL，便于后续分析和回放。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def write(self, event: dict) -> None:
        async with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")


async def fake_model_call(prompt: str, attempt: int) -> str:
    """模拟网络型模型调用：可成功、可超时、可临时失败。"""

    if "慢请求" in prompt:
        await asyncio.sleep(2.0)
        return "这个结果通常不会在默认超时时间内返回。"

    await asyncio.sleep(0.15 + (len(prompt) % 3) * 0.05)

    if "临时失败" in prompt and attempt == 1:
        raise TransientModelError("模拟模型服务临时失败")

    return f"模拟回答：{prompt[:24]}"


class RuntimeController:
    """统一处理并发、预算、超时、重试和日志。"""

    def __init__(self, policy: RuntimePolicy, log_path: str | Path = "logs/runtime.jsonl"):
        self.policy = policy
        self.semaphore = asyncio.Semaphore(policy.max_concurrency)
        self.budget = BudgetState(policy)
        self.logger = JsonlLogger(log_path)

    async def handle(self, request: AgentRequest) -> AgentResult:
        start = time.perf_counter()
        estimated_total_tokens = estimate_tokens(request.prompt) + 24
        reserved, reason = await self.budget.reserve(estimated_total_tokens)
        if not reserved:
            result = AgentResult(
                request_id=request.request_id,
                ok=False,
                status="rejected",
                answer="",
                attempts=0,
                elapsed_ms=0,
                prompt_tokens=estimate_tokens(request.prompt),
                completion_tokens=0,
                cost=0.0,
                error=reason,
            )
            await self.logger.write({"type": "runtime_result", **asdict(result)})
            return result

        async with self.semaphore:
            last_error: str | None = None
            for attempt in range(1, self.policy.retries + 2):
                try:
                    answer = await asyncio.wait_for(
                        fake_model_call(request.prompt, attempt),
                        timeout=self.policy.request_timeout,
                    )
                    elapsed_ms = int((time.perf_counter() - start) * 1000)
                    prompt_tokens = estimate_tokens(request.prompt)
                    completion_tokens = estimate_tokens(answer)
                    result = AgentResult(
                        request_id=request.request_id,
                        ok=True,
                        status="completed",
                        answer=answer,
                        attempts=attempt,
                        elapsed_ms=elapsed_ms,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        cost=estimate_cost(prompt_tokens + completion_tokens),
                    )
                    await self.logger.write({"type": "runtime_result", **asdict(result)})
                    return result
                except Exception as exc:
                    last_error = type(exc).__name__
                    if attempt <= self.policy.retries:
                        await asyncio.sleep(self.policy.retry_base_delay * attempt)

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        result = AgentResult(
            request_id=request.request_id,
            ok=False,
            status="failed",
            answer="",
            attempts=self.policy.retries + 1,
            elapsed_ms=elapsed_ms,
            prompt_tokens=estimate_tokens(request.prompt),
            completion_tokens=0,
            cost=0.0,
            error=last_error,
        )
        await self.logger.write({"type": "runtime_result", **asdict(result)})
        return result


async def run_batch(prompts: list[str], policy: RuntimePolicy, log_path: str | Path) -> list[AgentResult]:
    """批量运行请求，供压测脚本和测试用例复用。"""

    controller = RuntimeController(policy, log_path=log_path)
    requests = [
        AgentRequest(prompt=prompt, request_id=f"req-{index:03d}")
        for index, prompt in enumerate(prompts, start=1)
    ]
    return await asyncio.gather(*(controller.handle(request) for request in requests))
```

### 观察要点

这段代码体现了几个运行时决策：

1. `RuntimePolicy` 是策略入口，避免把并发数、超时、预算散落在不同函数里。
2. `BudgetState` 默认使用 `reject`，所以预算不足会立即返回结构化失败，不会让课堂实验等待一分钟。
3. `RuntimeController.handle()` 先判断预算，再进入并发信号量，避免已经超预算的请求占用执行槽。
4. 所有结果都会写入 `logs/runtime.jsonl`，失败也是可观察结果。

## 第三阶段：编写默认压测脚本

### 目标

这一阶段创建 `demo_benchmark.py`，用一组教学请求验证并发、重试、超时和预算控制。默认脚本应在 10 秒内完成。

创建 `demo_benchmark.py`：

```python
import asyncio
import json
import time
from dataclasses import asdict

from runtime_controller import RuntimePolicy, run_batch


PROMPTS = [
    "总结 Git 分支的作用。",
    "解释 TDD 的 Red-Green-Refactor。",
    "说明 Agent 工具调用和 Harness 的关系。",
    "这个请求会临时失败，请观察重试。",
    "生成一个 JSON 格式的任务单。",
]


async def main() -> None:
    policy = RuntimePolicy(
        max_concurrency=3,
        request_timeout=0.8,
        retries=2,
        token_budget=240,
        token_window_seconds=6,
        over_budget="reject",
    )

    start = time.perf_counter()
    results = await run_batch(PROMPTS * 2, policy, log_path="logs/runtime.jsonl")
    elapsed = time.perf_counter() - start

    for item in results:
        print(json.dumps(asdict(item), ensure_ascii=False))

    print("=" * 80)
    print("total_elapsed", round(elapsed, 3))
    print("completed", sum(1 for item in results if item.ok))
    print("rejected", sum(1 for item in results if item.status == "rejected"))
    print("failed", sum(1 for item in results if item.status == "failed"))
    print("total_cost", round(sum(item.cost for item in results), 6))


if __name__ == "__main__":
    asyncio.run(main())
```

运行：

```bash
python demo_benchmark.py
```

预期现象：

1. 总耗时通常小于 10 秒。
2. 包含“临时失败”的请求第一次失败后会重试成功。
3. 如果 Token 预算不足，结果会显示 `status="rejected"`，而不是长时间等待。
4. `logs/runtime.jsonl` 中会出现每个请求的结构化结果。

Windows PowerShell 也使用同一条运行命令：

```powershell
python demo_benchmark.py
```

## 第四阶段：观察预算拒绝策略

### 目标

这一阶段通过降低预算观察拒绝行为。拒绝策略适合交互式系统，因为用户不应该在预算不足时无提示地等待很久。

在终端运行：

```bash
python - <<'PY'
import asyncio
from runtime_controller import RuntimePolicy, run_batch

async def main():
    prompts = ["预算测试请求"] * 8
    policy = RuntimePolicy(token_budget=45, token_window_seconds=6, over_budget="reject")
    results = await run_batch(prompts, policy, log_path="logs/reject_demo.jsonl")
    print([(item.request_id, item.status, item.error) for item in results])

asyncio.run(main())
PY
```

Windows PowerShell 不建议直接使用 heredoc。可以创建 `reject_demo.py`：

```python
import asyncio

from runtime_controller import RuntimePolicy, run_batch


async def main() -> None:
    prompts = ["预算测试请求"] * 8
    policy = RuntimePolicy(token_budget=45, token_window_seconds=6, over_budget="reject")
    results = await run_batch(prompts, policy, log_path="logs/reject_demo.jsonl")
    print([(item.request_id, item.status, item.error) for item in results])


asyncio.run(main())
```

然后运行：

```powershell
python reject_demo.py
```

## 第五阶段：观察排队等待策略

### 目标

排队策略适合后台批处理任务。为了避免课堂等待过久，本实验仍使用短窗口，并限制请求数量。

创建 `queue_demo.py`：

```python
import asyncio
import time

from runtime_controller import RuntimePolicy, run_batch


async def main() -> None:
    prompts = ["排队预算测试"] * 4
    policy = RuntimePolicy(
        max_concurrency=2,
        token_budget=55,
        token_window_seconds=1.5,
        over_budget="queue",
    )

    start = time.perf_counter()
    results = await run_batch(prompts, policy, log_path="logs/queue_demo.jsonl")
    elapsed = time.perf_counter() - start

    print("elapsed", round(elapsed, 3))
    print([(item.request_id, item.status) for item in results])


asyncio.run(main())
```

运行：

```bash
python queue_demo.py
```

观察：排队模式会比拒绝模式慢，但因为窗口只有 1.5 秒，不会出现 60 秒级等待。真实系统如果使用 60 秒窗口，应该把这类等待放在后台任务中，并给用户返回“已排队”的状态。

## 第六阶段：编写自动化测试

### 目标

本阶段用 pytest 固化本实验的可运行标准。测试不是为了追求覆盖率，而是保证默认流程不会再次退化成“文档能写但课堂跑不完”。

创建 `tests/test_runtime_controller.py`：

```python
import asyncio
import time

from runtime_controller import RuntimePolicy, run_batch


def test_default_batch_finishes_quickly(tmp_path):
    prompts = [
        "总结 Git 分支。",
        "解释 TDD。",
        "这个请求会临时失败，请观察重试。",
        "说明 Harness。",
    ] * 2
    policy = RuntimePolicy(token_budget=220, token_window_seconds=6, over_budget="reject")

    start = time.perf_counter()
    results = asyncio.run(run_batch(prompts, policy, tmp_path / "runtime.jsonl"))
    elapsed = time.perf_counter() - start

    assert elapsed < 10
    assert any(item.attempts > 1 for item in results)
    assert all(item.status in {"completed", "rejected", "failed"} for item in results)


def test_over_budget_rejects_without_waiting(tmp_path):
    prompts = ["预算测试请求"] * 10
    policy = RuntimePolicy(token_budget=40, token_window_seconds=6, over_budget="reject")

    start = time.perf_counter()
    results = asyncio.run(run_batch(prompts, policy, tmp_path / "reject.jsonl"))
    elapsed = time.perf_counter() - start

    assert elapsed < 3
    assert any(item.status == "rejected" for item in results)


def test_slow_request_fails_with_timeout(tmp_path):
    policy = RuntimePolicy(request_timeout=0.2, retries=1, token_budget=200)

    results = asyncio.run(run_batch(["这是一个慢请求"], policy, tmp_path / "timeout.jsonl"))

    assert results[0].status == "failed"
    assert results[0].error in {"TimeoutError"}
```

运行：

```bash
python -m pytest -q
```

预期看到：

```text
3 passed
```

如果测试失败，不要只修改测试让它通过；应回到运行时策略或文档说明中分析失败原因。例如：默认预算太低、超时时间太短、平台启动过慢，都可能需要写进故障排除部分。

## 第七阶段：Docker 复现

### 目标

Docker 用于验证“文档中的代码不是只在某一台电脑上碰巧能跑”。如果本机没有启动 Docker，可以先完成 conda 路径，并在报告中说明 Docker 环境状态。

创建 `requirements.txt`：

```text
pytest
```

创建 `Dockerfile`：

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --index-url "$PIP_INDEX_URL" --no-cache-dir -r requirements.txt

COPY runtime_controller.py demo_benchmark.py ./
COPY tests ./tests

CMD ["python", "-m", "pytest", "-q"]
```

构建镜像：

```bash
docker build -t agent-runtime-lab .
```

如果当前网络访问默认 PyPI 不稳定，可改用课堂网络可访问的镜像源：

```bash
docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple -t agent-runtime-lab .
```

运行测试：

```bash
docker run --rm agent-runtime-lab
```

如果看到 Docker daemon 未启动，例如 `Cannot connect to the Docker daemon`，说明 Docker 客户端存在但后台服务未启动。macOS/Windows 需要先打开 Docker Desktop；Linux 需要确认当前用户是否有权限访问 Docker 服务。该问题属于环境问题，不应通过修改 Python 代码解决。

## 第八阶段：真实 API 接入扩展

### 目标

本实验默认不调用真实模型。如果要接入真实 OpenAI 兼容 API 或本地 Ollama，应保持运行时边界不变：真实模型函数只替换 `fake_model_call()`，不要移除并发、超时、重试、预算和日志。

接入真实 API 时至少保留以下约束：

1. `max_concurrency` 从 1 或 2 开始，不要一开始就高并发。
2. `request_timeout` 设置为明确值，例如 15 到 30 秒。
3. `retries` 不超过 2 次，避免失败时放大成本。
4. Token 预算由课堂或个人预算决定，并在环境变量或配置中设置。
5. 日志中不要写入 API Key、完整用户隐私文本或内部系统地址。

如果使用本地 Qwen/Ollama，建议先关闭 thinking 或使用较小输出长度，再观察是否仍超过课堂可接受时长。模型推理慢属于运行时现象，应通过超时、较短上下文、较小输出和任务拆分处理，而不是把超时时间无限加长。

## 故障排除 FAQ

### Q1: 为什么默认预算窗口不是 60 秒？

真实 API 常按分钟限流，但课堂实验如果默认使用 60 秒等待，很容易让同学误以为程序卡死。本实验用 6 秒短窗口教学，帮助同学先理解机制。生产环境可以把 `token_window_seconds` 改回 60，并配合后台队列和用户可见状态。

### Q2: 为什么默认超预算策略是 reject？

交互式 Agent 更适合立即返回“预算不足”或“稍后重试”，后台批处理才适合排队等待。默认 `reject` 可以保证手册流程稳定跑完。

### Q3: 为什么异步并发不等于更快的模型？

异步并发减少的是等待时间重叠，不能让单次模型推理变快。如果模型本身慢，仍然需要超时、缓存、任务拆分或更小模型。

### Q4: 为什么有些请求失败仍然算实验成功？

运行时治理不是保证所有请求都成功，而是保证成功、失败、拒绝和超时都有明确、可记录、可解释的结果。

### Q5: Windows 上 heredoc 命令不能运行怎么办？

Windows PowerShell 对 `python - <<'PY'` 支持不一致。文档中提供了创建 `.py` 文件再运行的替代方式，优先使用该方式。

### Q6: Docker 构建失败怎么办？

先确认 `docker --version` 和 Docker daemon 是否可用。如果失败发生在 `pip install` 且日志中出现 SSL、timeout 或 PyPI 下载失败，可使用 `--build-arg PIP_INDEX_URL=...` 切换到课堂网络可访问的镜像源。Docker 失败时，不要跳过 conda 路径；应先完成 conda 流程，再记录 Docker 的环境问题。

### Q7: `pip install` 下载依赖时出现 SSL EOF 或 timeout 怎么办？

这通常是网络或镜像源问题，不是实验代码问题。可以临时使用课堂网络可访问的镜像源，例如 `python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt`。如果使用了镜像源，应在报告中说明。

## 三平台跑通检查

文档维护者发布本章前应至少完成以下检查：

| 平台 | 必做检查 | 通过标准 |
| :-- | :-- | :-- |
| macOS | conda 环境、`python demo_benchmark.py`、`python -m pytest -q` | 默认压测小于 10 秒，测试通过 |
| Linux | conda 或系统 Python、`python -m pytest -q`、可选 Docker | 命令与日志路径无平台差异 |
| Windows | PowerShell、conda、`python -m pytest -q` | 不依赖 Bash heredoc，路径可运行 |
| Docker | `docker build`、`docker run` | daemon 可用时测试通过 |

如果某个平台失败，应把失败原因回写到本 FAQ 或环境准备部分，而不是只在维护日志里记录。

## 参考资源

- Python asyncio: https://docs.python.org/3/library/asyncio.html
- Python dataclasses: https://docs.python.org/3/library/dataclasses.html
- pytest: https://docs.pytest.org/
- Docker: https://docs.docker.com/
