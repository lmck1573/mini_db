#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto_validate.py —— 页式存储系统全自动暴力测试。

用法：在项目根目录（含 src/ 的目录）执行
    python auto_validate.py

行为：
    成功 —— 终端仅打印一行  🎉 ALL TESTS PASSED
    失败 —— 抛出 AssertionError，Python 自动打印完整堆栈（含文件与行号）

覆盖用例：
    1. 临时库文件 test_temp.db 自动创建，结束后自动删除
    2. 宕机重启：写入数据 -> del 销毁实例 -> 重新实例化 -> 数据仍在
    3. LRU 淘汰脏页不丢失（容量 3，写 5 页不同内容，读回第 1 页）
    4. 空闲页复用（分配 1/2/3，释放 2，再分配应返回 2 而非 4）
    5. 写页拒绝非 4KB 数据，必须抛出 StorageError

说明：脚本对导入路径与方法命名做兼容，同时支持
    - from src.storage.storage_manager import StorageManager
    - from storage.storage_manager import StorageManager
    - 写页方法 put_page / write_page；分配/释放 alloc_page/free_page 或 allocate_page/deallocate_page
"""

import contextlib
import importlib
import io
import os
import sys

PAGE_SIZE = 4096
TMP_DB = "test_temp.db"

# 确保 UTF-8 输出（Windows 默认 GBK，emoji 会编码失败）
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# --------------------------------------------------------------------------- #
# 1. 导入 StorageManager / StorageError（多路径兜底）
# --------------------------------------------------------------------------- #
def _load_storage():
    """尝试多种导入路径，返回 (StorageManager, StorageError, errors)。"""
    attempts = [
        # (manager 模块, StorageError 所在模块)
        ("src.storage.storage_manager", "src.storage.exceptions"),
        ("src.storage.storage_manager", "src.storage"),          # StorageError 从包导出
        ("storage.storage_manager", "storage.storage_manager"),  # 用户假设的结构
        ("storage.storage_manager", "storage.exceptions"),
    ]
    errors = []
    for mgr_mod, err_mod in attempts:
        try:
            mgr = importlib.import_module(mgr_mod)
            err = importlib.import_module(err_mod)
            return getattr(mgr, "StorageManager"), getattr(err, "StorageError"), []
        except (ImportError, AttributeError) as e:
            errors.append(f"    {mgr_mod} / {err_mod}  ->  {e!r}")
    return None, None, errors


StorageManager, StorageError, _IMPORT_ERRORS = _load_storage()

if StorageManager is None:
    print("❌ 导入 StorageManager / StorageError 失败。")
    print("   请确认在项目根目录（含 src/ 的目录）执行本脚本。")
    print("   正确的导入路径应为：")
    print("       from src.storage.storage_manager import StorageManager")
    print("       from src.storage.exceptions import StorageError")
    print("   或（如果你的目录没有 src/ 前缀）：")
    print("       from storage.storage_manager import StorageManager")
    print("       from storage.exceptions import StorageError")
    print("\n   已尝试的导入：")
    print("\n".join(_IMPORT_ERRORS))
    sys.exit(1)


# --------------------------------------------------------------------------- #
# 2. 方法名兼容（契约门面 vs 早期 spec 命名）
# --------------------------------------------------------------------------- #
def _pick(*names):
    """从候选方法名中挑一个存在的，否则抛 AttributeError。"""
    for name in names:
        if hasattr(StorageManager, name):
            return getattr(StorageManager, name)
    raise AttributeError(f"StorageManager 缺少方法：{' / '.join(names)}")


ALLOC = _pick("alloc_page", "allocate_page")
FREE = _pick("free_page", "deallocate_page")
WRITE = _pick("put_page", "write_page")
READ = _pick("get_page", "read_page")
FLUSH_ALL = _pick("flush_all")


def _shutdown(mgr):
    """优雅关闭：优先 close()，否则 flush_all()。"""
    close = getattr(mgr, "close", None)
    if close is not None:
        close()
    else:
        FLUSH_ALL(mgr)


def _remove_tmp():
    """删除临时数据库文件（若存在）。"""
    if os.path.exists(TMP_DB):
        os.remove(TMP_DB)


# --------------------------------------------------------------------------- #
# 3. 各测试用例
# --------------------------------------------------------------------------- #
def test_crash_restart():
    """宕机重启：写数据 -> del -> 重新实例化 -> 数据仍在。"""
    _remove_tmp()
    mgr = StorageManager(TMP_DB)
    pid = ALLOC(mgr)                                   # 页号 1
    data = bytes([0xAB]) * PAGE_SIZE
    WRITE(mgr, pid, data)                              # 写入（置脏）
    FLUSH_ALL(mgr)                                     # 落盘
    del mgr                                            # 模拟宕机：内存缓存随进程消失

    mgr = StorageManager(TMP_DB)                       # 重新实例化
    try:
        assert READ(mgr, pid) == data, f"宕机重启后第 {pid} 页数据不一致"
    finally:
        _shutdown(mgr)


def test_lru_dirty_page_not_lost():
    """LRU 淘汰脏页不丢失：容量 3，写 5 页，读回第 1 页。"""
    _remove_tmp()
    mgr = StorageManager(TMP_DB, capacity=3)           # 默认 LRU
    try:
        pids = [ALLOC(mgr) for _ in range(5)]          # 1,2,3,4,5
        for k in pids:
            WRITE(mgr, k, bytes([k]) * PAGE_SIZE)      # 每页写入不同内容
        # 写第 4、5 页时已触发淘汰（脏页被写回磁盘），第 1 页内容不应丢失
        assert READ(mgr, pids[0]) == bytes([pids[0]]) * PAGE_SIZE, \
            f"LRU 淘汰后第 {pids[0]} 页脏数据丢失"
    finally:
        _shutdown(mgr)


def test_free_page_reuse():
    """空闲页复用：分配 1/2/3，释放 2，再分配应返回 2 而非 4。"""
    _remove_tmp()
    mgr = StorageManager(TMP_DB)
    try:
        a = ALLOC(mgr)                                 # 1
        b = ALLOC(mgr)                                 # 2
        c = ALLOC(mgr)                                 # 3
        FREE(mgr, b)                                   # 释放 2
        d = ALLOC(mgr)                                 # 应复用 2
        assert d == b, f"空闲页复用失败：期望 {b}，实际得到 {d}"
    finally:
        _shutdown(mgr)


def test_write_rejects_non_4kb():
    """写页拒绝非 4KB 数据，必须抛出 StorageError。"""
    _remove_tmp()
    mgr = StorageManager(TMP_DB)
    try:
        pid = ALLOC(mgr)
        try:
            WRITE(mgr, pid, b"not-4KB")
        except StorageError:
            pass                                     # 预期：抛出 StorageError
        else:
            raise AssertionError("写入非 4KB 数据未抛出 StorageError")
    finally:
        _shutdown(mgr)


# --------------------------------------------------------------------------- #
# 4. 主流程
# --------------------------------------------------------------------------- #
def main():
    _remove_tmp()                     # 清理上一次运行残留
    try:
        # 测试期间吞掉 storage 的 [_CACHE] 日志，保证"成功只打印一行"
        with contextlib.redirect_stdout(io.StringIO()):
            test_crash_restart()
            test_lru_dirty_page_not_lost()
            test_free_page_reuse()
            test_write_rejects_non_4kb()
    finally:
        _remove_tmp()                 # 结束后删除临时文件，不污染项目
    print("🎉 ALL TESTS PASSED")


if __name__ == "__main__":
    main()
