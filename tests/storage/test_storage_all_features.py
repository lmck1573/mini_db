"""storage 模块全部功能展示性测试（预设测试程序）。

本文件提供 9 个维度的组织性测试，用单元测试的形式覆盖页式存储系统，
与评分清单一一对应：

  1. Page 页结构         —— TestPageStructure       页大小 / 元数据页 / 魔数
  2. BufferPool 缓冲池   —— TestBufferPool          命中统计 / 容量不超限
  3. 页获取与释放        —— TestPageGetRelease      fetch/new/delete/复用
  4. LRU 替换            —— TestLRU                 touch 顺序 / victim 选取
  5. FIFO 替换与切换     —— TestFIFO                淘汰顺序 / set_policy 切换
  6. 脏页写回            —— TestDirtyWriteback      脏标记 / flush / 淘汰自动写回
  7. 持久化              —— TestPersistence         数据恢复 / 分配计数 / 空闲状态
  8. 数据访问接口        —— TestDataAccessInterface StorageManager 门面全覆盖
  9. 异常边界与淘汰失败  —— TestErrorsBoundary      越界 / 长度 / 释放元数据页 / 空缓存淘汰

运行方式（在项目根目录）：
    python -m unittest discover -s tests -t . -p "test_storage_all_features.py" -v
"""

from __future__ import annotations

import contextlib
import io
import os
import unittest

from src.storage import (
    CacheError,
    CacheManager,
    DiskError,
    DiskManager,
    PageError,
    PageManager,
    StorageError,
    StorageManager,
    PAGE_SIZE,
)

_TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP_DIR = os.path.join(_TESTS_DIR, "tmp")


def make_page(seed: int) -> bytes:
    """生成一页确定性的、可区分值的 4096 字节数据。"""
    return bytes(((i * 31 + seed) % 256) for i in range(PAGE_SIZE))


class AllFeaturesTestCase(unittest.TestCase):
    """公共脚手架：每个用例一个独立临时文件，统一追踪并关闭。"""

    def setUp(self):
        os.makedirs(TMP_DIR, exist_ok=True)
        self.db_path = os.path.join(TMP_DIR, f"feat_{self._testMethodName}.db")
        self._extra_mgrs = []
        self.mgr = StorageManager(self.db_path, capacity=10, policy="LRU")

    def new_manager(self, capacity, policy, suffix=""):
        """在独立临时文件上新建管理器，自动追踪并关闭。"""
        path = os.path.join(TMP_DIR, f"feat_{self._testMethodName}_{suffix}.db")
        mgr = StorageManager(path, capacity=capacity, policy=policy)
        self._extra_mgrs.append(mgr)
        return mgr

    def tearDown(self):
        for mgr in [getattr(self, "mgr", None)] + self._extra_mgrs:
            if mgr is not None:
                mgr.close()
        for f in os.listdir(TMP_DIR):
            if f.startswith(f"feat_{self._testMethodName}"):
                try:
                    os.remove(os.path.join(TMP_DIR, f))
                except OSError:
                    pass

    @staticmethod
    def capture(fn) -> str:
        """运行 fn 并捕获其 stdout（用于断言 [_CACHE] 替换日志）。"""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn()
        return buf.getvalue()


class TestPageStructure(AllFeaturesTestCase):
    def test_page_size_is_4096(self):
        """页大小固定 4KB。"""
        self.assertEqual(PAGE_SIZE, 4096)

    def test_page_zero_is_metadata_with_magic(self):
        """第 0 页是元数据页，头部魔数 b"MDB1" 开头。"""
        raw = self.mgr.get_page(0)
        self.assertEqual(len(raw), PAGE_SIZE)
        self.assertEqual(raw[0:4], b"MDB1")

    def test_first_data_page_id_is_one(self):
        """第 0 页被元数据占用，首个数据页页号应为 1。"""
        self.assertEqual(self.mgr.alloc_page(), 1)

    def test_page_content_is_fixed_length(self):
        """任何读到的页数据都是定长 PAGE_SIZE 字节。"""
        pid = self.mgr.alloc_page()
        self.assertEqual(len(self.mgr.get_page(pid)), PAGE_SIZE)


class TestBufferPool(AllFeaturesTestCase):
    def test_capacity_is_reported(self):
        """stats 中的 capacity 与构造参数一致。"""
        self.assertEqual(self.mgr.stats()["capacity"], 10)

    def test_size_never_exceeds_capacity(self):
        """无论访问多少页，缓存大小始终不超过 capacity。"""
        mgr = self.new_manager(3, "LRU", "cap")
        pids = [mgr.alloc_page() for _ in range(6)]
        for p in pids:
            mgr.get_page(p)
        self.assertLessEqual(mgr.stats()["size"], 3)

    def test_hit_and_miss_counts(self):
        """首次读 miss，再次读 hit，命中统计正确。"""
        pid = self.mgr.alloc_page()
        self.mgr.get_page(pid)
        self.mgr.get_page(pid)
        stats = self.mgr.stats()
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 1)
        self.assertEqual(stats["hit_rate"], 0.5)


