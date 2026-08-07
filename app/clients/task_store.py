"""
任务持久化存储：基于 MongoDB 的 task 集合（写穿透模式的上游存储）。

设计要点：
- 复用 mongo_history_utils 的 HistoryMongoTool 单例连接（同一 MongoClient，不另起连接）；
- 任务文档以 task_id 为唯一键，一次读写拿全（status/done_list/running_list/result/meta）；
- TTL 索引按 expire_at 自动清理旧任务，无需定时任务；
- 全部操作 try/except 降级：MongoDB 不可用时返回 None/False，不阻塞主流程
  （内存态 task_utils 仍可用，持久化只是增强，不是硬依赖）。
"""
import logging
import time
from typing import Any, Dict, List, Optional

from app.clients.mongo_history_utils import get_history_mongo_tool

logger = logging.getLogger(__name__)

# 任务集合名
TASK_COLLECTION = "task"
# 任务默认保留时长（秒）：7 天
DEFAULT_TASK_TTL_SECONDS = 7 * 24 * 3600


def _get_collection():
    """获取 task 集合（复用 HistoryMongoTool 单例的 db 连接）。"""
    return get_history_mongo_tool().db[TASK_COLLECTION]


def ensure_indexes() -> None:
    """创建 task 集合索引：task_id 唯一 + expire_at TTL（幂等）。"""
    try:
        col = _get_collection()
        col.create_index([("task_id", 1)], unique=True)
        col.create_index([("expire_at", 1)], expireAfterSeconds=0)
    except Exception as e:
        logger.warning(f"创建 task 集合索引失败（持久化降级为内存态）：{e}")


def upsert_task(
    task_id: str,
    *,
    status: str,
    done_list: List[str],
    running_list: List[str],
    result: Optional[Dict[str, str]] = None,
    meta: Optional[Dict[str, Any]] = None,
    ttl_seconds: int = DEFAULT_TASK_TTL_SECONDS,
) -> bool:
    """
    写入/更新任务文档（幂等 upsert）。

    :param task_id: 任务唯一 ID
    :param status: 任务状态（pending/processing/completed/failed）
    :param done_list: 已完成节点列表
    :param running_list: 运行中节点列表
    :param result: 任务结果字段（如 answer/error）
    :param meta: 任务元数据（如 file_name/user_id/item_name）
    :param ttl_seconds: 保留时长，过期后 TTL 索引自动清理
    :return: 写入成功 True；Mongo 不可用返回 False（调用方降级）
    """
    try:
        now = time.time()
        doc = {
            "task_id": task_id,
            "status": status,
            "done_list": done_list,
            "running_list": running_list,
            "result": result or {},
            "meta": meta or {},
            "updated_at": now,
            "expire_at": now + ttl_seconds,
        }
        col = _get_collection()
        col.update_one(
            {"task_id": task_id},
            {"$set": doc, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        return True
    except Exception as e:
        logger.warning(f"任务持久化失败（task_id={task_id}，降级为内存态）：{e}")
        return False


def load_task(task_id: str) -> Optional[Dict[str, Any]]:
    """按 task_id 读取任务文档；不存在或 Mongo 不可用返回 None。"""
    try:
        return _get_collection().find_one({"task_id": task_id})
    except Exception as e:
        logger.warning(f"任务读取失败（task_id={task_id}）：{e}")
        return None


def delete_task(task_id: str) -> bool:
    """删除任务文档；Mongo 不可用返回 False。"""
    try:
        _get_collection().delete_one({"task_id": task_id})
        return True
    except Exception as e:
        logger.warning(f"任务删除失败（task_id={task_id}）：{e}")
        return False


def list_tasks(limit: int = 100) -> List[Dict[str, Any]]:
    """按更新时间倒序列出最近任务（含 status/meta），供管理/监控页使用。"""
    try:
        col = _get_collection()
        cursor = col.find({}, {"_id": 0}).sort("updated_at", -1).limit(limit)
        return list(cursor)
    except Exception as e:
        logger.warning(f"任务列表读取失败：{e}")
        return []


# 模块加载时确保索引存在（幂等；失败不阻塞）
ensure_indexes()
