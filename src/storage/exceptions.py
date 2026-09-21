"""storage 模块自定义异常体系。

所有存储层异常均继承自 StorageError，engine 只需捕获 StorageError 即可兜底；
各层抛出更具体的子类，便于定位错误来源：

- DiskError  : disk 层文件 IO / 越界 / 长度错误
- PageError  : page 层页分配 / 释放 / 元数据校验错误
- CacheError : cache 层非法策略 / 容量 / 页数据长度错误
"""


class StorageError(Exception):
    """存储层异常基类。"""


class DiskError(StorageError):
    """磁盘 IO 层错误（文件损坏、读写越界、长度不符等）。"""


class PageError(StorageError):
    """页管理层错误（非法页号、元数据损坏、释放保留页等）。"""


class CacheError(StorageError):
    """缓存层错误（非法替换策略、页数据长度不符等）。"""
