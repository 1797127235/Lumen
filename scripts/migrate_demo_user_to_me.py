"""一次性迁移：把单用户身份从 demo_user 迁到 'me'。

用途：身份统一后，把现有真实数据的 user_id 从 'demo_user' 改成 'me'。
迁移内容:
  1. 记忆目录：~/.lumen/memory/demo_user/  →  ~/.lumen/memory/me/
  2. DB：lumen.db 中 conversations/lumen_state/lumen_thoughts/data_sources/external_items
      表的 user_id 'demo_user' → 'me'

使用(必须先停后端):
  python scripts/migrate_demo_user_to_me.py
  python scripts/migrate_demo_user_to_me.py --dry-run   # 只打印不执行

安全:
  - 幂等。目录目标已存在则跳过(避免覆盖)。
  - 不迁移测试数据(test_*/eval_*/cache_*/freeze_*/bench_*)。
  - lumen_state 主键是 user_id，若 'me' 已存在则删除 'demo_user' 行，避免主键冲突。
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

OLD_USER_ID = "demo_user"
NEW_USER_ID = "me"

LUMEN_HOME = Path(os.environ.get("LUMEN_HOME", str(Path.home() / ".lumen")))
MEM_DIR = LUMEN_HOME / "memory"
DB_PATH = Path("lumen.db")  # 项目根的 lumen.db（DATABASE_URL 默认相对路径）

TEST_PREFIXES = ("test_", "eval_", "cache_", "freeze_", "bench_")

# 需要迁移 user_id 的表。注意 messages 表无 user_id 列，按 conversation_id 间接归属。
USER_ID_TABLES = ["conversations", "lumen_thoughts", "data_sources", "external_items"]


def is_test(uid: str) -> bool:
    return any(uid.startswith(p) for p in TEST_PREFIXES)


def migrate_memory(dry_run: bool) -> None:
    src = MEM_DIR / OLD_USER_ID
    dst = MEM_DIR / NEW_USER_ID
    print(f"[memory] {src}")
    if not src.exists():
        print("  源目录不存在，跳过")
        return
    if dst.exists():
        print(f"  目标 {dst} 已存在，跳过(避免覆盖)")
        return
    print(f"  → {dst}")
    if not dry_run:
        src.rename(dst)


def _migrate_simple_table(conn: sqlite3.Connection, table: str, dry_run: bool) -> None:
    try:
        cur = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (OLD_USER_ID,))
        count = cur.fetchone()[0]
        if count == 0:
            print(f"  {table}: 无 user_id={OLD_USER_ID!r} 的行，跳过")
            return
        print(f"  {table}: 迁移 {count} 行  user_id {OLD_USER_ID!r} → {NEW_USER_ID!r}")
        if not dry_run:
            conn.execute(
                f"UPDATE {table} SET user_id = ? WHERE user_id = ?",
                (NEW_USER_ID, OLD_USER_ID),
            )
    except sqlite3.OperationalError as e:
        print(f"  {table}: 跳过({e})")


def _migrate_lumen_state(conn: sqlite3.Connection, dry_run: bool) -> None:
    """lumen_state 主键是 user_id，需要处理 'me' 已存在的情况。"""
    try:
        cur = conn.execute("SELECT COUNT(*) FROM lumen_state WHERE user_id = ?", (OLD_USER_ID,))
        old_count = cur.fetchone()[0]
        if old_count == 0:
            print("  lumen_state: 无 user_id=demo_user 的行，跳过")
            return

        cur = conn.execute("SELECT COUNT(*) FROM lumen_state WHERE user_id = ?", (NEW_USER_ID,))
        new_exists = cur.fetchone()[0] > 0

        if new_exists:
            print(f"  lumen_state: 'me' 已存在，删除 {old_count} 行 demo_user 记录(避免主键冲突)")
            if not dry_run:
                conn.execute("DELETE FROM lumen_state WHERE user_id = ?", (OLD_USER_ID,))
        else:
            print(f"  lumen_state: 迁移 {old_count} 行  user_id {OLD_USER_ID!r} → {NEW_USER_ID!r}")
            if not dry_run:
                conn.execute(
                    "UPDATE lumen_state SET user_id = ? WHERE user_id = ?",
                    (NEW_USER_ID, OLD_USER_ID),
                )
    except sqlite3.OperationalError as e:
        print(f"  lumen_state: 跳过({e})")


def migrate_db(dry_run: bool) -> None:
    if not DB_PATH.exists():
        print(f"[db] {DB_PATH} 不存在，跳过")
        return
    print(f"[db] {DB_PATH.absolute()}")
    conn = sqlite3.connect(str(DB_PATH))
    try:
        for table in USER_ID_TABLES:
            _migrate_simple_table(conn, table, dry_run)
        _migrate_lumen_state(conn, dry_run)

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
        print("重启后端后，全系统身份统一为 'me'，记忆/会话归属一致。")


if __name__ == "__main__":
    main()