class TestPageGetRelease(AllFeaturesTestCase):
    def test_get_put_roundtrip(self):
        """写入一页再读出，内容一致。"""
        pid = self.mgr.alloc_page()
        data = make_page(1)
        self.mgr.put_page(pid, data)
        self.assertEqual(self.mgr.get_page(pid), data)

    def test_free_then_realloc_reuses_page(self):
        """释放页后重新分配优先复用该空闲页（DeletePage -> NewPage）。"""
        pid1 = self.mgr.alloc_page()
        pid2 = self.mgr.alloc_page()
        self.mgr.free_page(pid2)
        self.assertEqual(self.mgr.alloc_page(), pid2)

    def test_multiple_free_reuse_is_lifo(self):
        """多次释放按链表头后进先出复用，最后分配新页。"""
        pids = [self.mgr.alloc_page() for _ in range(3)]
        self.mgr.free_page(pids[2])
        self.mgr.free_page(pids[1])
        self.assertEqual(self.mgr.alloc_page(), pids[1])
        self.assertEqual(self.mgr.alloc_page(), pids[2])
        self.assertEqual(self.mgr.alloc_page(), 4)

    def test_reused_page_is_zeroed(self):
        """复用的空闲页应为全零（旧数据被清掉）。

        脏页延迟回写加释放防污染
        写页只标脏不落盘，把 IO 推迟到淘汰、flush 或关闭这三个时机。并且释放页之前会先把缓存里那份旧副本
         invalidate 掉，防止它被当成有效数据回写、污染复用后的页，保证复用出来的页是干净的全零。这对应
         test_reused_page_is_zeroed 测试。脏页淘汰前必须先写回，否则内存副本没了，修改就丢了，所以
         evict 里遇到脏页会先 write_page 再删除，并打印 (dirty, flushed) 日志。

        """
        pid = self.mgr.alloc_page()
        self.mgr.put_page(pid, make_page(7))
        self.mgr.flush_all()
        self.mgr.free_page(pid)
        pid2 = self.mgr.alloc_page()
        self.assertEqual(pid2, pid)
        self.assertEqual(self.mgr.get_page(pid2), b"\x00" * PAGE_SIZE)


class TestLRU(AllFeaturesTestCase):
    def test_lru_evicts_least_recently_used(self):
        """LRU：touch 页 1 后，最久未用的是页 2，应淘汰页 2。"""
        mgr = self.new_manager(3, "LRU", "lru")
        pids = [mgr.alloc_page() for _ in range(4)]
        for p in pids[:3]:
            mgr.get_page(p)
        mgr.get_page(pids[0])
        log = self.capture(lambda: mgr.get_page(pids[3]))
        self.assertIn("EVICT page=2", log)
        self.assertEqual(mgr.stats()["evictions"], 1)


class TestFIFO(AllFeaturesTestCase):
    def test_fifo_evicts_first_inserted(self):
        """FIFO：命中不改变顺序，最早插入的页 1 被淘汰。"""
        mgr = self.new_manager(3, "FIFO", "fifo")
        pids = [mgr.alloc_page() for _ in range(4)]
        for p in pids[:3]:
            mgr.get_page(p)
        mgr.get_page(pids[0])
        log = self.capture(lambda: mgr.get_page(pids[3]))
        self.assertIn("EVICT page=1", log)

    def test_lru_and_fifo_evict_different_pages(self):
        """同一访问序列下 LRU 与 FIFO 淘汰不同页，证明策略确实生效。"""
        _, _, log_lru = self._run_sequence("LRU", "diff_lru")
        _, _, log_fifo = self._run_sequence("FIFO", "diff_fifo")
        self.assertIn("EVICT page=2", log_lru)
        self.assertIn("EVICT page=1", log_fifo)

    def test_set_policy_is_case_insensitive(self):
        """set_policy 大小写不敏感，且不破坏已有状态。"""
        self.mgr.set_policy("fifo")
        self.mgr.set_policy("LRU")
        self.assertEqual(self.mgr.stats()["capacity"], 10)

    def _run_sequence(self, policy, suffix):
        """构造固定访问序列，返回 (manager, pids, 触发淘汰时的日志)。"""
        mgr = self.new_manager(3, policy, suffix)
        pids = [mgr.alloc_page() for _ in range(4)]
        for p in pids[:3]:
            mgr.get_page(p)
        mgr.get_page(pids[0])
        log = self.capture(lambda: mgr.get_page(pids[3]))
        return mgr, pids, log


