"""行序列化与槽式页（Slotted Page）读写。

行格式（interface-contract 2.2）：
    FLAG(1B) | NCOLS(2B, 小端) | 逐列 TYPE(1B) LEN(4B, 小端) DATA(LEN B)
    FLAG bit0 = 删除标记；TYPE: 0=NULL 1=INT 2=FLOAT 3=STRING

页格式（interface-contract 2.3），页大小 4096：
    页头 16B：page_id(4) | free_offset(4) | slot_count(2) | next_page_id(4) | reserved(2)
    数据区从 16 正向增长，槽目录从页尾反向增长，槽项 = offset(2) + length(2)
"""

from __future__ import annotations

import struct

from src.storage import PAGE_SIZE

from ..errors import ExecutionError

# ---- 页头偏移（共 16 字节） ----
OFF_PAGE_ID = 0
OFF_FREE_OFFSET = 4
OFF_SLOT_COUNT = 8
OFF_NEXT_PAGE_ID = 10
OFF_RESERVED = 14
HEADER_SIZE = 16

SLOT_SIZE = 4            # 每条槽 offset(2) + length(2)
_NEXT_END = -1           # next_page_id 链尾标记

# ---- 行内类型码 ----
TYPE_NULL = 0
TYPE_INT = 1
TYPE_FLOAT = 2
TYPE_STRING = 3

_FLAG_DELETED = 0x01


# ----------------------------------------------------------------------
# 行序列化 / 反序列化
# ----------------------------------------------------------------------
def encode_row(values: list, columns: list) -> bytes:
    """把一行的值（按列定义顺序）序列化为字节流。"""
    parts = [bytes([0x00])]                       # FLAG：默认未删除
    parts.append(struct.pack("<H", len(values)))  # NCOLS
    for value, col in zip(values, columns):
        if value is None:
            parts.append(bytes([TYPE_NULL]))
            parts.append(struct.pack("<I", 0))
        elif col.type == "INT":
            parts.append(bytes([TYPE_INT]))
            parts.append(struct.pack("<I", 4))
            parts.append(struct.pack("<i", int(value)))
        elif col.type == "FLOAT":
            parts.append(bytes([TYPE_FLOAT]))
            parts.append(struct.pack("<I", 8))
            parts.append(struct.pack("<d", float(value)))
        else:  # VARCHAR / TEXT
            data = str(value).encode("utf-8")
            parts.append(bytes([TYPE_STRING]))
            parts.append(struct.pack("<I", len(data)))
            parts.append(data)
    return b"".join(parts)


def decode_row(data: bytes, columns: list) -> dict:
    """把字节流反序列化为 {列名: 值} 字典（键为 schema 的规范列名）。"""
    ncols = struct.unpack("<H", data[1:3])[0]
    if ncols != len(columns):
        raise ExecutionError(f"行列数 {ncols} 与表结构 {len(columns)} 不一致")
    pos = 3
    result = {}
    for col in columns:
        type_code = data[pos]
        pos += 1
        length = struct.unpack("<I", data[pos:pos + 4])[0]
        pos += 4
        if type_code == TYPE_NULL:
            value = None
        elif type_code == TYPE_INT:
            value = struct.unpack("<i", data[pos:pos + 4])[0]
            pos += 4
        elif type_code == TYPE_FLOAT:
            value = struct.unpack("<d", data[pos:pos + 8])[0]
            pos += 8
        elif type_code == TYPE_STRING:
            value = data[pos:pos + length].decode("utf-8")
            pos += length
        else:
            raise ExecutionError(f"未知的行类型码：{type_code}")
        result[col.name] = value
    return result


# ----------------------------------------------------------------------
# 槽式页读写
# ----------------------------------------------------------------------
def new_page(page_id: int) -> bytes:
    """构造一个空的槽式页（全零 + 页头初始化）。"""
    raw = bytearray(PAGE_SIZE)
    _write_header(raw, page_id, HEADER_SIZE, 0, _NEXT_END)
    return bytes(raw)


