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
