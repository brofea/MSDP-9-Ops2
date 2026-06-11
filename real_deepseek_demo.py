import asyncio
import json
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path

from runtime_controller import RuntimePolicy, run_batch


def load_env(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    all_proxy = os.getenv("ALL_PROXY") or os.getenv("all_proxy")
    if all_proxy:
        os.environ.setdefault("HTTP_PROXY", all_proxy)
        os.environ.setdefault("HTTPS_PROXY", all_proxy)


async def deepseek_model_call(prompt: str, attempt: int) -> str:
    del attempt
    api_key = os.getenv("DEEPSEEK_API_KEY")
    base_url = os.getenv("DEEPSEEK_URL_BASE", "https://api.deepseek.com/v1").rstrip("/")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")

    payload = {
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        "messages": [
            {
                "role": "system",
                "content": "你是一个简洁的实验助手，用两句话以内回答。",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 80,
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    def open_request(context: ssl.SSLContext | None = None) -> dict:
        kwargs = {"timeout": 20}
        if context is not None:
            kwargs["context"] = context
        with urllib.request.urlopen(request, **kwargs) as response:
            return json.loads(response.read().decode("utf-8"))

    def call_api() -> str:
        try:
            body = open_request()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            raise RuntimeError(f"HTTPError {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            reason = str(exc.reason)
            if "CERTIFICATE_VERIFY_FAILED" in reason:
                # Lab fallback for local proxy environments with a self-signed chain.
                insecure_context = ssl._create_unverified_context()
                body = open_request(insecure_context)
                return body["choices"][0]["message"]["content"].strip()
            raise RuntimeError(f"URLError: {exc.reason}") from exc
        return body["choices"][0]["message"]["content"].strip()

    return await asyncio.to_thread(call_api)


async def main() -> None:
    load_env()
    prompts = [
        "用一句话说明 Agent 运行时为什么需要超时控制。",
        "用一句话说明 Token 预算控制的价值。",
    ]
    policy = RuntimePolicy(
        max_concurrency=1,
        request_timeout=25,
        retries=1,
        token_budget=160,
        token_window_seconds=60,
        over_budget="reject",
    )
    results = await run_batch(
        prompts,
        policy,
        log_path="logs/real_deepseek.jsonl",
        model_call=deepseek_model_call,
    )
    for item in results:
        print(json.dumps(asdict(item), ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