class TestDirtyWriteback(AllFeaturesTestCase):
    def test_put_marks_dirty_without_flushing(self):
        """写页只置脏不落盘，flush_all 前 flushes 计数为 0。"""
        mgr = self.new_manager(4, "LRU", "dirty1")
        pid = mgr.alloc_page()
        mgr.put_page(pid, make_page(5))
        self.assertEqual(mgr.stats()["flushes"], 0)
        mgr.flush_all()
        self.assertEqual(mgr.stats()["flushes"], 1)

    def test_flush_page_single(self):
        """flush_page 只写回指定页。"""
        mgr = self.new_manager(4, "LRU", "dirty2")
        pid1 = mgr.alloc_page()
        pid2 = mgr.alloc_page()
        mgr.put_page(pid1, make_page(1))
        mgr.put_page(pid2, make_page(2))
        mgr.flush_page(pid1)
        self.assertEqual(mgr.stats()["flushes"], 1)
        mgr.flush_page(pid2)
        self.assertEqual(mgr.stats()["flushes"], 2)

    def test_evict_dirty_page_flushes_before_eviction(self):
        """淘汰脏页时先写回，日志带 (dirty, flushed) 标记。"""
        mgr = self.new_manager(1, "LRU", "dirty3")
        pid1 = mgr.alloc_page()
        mgr.put_page(pid1, make_page(3))
        pid2 = mgr.alloc_page()
        log = self.capture(lambda: mgr.get_page(pid2))
        self.assertIn(f"EVICT page={pid1}", log)
        self.assertIn("(dirty, flushed)", log)
        self.assertEqual(mgr.stats()["evictions"], 1)


class TestPersistence(AllFeaturesTestCase):
    def test_restart_recovers_data(self):
        """关闭再重启，页数据持久不丢。"""
        pid = self.mgr.alloc_page()
        data = make_page(9)
        self.mgr.put_page(pid, data)
        self.mgr.flush_all()
        self.mgr.close()

        mgr2 = StorageManager(self.db_path)
        try:
            self.assertEqual(mgr2.get_page(pid), data)
        finally:
            mgr2.close()

    def test_restart_recovers_allocation_state(self):
        """重启后页分配计数恢复，继续分配得到 pid+1 而非从头。"""
        pid = self.mgr.alloc_page()
        self.mgr.close()

        mgr2 = StorageManager(self.db_path)
        try:
            self.assertEqual(mgr2.alloc_page(), pid + 1)
        finally:
            mgr2.close()

    def test_restart_recovers_free_list(self):
        """重启后空闲页链表恢复，释放状态持久化。"""
        pid1 = self.mgr.alloc_page()
        pid2 = self.mgr.alloc_page()
        self.mgr.free_page(pid2)
        self.mgr.close()

        mgr2 = StorageManager(self.db_path)
        try:
            self.assertEqual(mgr2.alloc_page(), pid2)
        finally:
            mgr2.close()


class TestDataAccessInterface(AllFeaturesTestCase):
    def test_full_workflow_via_facade(self):
        """只经 StorageManager 门面接口：分配->写->读->释放->复用->统计->flush->关闭。"""
        m = self.mgr
        pid1 = m.alloc_page()
        m.put_page(pid1, make_page(11))
        self.assertEqual(m.get_page(pid1), make_page(11))
        m.free_page(pid1)
        pid2 = m.alloc_page()
        self.assertEqual(pid2, pid1)
        stats = m.stats()
        for key in ("hits", "misses", "hit_rate", "evictions", "flushes", "capacity", "size"):
            self.assertIn(key, stats)
        m.flush_all()
        m.close()

    def test_facade_methods_exist(self):
        """StorageManager 提供以下基本接口（页管理）。"""
        for name in ("get_page", "put_page", "alloc_page", "free_page", "flush_page", "flush_all", "set_policy", "stats", "close"):
            self.assertTrue(callable(getattr(self.mgr, name)), name)


class TestErrorsBoundary(AllFeaturesTestCase):
    def test_read_out_of_range_raises(self):
        """读取越界页号抛 DiskError。"""
        with self.assertRaises(DiskError):
            self.mgr.get_page(9999)

    def test_put_wrong_length_raises(self):
        """写入长度不等于 PAGE_SIZE 抛 CacheError。"""
        pid = self.mgr.alloc_page()
        with self.assertRaises(CacheError):
            self.mgr.put_page(pid, b"short")

    def test_free_metadata_page_raises(self):
        """释放第 0 页（元数据页）抛 PageError。"""
        with self.assertRaises(PageError):
            self.mgr.free_page(0)

    def test_free_out_of_range_raises(self):
        """释放越界页号抛 PageError。"""
        with self.assertRaises(PageError):
            self.mgr.free_page(9999)

    def test_invalid_policy_raises(self):
        """非法替换策略抛 CacheError。"""
        with self.assertRaises(CacheError):
            StorageManager(self.db_path, policy="WRONG")

    def test_evict_empty_cache_raises(self):
        """缓存为空时淘汰抛 CacheError（淘汰失败路径）。"""
        path = os.path.join(TMP_DIR, f"feat_evictempty_{id(self)}.db")
        disk = DiskManager(path)
        try:
            pm = PageManager(disk)
            cm = CacheManager(pm, capacity=2)
            with self.assertRaises(CacheError):
                cm.evict()
        finally:
            disk.close()
            try:
                os.remove(path)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
