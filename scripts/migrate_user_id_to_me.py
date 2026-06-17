"""一次性迁移:把单用户身份从 telegram_chat_id (8595876131) 迁到 'me'。

用途:身份与渠道解耦后,把现有真实数据的 user_id 从 '8595876131' 改成 'me'。
迁移内容:
  1. 记忆目录:~/.lumen/memory/8595876131/  →  ~/.lumen/memory/me/
  2. DB:lumen.db 的 conversations/messages/lumen_state 表 user_id '8595876131' → 'me'

使用(必须先停后端):
  python scripts/migrate_user_id_to_me.py
  python scripts/migrate_user_id_to_me.py --dry-run   # 只打印不执行

安全:幂等。目标已存在则跳过。只动 user_id='8595876131' 的真实数据,
不碰测试数据(test_*/eval_*/cache_*/freeze_*/bench_*)。
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

# 真实用户旧身份 = config 的 telegram_chat_id(迁移前的真相源)
OLD_USER_ID = "8595876131"
NEW_USER_ID = "me"

LUMEN_HOME = Path(os.environ.get("LUMEN_HOME", str(Path.home() / ".lumen")))
MEM_DIR = LUMEN_HOME / "memory"
DB_PATH = Path("lumen.db")  # 项目根的 lumen.db(DATABASE_URL 默认相对路径)

TEST_PREFIXES = ("test_", "eval_", "cache_", "freeze_", "bench_")


def is_test(uid: str) -> bool:
    return any(uid.startswith(p) for p in TEST_PREFIXES)


def migrate_memory(dry_run: bool) -> None:
    src = MEM_DIR / OLD_USER_ID
    dst = MEM_DIR / NEW_USER_ID
    print(f"[memory] {src}")
    if not src.exists():
        print("  源目录不存在,跳过")
        return
    if dst.exists():
        print(f"  目标 {dst} 已存在,跳过(避免覆盖)")
        return
    print(f"  → {dst}")
    if not dry_run:
        src.rename(dst)


def migrate_db(dry_run: bool) -> None:
    if not DB_PATH.exists():
        print(f"[db] {DB_PATH} 不存在,跳过")
        return
    print(f"[db] {DB_PATH.absolute()}")
    conn = sqlite3.connect(str(DB_PATH))
    try:
        # messages 表无 user_id 列(按 conversation_id 归属),
        # 改 conversations.user_id 即可让消息间接归属正确,无需动 messages。
        for table in ("conversations", "lumen_state"):
            try:
                cur = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (OLD_USER_ID,))
                count = cur.fetchone()[0]
                if count == 0:
                    print(f"  {table}: 无 user_id={OLD_USER_ID} 的行,跳过")
                    continue
                print(f"  {table}: 迁移 {count} 行  user_id {OLD_USER_ID!r} → {NEW_USER_ID!r}")
                if not dry_run:
                    conn.execute(
                        f"UPDATE {table} SET user_id = ? WHERE user_id = ?",
                        (NEW_USER_ID, OLD_USER_ID),
                    )
            except sqlite3.OperationalError as e:
                print(f"  {table}: 跳过({e})")
        if not dry_run:
            conn.commit()
            print("  已提交")
    finally:
        conn.close()


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("=== DRY RUN(不实际执行)===\n")
    print(f"迁移身份: {OLD_USER_ID!r} → {NEW_USER_ID!r}\n")
    migrate_memory(dry_run)
    print()
    migrate_db(dry_run)
    print("\n完成。" + ("(dry-run,未实际改动)" if dry_run else ""))
    if not dry_run:
        print("重启后端后,get_user_id() 会返回 'me',记忆/会话归属一致。")


if __name__ == "__main__":
    main()
