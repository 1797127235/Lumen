import socket

for host in ["api.deepseek.com", "dashscope.aliyuncs.com"]:
    try:
        ip = socket.gethostbyname(host)
        print(f"{host} -> {ip}")
    except Exception as e:
        print(f"{host} -> DNS 失败: {e}")

# 再试纯 TCP 连通
print()
for host, port in [("api.deepseek.com", 443), ("dashscope.aliyuncs.com", 443)]:
    try:
        s = socket.create_connection((host, port), timeout=8)
        s.close()
        print(f"TCP {host}:443 -> OK")
    except Exception as e:
        print(f"TCP {host}:443 -> 失败: {type(e).__name__}: {e}")
