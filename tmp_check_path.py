from pathlib import Path

p = Path("core/config.py").resolve()
print(f"file = {p}")
print(f"parents[0] = {p.parents[0]}")
print(f"parents[1] = {p.parents[1]}")
print(f"parents[2] = {p.parents[2]}")

# 当前 config.py 实际使用的路径
current_env = p.parents[2] / ".env"
print(f"\ncurrent env_file = {current_env}")
print(f"exists = {current_env.exists()}")

correct_env = p.parents[1] / ".env"
print(f"\ncorrect env_file = {correct_env}")
print(f"exists = {correct_env.exists()}")
