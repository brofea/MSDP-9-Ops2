# Agent Runtime Lab

本项目完成“Agent 运行时治理：异步并发、超时重试与 Token 预算控制”实验。实现内容包括运行时控制器、默认压测、预算拒绝演示、排队演示、真实 DeepSeek API 演示、pytest 测试和 Dockerfile。

## 环境准备

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## 运行实验

默认压测：

```bash
.venv/bin/python demo_benchmark.py
```

预算拒绝：

```bash
.venv/bin/python reject_demo.py
```

排队模式：

```bash
.venv/bin/python queue_demo.py
```

真实 DeepSeek API 演示：

```bash
.venv/bin/python real_deepseek_demo.py
```

真实 API 需要 `.env` 中存在 `DEEPSEEK_API_KEY` 和 `DEEPSEEK_URL_BASE`，不要提交或展示 API Key。

## 测试

```bash
.venv/bin/python -m pytest -q
```

本次验证结果为 `3 passed in 1.65s`。

## Docker

```bash
docker build -t agent-runtime-lab .
docker run --rm agent-runtime-lab
```

本机已验证 Docker 客户端存在，但当前网络访问 Docker Hub 鉴权服务超时，因此使用本地 venv 作为替代复现路径。