def slot_count(raw) -> int:
    """读取页内已使用的槽数量。"""
    return struct.unpack_from("<H", raw, OFF_SLOT_COUNT)[0]


def get_next_page_id(raw) -> int:
    """读取页链表的下一页码（-1 表示链尾）。"""
    return struct.unpack_from("<i", raw, OFF_NEXT_PAGE_ID)[0]


def set_next_page_id(raw: bytearray, page_id: int) -> None:
    """写入页链表的下一页码。"""
    struct.pack_into("<i", raw, OFF_NEXT_PAGE_ID, page_id)


def try_insert_record(raw: bytearray, record: bytes) -> int | None:
    """尝试把 record 写入页；成功返回槽号，空间不足返回 None。"""
    _, free_offset, count, _ = _read_header(raw)
    free_space = (PAGE_SIZE - count * SLOT_SIZE) - free_offset
    if len(record) + SLOT_SIZE > free_space:
        return None
    # 写记录到数据区
    raw[free_offset:free_offset + len(record)] = record
    # 写槽目录（从页尾反向增长）
    slot_pos = _slot_offset(count)
    struct.pack_into("<H", raw, slot_pos, free_offset)
    struct.pack_into("<H", raw, slot_pos + 2, len(record))
    # 更新页头
    struct.pack_into("<i", raw, OFF_FREE_OFFSET, free_offset + len(record))
    struct.pack_into("<H", raw, OFF_SLOT_COUNT, count + 1)
    return count


def get_record(raw, slot_index: int) -> bytes | None:
    """读取槽 i 的记录字节；槽未使用（length==0）返回 None。"""
    count = slot_count(raw)
    if slot_index >= count:
        return None
    slot_pos = _slot_offset(slot_index)
    offset = struct.unpack_from("<H", raw, slot_pos)[0]
    length = struct.unpack_from("<H", raw, slot_pos + 2)[0]
    if length == 0:
        return None
    return raw[offset:offset + length]


def mark_deleted(raw: bytearray, slot_index: int) -> None:
    """把槽 i 记录的 FLAG bit0 置 1（标记删除）。"""
    slot_pos = _slot_offset(slot_index)
    offset = struct.unpack_from("<H", raw, slot_pos)[0]
    raw[offset] |= _FLAG_DELETED


def is_deleted(record: bytes) -> bool:
    """判断记录是否已被标记删除。"""
    return bool(record[0] & _FLAG_DELETED)


# ----------------------------------------------------------------------
# 内部工具
# ----------------------------------------------------------------------
def _slot_offset(slot_index: int) -> int:
    """槽目录从页尾反向增长，返回槽 i 的起始字节位置。"""
    return PAGE_SIZE - SLOT_SIZE * (slot_index + 1)


def _read_header(raw) -> tuple[int, int, int, int]:
    """读取页头，返回 (page_id, free_offset, slot_count, next_page_id)。"""
    page_id = struct.unpack_from("<i", raw, OFF_PAGE_ID)[0]
    free_offset = struct.unpack_from("<i", raw, OFF_FREE_OFFSET)[0]
    count = struct.unpack_from("<H", raw, OFF_SLOT_COUNT)[0]
    next_page_id = struct.unpack_from("<i", raw, OFF_NEXT_PAGE_ID)[0]
    return page_id, free_offset, count, next_page_id


def _write_header(raw: bytearray, page_id: int, free_offset: int,
                  count: int, next_page_id: int) -> None:
    struct.pack_into("<i", raw, OFF_PAGE_ID, page_id)
    struct.pack_into("<i", raw, OFF_FREE_OFFSET, free_offset)
    struct.pack_into("<H", raw, OFF_SLOT_COUNT, count)
    struct.pack_into("<i", raw, OFF_NEXT_PAGE_ID, next_page_id)
