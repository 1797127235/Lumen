import asyncio
from pathlib import Path

import httpx


async def try_connect(label: str, **client_kwargs):
    env = Path(".env").read_text(encoding="utf-8")
    key = ""
    for line in env.splitlines():
        if line.startswith("EMBEDDING_API_KEY="):
            key = line.split("=", 1)[1].strip()

    print(f"\n--- {label} ---")
    print(f"client_kwargs: {client_kwargs}")
    try:
        async with httpx.AsyncClient(timeout=30.0, **client_kwargs) as client:
            resp = await client.post(
                "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": "text-embedding-v4", "input": ["hello"]},
            )
            print(f"status: {resp.status_code}")
            if resp.status_code == 200:
                emb = resp.json().get("data", [{}])[0].get("embedding", [])
                print(f"✅ embedding 维度: {len(emb)}")
            else:
                print(f"body: {resp.text[:300]}")
    except Exception as e:
        print(f"❌ {type(e).__name__}: {e}")


async def main():
    # 1. 默认(走系统代理)
    await try_connect("默认(系统代理/trust_env=True)")
    # 2. 显式禁用代理
    await try_connect("禁用代理(trust_env=False)", trust_env=False)
    # 3. 看看 deepseek(你 LLM 在用)是否也走代理 —— 它能通说明代理对 deepseek 有效
    print("\n--- 对比: deepseek.com 连通性(默认) ---")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get("https://api.deepseek.com")
            print(f"deepseek status: {r.status_code}")
    except Exception as e:
        print(f"deepseek ❌ {type(e).__name__}: {e}")


asyncio.run(main())
