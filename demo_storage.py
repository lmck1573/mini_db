#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""demo_storage.py —— 页式存储系统现场演示脚本（给老师看）

用法（在项目根目录，即含 src/ 的目录）：
    py demo_storage.py
    （Windows 控制台若中文乱码，先执行 chcp 65001 再跑，或用 test.bat 的方式）

脚本按顺序演示 5 件事，每件都只用到对外公开的 StorageManager 接口：
    1. 页分配 / 释放 / 空闲页复用（LIFO）
    2. 缓存命中统计（stats 的 hit_rate）
    3. LRU 与 FIFO 淘汰差异（对比 [_CACHE] 替换日志）
    4. 脏页淘汰自动回写（(dirty, flushed) 日志）
    5. 持久化：关闭再重启数据不丢

临时数据库文件建在 data/_demo_*.db，脚本结束自动清理。
"""

import os
import sys

# Windows 控制台默认 GBK，先强制 UTF-8 避免中文乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from src.storage import StorageManager, PAGE_SIZE

SEP = "=" * 66
SUB = "-" * 66


def make_page(seed: int) -> bytes:
    """生成一页确定性的、可区分的 4KB 数据（方便验证读回一致）。"""
    return bytes(((i * 31 + seed) % 256) for i in range(PAGE_SIZE))


def demo_db(name: str) -> str:
    """返回一个干净临时库文件路径（删掉上次残留）。"""
    path = os.path.join("data", f"_demo_{name}.db")
    if os.path.exists(path):
        os.remove(path)
    return path


def cleanup(*names: str) -> None:
    """删除演示产生的临时文件。"""
    for n in names:
        p = os.path.join("data", f"_demo_{n}.db")
        if os.path.exists(p):
            os.remove(p)


def section(title: str) -> None:
    print("\n" + SEP)
    print(title)
    print(SEP)


# --------------------------------------------------------------------------- #
section("演示 1/5：页分配 / 释放 / 空闲页复用（LIFO）")

m = StorageManager(demo_db("alloc"))
p1 = m.alloc_page()
p2 = m.alloc_page()
p3 = m.alloc_page()
print(f"连续分配三页      -> {p1}, {p2}, {p3}   （第 0 页是元数据页，数据页从 1 开始）")

m.free_page(p2)
print(f"释放第 {p2} 页后再次分配 -> {m.alloc_page()}   （优先复用空闲页，后进先出）")

m.free_page(p1)
m.free_page(p3)
print(f"再释放 {p1}、{p3} 两页，连续分配 -> {m.alloc_page()}, {m.alloc_page()}, {m.alloc_page()}"
      f"   （空闲链耗尽后继续追加新页）")
m.close()

# --------------------------------------------------------------------------- #
section("演示 2/5：缓存命中统计（stats）")

m = StorageManager(demo_db("hit"), capacity=4)
pid = m.alloc_page()
m.get_page(pid)   # 第一次：未命中 -> miss
m.get_page(pid)   # 第二次：命中   -> hit
print("stats() =", m.stats())
print("说明：hits=1, misses=1，hit_rate=0.5，容量 capacity=4，当前 size=1")
m.close()

# --------------------------------------------------------------------------- #
section("演示 3/5：LRU 与 FIFO 淘汰差异（同一访问序列）")

print("\n访问序列：依次读页 1,2,3（填满容量 3 的缓存）→ 再读一次页 1（touch）→ 读页 4 触发淘汰\n")

print(SUB)
print("[LRU]  命中时把页移到队尾，队首=最久未用，应淘汰页 2")
print(SUB)
m = StorageManager(demo_db("lru"), capacity=3, policy="LRU")
pids = [m.alloc_page() for _ in range(4)]     # 页号 1,2,3,4
m.get_page(pids[0]); m.get_page(pids[1]); m.get_page(pids[2])   # 填满缓存
m.get_page(pids[0])                            # 命中并 touch 页 1
m.get_page(pids[3])                            # miss -> 触发淘汰
m.close()

print(SUB)
print("[FIFO] 命中时不动顺序，队首=最早插入，应淘汰页 1")
print(SUB)
m = StorageManager(demo_db("fifo"), capacity=3, policy="FIFO")
pids = [m.alloc_page() for _ in range(4)]     # 页号 1,2,3,4
m.get_page(pids[0]); m.get_page(pids[1]); m.get_page(pids[2])   # 填满缓存
m.get_page(pids[0])                            # 命中但不改变顺序
m.get_page(pids[3])                            # miss -> 触发淘汰
m.close()

# --------------------------------------------------------------------------- #
section("演示 4/5：脏页淘汰自动回写（容量 1）")

m = StorageManager(demo_db("dirty"), capacity=1, policy="LRU")
pid1 = m.alloc_page()
m.put_page(pid1, make_page(3))   # 写入 -> 置脏，不立即落盘
print(f"已写入脏页 {pid1}（容量 1，缓存已满）")
pid2 = m.alloc_page()
m.get_page(pid2)                 # 触发淘汰脏页 pid1 -> 先写回磁盘
print("stats() =", m.stats())
print("说明：evictions=1 且 flushes>=1，说明脏页被淘汰前写回了磁盘（日志见 (dirty, flushed)）")
m.close()

# --------------------------------------------------------------------------- #
section("演示 5/5：持久化——关闭再重启数据不丢")

path = demo_db("persist")
m = StorageManager(path)
pid = m.alloc_page()
data = make_page(9)
m.put_page(pid, data)            # 置脏
m.flush_all()                    # 落盘
print(f"写入第 {pid} 页（内容 seed=9）并 flush_all 落盘")
m.close()
print("已关闭 StorageManager（内存缓存随进程销毁）……")

m2 = StorageManager(path)        # 重新打开同一文件
got = m2.get_page(pid)
print(f"重新打开后读第 {pid} 页 -> 数据一致：{got == data}")
print(f"页总数已恢复，继续分配得到新页号 -> {m2.alloc_page()}   （应为 {pid + 1}）")
m2.close()

print("\n" + SEP)
print("演示结束")
print(SEP)

cleanup("alloc", "hit", "lru", "fifo", "dirty", "persist")
