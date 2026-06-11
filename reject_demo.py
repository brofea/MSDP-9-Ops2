import asyncio

from runtime_controller import RuntimePolicy, run_batch


async def main() -> None:
    prompts = ["预算测试请求"] * 8
    policy = RuntimePolicy(token_budget=45, token_window_seconds=6, over_budget="reject")
    results = await run_batch(prompts, policy, log_path="logs/reject_demo.jsonl")
    print([(item.request_id, item.status, item.error) for item in results])


if __name__ == "__main__":
    asyncio.run(main())
