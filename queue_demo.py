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


if __name__ == "__main__":
    asyncio.run(main())
