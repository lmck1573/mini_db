"""storage 模块自定义异常体系。



继承关系：
    StorageError（基类）
       ├── DiskError   —— disk 层：文件 IO / 越界 / 长度错误
       ├── PageError   —— page 层：页分配 / 释放 / 元数据校验错误
       └── CacheError  —— cache 层：非法策略 / 容量 / 页数据长度错误
"""


class StorageError(Exception):
    """存储层异常基类。

    作用：作为所有存储层异常的公共父类，让上层可以用 `except StorageError` 统一兜底。
    """


class DiskError(StorageError):
    """磁盘 IO 层错误（文件损坏、读写越界、长度不符等）。"""


class PageError(StorageError):
    """页管理层错误（非法页号、元数据损坏、释放保留页等）。"""


class CacheError(StorageError):
    """缓存层错误（非法替换策略、页数据长度不符等）。"""
