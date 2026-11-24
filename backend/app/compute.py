"""通用计算引擎：跨模块复用的纯计算函数，不依赖任何外部框架。"""
from __future__ import annotations

import hashlib
import math
import time
from difflib import SequenceMatcher
from typing import Any


def content_hash(content: str | bytes, algorithm: str = "sha256") -> str:
    """对内容计算哈希摘要。"""
    if isinstance(content, str):
        content = content.encode("utf-8")
    h = hashlib.new(algorithm)
    h.update(content)
    return h.hexdigest()


def hash_dedup(
    items: list[dict[str, Any]],
    key_fields: list[str],
    algorithm: str = "sha256",
) -> tuple[list[dict[str, Any]], int]:
    """基于内容哈希对数据行去重，返回去重结果和去重数。"""
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        parts = [str(item.get(f, "")) for f in key_fields]
        h = content_hash("|".join(parts), algorithm)
        if h not in seen:
            seen.add(h)
            result.append(item)
    return result, len(items) - len(result)


def text_similarity(a: str, b: str) -> float:
    """计算两段文本的相似度，返回值 0.0~1.0。"""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def rate_limit_check(
    request_count: int,
    window_seconds: float,
    max_per_second: float,
    now: float | None = None,
) -> tuple[bool, float]:
    """限速检查：返回 (是否通过, 建议等待秒数)。"""
    if now is None:
        now = time.time()
    if window_seconds <= 0 or max_per_second <= 0:
        return True, 0.0
    max_in_window = max_per_second * window_seconds
    if request_count <= max_in_window:
        return True, 0.0
    wait = (request_count - max_in_window) / max_per_second
    return False, max(wait, 0.0)


def batch_partition(
    items: list[Any],
    batch_size: int,
    min_batches: int = 0,
) -> list[list[Any]]:
    """将数据行按指定大小切片，并保证最小切片数。"""
    if not items:
        return []
    if len(items) <= min_batches * batch_size:
        actual_size = max(1, math.ceil(len(items) / max(min_batches, 1)))
    else:
        actual_size = max(1, batch_size)
    partitions: list[list[Any]] = []
    for i in range(0, len(items), actual_size):
        partitions.append(items[i:i + actual_size])
    if len(partitions) < min_batches and partitions:
        while len(partitions) < min_batches:
            partitions.append([])
    return partitions


def page_numbers(page_start: int | None = None, page_end: int | None = None,
                 pages: list[int] | tuple[int, ...] | None = None) -> list[int]:
    """把页码区间或页码列表归一化成升序去重的整数页码列表（含首尾）。"""
    if pages is not None:
        nums = [int(p) for p in pages]
    else:
        start = int(page_start if page_start is not None else 1)
        end = int(page_end if page_end is not None else start)
        if end < start:
            start, end = end, start
        nums = list(range(start, end + 1))
    return sorted(set(nums))


def build_page_shards(
    page_start: int | None = None,
    page_end: int | None = None,
    pages: list[int] | tuple[int, ...] | None = None,
    shard_size: int | None = None,
    shard_count: int | None = None,
    shards: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """把来源分片计划切成分片，返回 1 起始序号、真实分页范围与页码。

    支持三种指定方式（优先级：显式分片 > 每片页数 > 分片总数）：
    - shards=[{"page_start": 1, "page_end": 2}, ...] 或 [[1, 2], [3, 4]]
    - shard_size=N：每 N 页一片，末片可不足
    - shard_count=M：尽量均匀地切成 M 片
    """
    if shards:
        result: list[dict[str, Any]] = []
        for raw in shards:
            if isinstance(raw, dict):
                ps = raw.get("pages")
                if ps:
                    pgs = page_numbers(pages=list(ps))
                else:
                    pgs = page_numbers(
                        raw.get("page_start", raw.get("start_page", raw.get("起始页"))),
                        raw.get("page_end", raw.get("end_page", raw.get("结束页"))),
                    )
            elif isinstance(raw, (list, tuple)):
                if len(raw) == 1:
                    pgs = page_numbers(pages=[raw[0]])
                else:
                    pgs = page_numbers(int(raw[0]), int(raw[1]))
            else:
                pgs = page_numbers(pages=[raw])
            if pgs:
                result.append({
                    "shard_index": len(result) + 1,
                    "shard_no": len(result) + 1,
                    "index": len(result) + 1,
                    "page_start": pgs[0],
                    "page_end": pgs[-1],
                    "pages": pgs,
                    "page_range": f"{pgs[0]}-{pgs[-1]}" if len(pgs) > 1 else f"{pgs[0]}",
                })
        return result

    nums = page_numbers(page_start, page_end, pages)
    if not nums:
        return []
    if shard_size and int(shard_size) > 0:
        chunks = [nums[i:i + int(shard_size)] for i in range(0, len(nums), int(shard_size))]
    elif shard_count and int(shard_count) > 0:
        count = min(max(int(shard_count), 1), len(nums))
        size = math.ceil(len(nums) / count)
        chunks = [nums[i:i + size] for i in range(0, len(nums), size)]
    else:
        chunks = [nums]
    return [{
        "shard_index": i + 1,
        "shard_no": i + 1,
        "index": i + 1,
        "page_start": chunk[0],
        "page_end": chunk[-1],
        "pages": list(chunk),
        "page_range": f"{chunk[0]}-{chunk[-1]}" if len(chunk) > 1 else f"{chunk[0]}",
    } for i, chunk in enumerate(chunks)]


# 常见命名别名，兼容不同调用习惯。
paginate_pages = build_page_shards
split_pages = build_page_shards
page_shards = build_page_shards
partition_pages = build_page_shards
page_ranges = build_page_shards
shard_ranges = build_page_shards
build_shards = build_page_shards
compute_shards = build_page_shards
plan_shards = build_page_shards
resolve_shards = build_page_shards


def retry_calculator(
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 300.0,
    multiplier: float = 2.0,
    jitter: bool = True,
) -> float:
    """指数退避重试延迟计算器，返回下次等待秒数。"""
    import random
    delay = base_delay * (multiplier ** max(attempt - 1, 0))
    delay = min(delay, max_delay)
    if jitter:
        delay = delay * (0.5 + random.random() * 0.5)
    return round(delay, 3)