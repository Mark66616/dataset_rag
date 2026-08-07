"""
LangGraph Checkpointer 封装：基于 MongoDB 的 MongoDBSaver 单例。

设计要点：
- 复用 mongo_history_utils 的 HistoryMongoTool 单例连接（同一 MongoClient，不另起连接）；
- 懒加载：首次调用时才实例化 MongoDBSaver，避免模块导入即连接；
- 与 task_store 分工：checkpointer 存「图执行状态」（断点续跑），task 表存「任务业务状态」（前端轮询）；
- MongoDB 不可用时返回 None，调用方（compile）降级为无 checkpoint 模式，不阻塞启动。
"""
import logging
from typing import TYPE_CHECKING, Optional

from app.clients.mongo_history_utils import get_history_mongo_tool

if TYPE_CHECKING:
    from langgraph.checkpoint.mongodb import MongoDBSaver

logger = logging.getLogger(__name__)

# 检查点默认保留时长（秒）：7 天
DEFAULT_CHECKPOINT_TTL_SECONDS = 7 * 24 * 3600

# 全局单例
_checkpointer = None


def get_checkpointer() -> Optional["MongoDBSaver"]:
    """
    获取 MongoDBSaver 单例（懒加载）。

    复用现有 MongoDB 连接；连接失败返回 None，由调用方降级为无 checkpoint 模式。
    """
    global _checkpointer
    if _checkpointer is not None:
        return _checkpointer

    try:
        from langgraph.checkpoint.mongodb import MongoDBSaver

        mongo_tool = get_history_mongo_tool()
        db_name = mongo_tool.db_name or "kb002"
        # 与历史/任务共用同一数据库实例（MONGO_DB_NAME）
        _checkpointer = MongoDBSaver(
            client=mongo_tool.client,
            db_name=db_name,
            ttl=DEFAULT_CHECKPOINT_TTL_SECONDS,
        )
        logger.info(f"MongoDBSaver 初始化成功（db={db_name}，TTL={DEFAULT_CHECKPOINT_TTL_SECONDS}s）")
    except Exception as e:
        logger.warning(f"MongoDBSaver 初始化失败，图将以无 checkpoint 模式运行：{e}")
        _checkpointer = None

    return _checkpointer
