"""storage 模块单元测试。

覆盖（tests/AGENTS.md storage 章节 + 指导书要求）：
- 页分配 / 释放 / 复用；页读写往返
- 缓存命中 / 未命中统计
- LRU 与 FIFO 替换顺序差异（通过 [_CACHE] 替换日志验证）
- 释放后重新分配优先复用空闲页
- 关闭再重启后数据与页分配状态持久化
- 错误路径：越界读写、页数据长度不符、释放元数据页、非法策略

临时数据库文件建在 tests/tmp/（gitignore），测试结束清理。
"""

import contextlib
import io
import os
import unittest

from src.storage import (
    CacheError,
    DiskError,
    PageError,
    StorageError,
    StorageManager,
    PAGE_SIZE,
)

_TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP_DIR = os.path.join(_TESTS_DIR, "tmp")


def make_page(seed: int) -> bytes:
    """生成一个确定性、可区分的 4096 字节页。"""
    return bytes(((i * 31 + seed) % 256) for i in range(PAGE_SIZE))


class StorageTestCase(unittest.TestCase):
    """公共脚手架：每个用例一个独立临时库文件；用例内新建的管理器统一追踪并关闭。"""

    def setUp(self):
        os.makedirs(TMP_DIR, exist_ok=True)
        self.db_path = os.path.join(TMP_DIR, f"test_{self._testMethodName}.db")
        self._extra_mgrs = []
        self.mgr = self.make_manager()

    def make_manager(self, capacity=10, policy="LRU"):
        return StorageManager(self.db_path, capacity=capacity, policy=policy)

    def new_manager(self, capacity, policy, suffix=""):
        """在独立临时文件上新建管理器，自动追踪并关闭，避免与 self.mgr 共享文件。"""
        path = os.path.join(TMP_DIR, f"test_{self._testMethodName}_{suffix}.db")
        mgr = StorageManager(path, capacity=capacity, policy=policy)
        self._extra_mgrs.append(mgr)
        return mgr

    def tearDown(self):
        for mgr in [getattr(self, "mgr", None)] + self._extra_mgrs:
            if mgr is not None:
                mgr.close()
        for f in os.listdir(TMP_DIR):
            if f.startswith(f"test_{self._testMethodName}"):
                try:
                    os.remove(os.path.join(TMP_DIR, f))
                except OSError:
                    pass

    @staticmethod
    def capture(fn) -> str:
        """运行 fn 并捕获其 stdout（用于断言 [_CACHE] 日志）。"""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn()
        return buf.getvalue()


class TestPageManagement(StorageTestCase):
    def test_allocate_and_read_write_roundtrip(self):
        """分配新页并写入数据，读取验证一致性。"""
        pid = self.mgr.alloc_page()
        data = make_page(1)
        self.mgr.put_page(pid, data)
        self.assertEqual(self.mgr.get_page(pid), data)

    def test_first_allocated_page_is_one(self):
        """第 0 页为元数据页，首个数据页页号应为 1。"""
        self.assertEqual(self.mgr.alloc_page(), 1)

    def test_free_then_realloc_reuses_page(self):
        """释放页后重新分配应优先复用该空闲页。"""
        pid1 = self.mgr.alloc_page()
        pid2 = self.mgr.alloc_page()
        self.mgr.free_page(pid2)
        self.assertEqual(self.mgr.alloc_page(), pid2)  # 复用 pid2

    def test_multiple_free_reuse_is_lifo(self):
        """多次释放按链表头后进先出复用。"""
        pids = [self.mgr.alloc_page() for _ in range(3)]  # 1,2,3
        self.mgr.free_page(pids[2])
        self.mgr.free_page(pids[1])
        # 链表头是最近释放的 pids[1]
        self.assertEqual(self.mgr.alloc_page(), pids[1])
        self.assertEqual(self.mgr.alloc_page(), pids[2])
        # 空闲链表耗尽后分配新页
        self.assertEqual(self.mgr.alloc_page(), 4)

    def test_freed_page_is_zeroed_on_reuse(self):
        """复用的空闲页应为全零（旧数据被清掉）。"""
        pid = self.mgr.alloc_page()
        self.mgr.put_page(pid, make_page(7))
        self.mgr.flush_all()
        self.mgr.free_page(pid)
        pid2 = self.mgr.alloc_page()
        self.assertEqual(pid2, pid)
        # free 时已 invalidate 缓存，读回应为全零
        self.assertEqual(self.mgr.get_page(pid2), b"\x00" * PAGE_SIZE)


class TestCacheStatistics(StorageTestCase):
    def test_hit_and_miss_counts(self):
        pid = self.mgr.alloc_page()
        self.mgr.get_page(pid)   # miss
        self.mgr.get_page(pid)   # hit
        stats = self.mgr.stats()
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 1)
        self.assertEqual(stats["hit_rate"], 0.5)

    def test_stats_contains_required_keys(self):
        self.mgr.get_page(self.mgr.alloc_page())
        keys = {"hits", "misses", "hit_rate", "evictions", "flushes", "capacity", "size"}
        self.assertTrue(keys.issubset(self.mgr.stats().keys()))


