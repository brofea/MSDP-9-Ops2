import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Literal


class TransientModelError(Exception):
    """Simulated temporary model failure used to validate retry logic."""


@dataclass
class RuntimePolicy:
    """Central runtime policy for classroom-friendly experiments."""

    max_concurrency: int = 3
    request_timeout: float = 0.8
    retries: int = 2
    retry_base_delay: float = 0.1
    token_budget: int = 240
    token_window_seconds: float = 6.0
    over_budget: Literal["reject", "queue"] = "reject"


@dataclass
class AgentRequest:
    """Minimal request protocol accepted by the runtime controller."""

    prompt: str
    request_id: str
    metadata: dict = field(default_factory=dict)


@dataclass
class AgentResult:
    """Structured result returned to callers and written to JSONL logs."""

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


ModelCall = Callable[[str, int], Awaitable[str]]


def describe_exception(exc: Exception) -> str:
    detail = str(exc).strip()
    if not detail:
        return type(exc).__name__
    return f"{type(exc).__name__}: {detail[:160]}"


def estimate_tokens(text: str) -> int:
    """Estimate tokens with a lightweight rule for budget-flow teaching."""

    if not text:
        return 0
    chinese_chars = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other_chars = len(text) - chinese_chars
    return chinese_chars + max(1, other_chars // 4)


def estimate_cost(total_tokens: int, price_per_1k: float = 0.002) -> float:
    """Calculate a simulated cost from estimated tokens."""

    return round(total_tokens / 1000 * price_per_1k, 6)


class BudgetState:
    """Short-window token budget controller with reject or queue behavior."""

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
        """Reserve request budget before occupying a concurrency slot."""

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
                wait_seconds = max(
                    0.05,
                    self.policy.token_window_seconds - (now - oldest_ts) + 0.01,
                )
                await asyncio.sleep(min(wait_seconds, 0.25))


class JsonlLogger:
    """Append runtime results to JSONL for later AgentOps analysis."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def write(self, event: dict) -> None:
        async with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")


async def fake_model_call(prompt: str, attempt: int) -> str:
    """Simulate a network model call that can succeed, timeout, or fail once."""

    if "慢请求" in prompt:
        await asyncio.sleep(2.0)
        return "这个结果通常不会在默认超时时间内返回。"

    await asyncio.sleep(0.15 + (len(prompt) % 3) * 0.05)

    if "临时失败" in prompt and attempt == 1:
        raise TransientModelError("模拟模型服务临时失败")

    return f"模拟回答：{prompt[:24]}"


class RuntimeController:
    """Unify concurrency, budget, timeout, retry, and logging decisions."""

    def __init__(
        self,
        policy: RuntimePolicy,
        log_path: str | Path = "logs/runtime.jsonl",
        model_call: ModelCall = fake_model_call,
    ):
        self.policy = policy
        self.semaphore = asyncio.Semaphore(policy.max_concurrency)
        self.budget = BudgetState(policy)
        self.logger = JsonlLogger(log_path)
        self.model_call = model_call

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
                        self.model_call(request.prompt, attempt),
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
                    last_error = describe_exception(exc)
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


async def run_batch(
    prompts: list[str],
    policy: RuntimePolicy,
    log_path: str | Path,
    model_call: ModelCall = fake_model_call,
) -> list[AgentResult]:
    """Run a batch of prompts for benchmark scripts and tests."""

    controller = RuntimeController(policy, log_path=log_path, model_call=model_call)
    requests = [
        AgentRequest(prompt=prompt, request_id=f"req-{index:03d}")
        for index, prompt in enumerate(prompts, start=1)
    ]
    return await asyncio.gather(*(controller.handle(request) for request in requests))
