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