class TestEvictionPolicy(StorageTestCase):
    def _fill_and_touch(self, capacity, policy, suffix):
        """在独立文件上构造访问序列并返回 (manager, 页号列表, 触发淘汰时的日志)。"""
        mgr = self.new_manager(capacity, policy, suffix=suffix)
        pids = [mgr.alloc_page() for _ in range(4)]  # 1,2,3,4
        for pid in pids[:3]:
            mgr.get_page(pid)          # 填满缓存
        mgr.get_page(pids[0])          # 命中并 touch 页 1
        log = self.capture(lambda: mgr.get_page(pids[3]))  # miss -> 触发淘汰
        return mgr, pids, log

    def test_lru_evicts_least_recently_used(self):
        """LRU：touch 页 1 后，最久未用的是页 2，应淘汰页 2。"""
        mgr, pids, log = self._fill_and_touch(3, "LRU", "lru")
        self.assertIn("EVICT page=2", log)
        self.assertEqual(mgr.stats()["evictions"], 1)

    def test_fifo_evicts_first_inserted(self):
        """FIFO：命中不改变顺序，最早插入的页 1 被淘汰。"""
        mgr, pids, log = self._fill_and_touch(3, "FIFO", "fifo")
        self.assertIn("EVICT page=1", log)
        self.assertEqual(mgr.stats()["evictions"], 1)

    def test_lru_and_fifo_evict_different_pages(self):
        """两种策略在同一访问序列下淘汰不同页，验证策略确实生效。"""
        _, _, log_lru = self._fill_and_touch(3, "LRU", "lru")
        _, _, log_fifo = self._fill_and_touch(3, "FIFO", "fifo")
        self.assertIn("EVICT page=2", log_lru)
        self.assertIn("EVICT page=1", log_fifo)

    def test_evict_dirty_page_flushes(self):
        """淘汰脏页时日志应带 (dirty, flushed) 标记。"""
        mgr = self.new_manager(capacity=1, policy="LRU", suffix="dirty")
        pid1 = mgr.alloc_page()
        mgr.put_page(pid1, make_page(3))  # 脏页
        pid2 = mgr.alloc_page()
        log = self.capture(lambda: mgr.get_page(pid2))  # 触发淘汰脏页 pid1
        self.assertIn("(dirty, flushed)", log)
        self.assertIn(f"EVICT page={pid1}", log)

    def test_set_policy_accepts_case_insensitive(self):
        """set_policy 大小写不敏感，且不破坏已有状态。"""
        self.mgr.set_policy("fifo")
        self.mgr.set_policy("LRU")
        self.assertEqual(self.mgr.stats()["capacity"], 10)


class TestPersistence(StorageTestCase):
    def test_restart_recovers_data_and_allocation_state(self):
        """关闭 StorageManager 再重新实例化，数据与页分配状态应恢复。"""
        pid = self.mgr.alloc_page()
        data = make_page(9)
        self.mgr.put_page(pid, data)
        self.mgr.flush_all()
        self.mgr.close()

        mgr2 = StorageManager(self.db_path)
        try:
            self.assertEqual(mgr2.get_page(pid), data)
            # 未释放 pid，继续分配应得到新页号 pid+1（页总数已恢复）
            self.assertEqual(mgr2.alloc_page(), pid + 1)
        finally:
            mgr2.close()

    def test_restart_recovers_free_list(self):
        """重启后空闲页链表应恢复（释放状态持久化）。"""
        pid1 = self.mgr.alloc_page()
        pid2 = self.mgr.alloc_page()
        self.mgr.free_page(pid2)
        self.mgr.close()

        mgr2 = StorageManager(self.db_path)
        try:
            # 重启后复用释放的 pid2
            self.assertEqual(mgr2.alloc_page(), pid2)
        finally:
            mgr2.close()


class TestErrorPaths(StorageTestCase):
    def test_read_out_of_range_raises(self):
        with self.assertRaises(DiskError):
            self.mgr.get_page(9999)

    def test_put_wrong_length_raises(self):
        pid = self.mgr.alloc_page()
        with self.assertRaises(StorageError):
            self.mgr.put_page(pid, b"short")

    def test_free_metadata_page_raises(self):
        with self.assertRaises(PageError):
            self.mgr.free_page(0)

    def test_free_out_of_range_raises(self):
        with self.assertRaises(PageError):
            self.mgr.free_page(9999)

    def test_invalid_policy_raises(self):
        with self.assertRaises(CacheError):
            StorageManager(self.db_path, policy="WRONG")


if __name__ == "__main__":
    unittest.main()
