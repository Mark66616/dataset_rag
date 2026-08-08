"""
发布节点：将导入完成的新版本从 staging 切换为 active，并将同文档旧版本标记为 superseded。

背景（P0.3 修复）：
- 导入链路全程以 index_status='staging' 写入 Milvus，检索过滤只查 active，
  因此导入期间旧版本持续可用，无检索窗口期；
- 本节点在导入全链路成功后执行「发布」：当前版本 staging → active，
  同 document_id 的其他 active 版本 → superseded；
- 若导入中途失败，本节点不会执行：旧 active 版本不受影响（失败兜底见 file_import_service
  的异常分支，将残留 staging 标记为 failed）。

对应设计文档的 Saga 发布步骤（简化版，不引入跨库事务）：
    v2 Milvus active → v1 superseded（状态标记切换，非物理删除）

Milvus API 说明：MilvusClient 无按 filter 的 update，采用
「query 按过滤条件取 chunk_id → upsert 更新 index_status」两步完成状态切换。
"""
from typing import Any, Dict, List, Optional

from app.clients.milvus_utils import get_milvus_client
from app.conf.milvus_config import milvus_config
from app.core.logger import logger
from app.utils.escape_milvus_string_utils import escape_milvus_string
from app.utils.task_utils import add_running_task, add_done_task

CHUNKS_COLLECTION_NAME = milvus_config.chunks_collection

# 索引状态常量
INDEX_STATUS_STAGING = "staging"
INDEX_STATUS_ACTIVE = "active"
INDEX_STATUS_SUPERSEDED = "superseded"
INDEX_STATUS_FAILED = "failed"


def _get_client():
    client = get_milvus_client()
    if client is None:
        raise RuntimeError("无法获取 Milvus 客户端，发布失败")
    return client


def _query_chunk_ids(client, expr: str) -> List[int]:
    """按过滤条件查询 chunk_id 主键列表（Milvus 状态切换的第一步）。"""
    try:
        res = client.query(
            collection_name=CHUNKS_COLLECTION_NAME,
            filter=expr,
            output_fields=["chunk_id"],
        )
    except Exception as e:
        # 兼容集合中无匹配/字段缺失等情况
        logger.warning(f"查询待更新 chunk 失败（expr={expr}）：{e}")
        return []
    return [int(r["chunk_id"]) for r in res if r.get("chunk_id") is not None]


def _update_status_by_ids(client, chunk_ids: List[int], new_status: str) -> int:
    """按 chunk_id 主键批量 upsert 更新 index_status（Milvus 状态切换的第二步）。"""
    if not chunk_ids:
        return 0
    try:
        rows = [
            {"chunk_id": cid, "index_status": new_status}
            for cid in chunk_ids
        ]
        client.upsert(collection_name=CHUNKS_COLLECTION_NAME, data=rows)
        return len(rows)
    except Exception as e:
        logger.error(f"更新 index_status 失败（{new_status}，{len(chunk_ids)} 条）：{e}")
        raise


def publish_version(
    document_id: str,
    document_version: int,
    client=None,
) -> Dict[str, int]:
    """
    执行版本发布（Saga 切换，非物理删除）。

    :param document_id: 逻辑文档ID
    :param document_version: 本次导入的版本号（将置为 active）
    :param client: Milvus 客户端（测试可注入 fake）
    :return: 统计信息 {"promoted": int, "superseded": int}
    """
    doc_id = (document_id or "").strip()
    if not doc_id:
        raise ValueError("document_id 为空，无法发布")

    client = client or _get_client()
    safe_doc_id = escape_milvus_string(doc_id)
    stats = {"promoted": 0, "superseded": 0}

    # 1. 当前版本 staging → active
    promote_expr = (
        f'document_id == "{safe_doc_id}" '
        f'and document_version == {int(document_version)} '
        f'and index_status == "{INDEX_STATUS_STAGING}"'
    )
    promote_ids = _query_chunk_ids(client, promote_expr)
    if not promote_ids:
        logger.warning(f"发布：未找到 {doc_id} v{document_version} 的 staging 数据（可能为空文档）")
    else:
        _update_status_by_ids(client, promote_ids, INDEX_STATUS_ACTIVE)
        stats["promoted"] = len(promote_ids)
        logger.info(f"发布：{doc_id} v{document_version} 共 {len(promote_ids)} 条 staging → active")

    # 2. 同文档其他版本的 active → superseded（历史版本默认不再检索）
    supersede_expr = (
        f'document_id == "{safe_doc_id}" '
        f'and document_version != {int(document_version)} '
        f'and index_status == "{INDEX_STATUS_ACTIVE}"'
    )
    try:
        old_ids = _query_chunk_ids(client, supersede_expr)
        if old_ids:
            _update_status_by_ids(client, old_ids, INDEX_STATUS_SUPERSEDED)
            stats["superseded"] = len(old_ids)
            logger.info(f"发布：{doc_id} 共 {len(old_ids)} 条旧版本 active → superseded")
    except Exception as e:
        logger.warning(f"旧版本 superseded 标记失败（不影响新版本 active）：{e}")

    return stats


def mark_document_failed(document_id: str, client=None) -> None:
    """
    失败兜底：将指定 document_id 下所有残留 staging 数据标记为 failed。

    供导入任务异常分支调用（file_import_service 的 except 中），
    确保失败的导入不留 staging 混淆状态；旧 active 版本不受影响。
    """
    doc_id = (document_id or "").strip()
    if not doc_id:
        return
    try:
        client = client or _get_client()
        safe_doc_id = escape_milvus_string(doc_id)
        expr = f'document_id == "{safe_doc_id}" and index_status == "{INDEX_STATUS_STAGING}"'
        ids = _query_chunk_ids(client, expr)
        if ids:
            _update_status_by_ids(client, ids, INDEX_STATUS_FAILED)
            logger.info(f"失败兜底：{doc_id} 残留 staging（{len(ids)} 条）已标记 failed")
    except Exception as e:
        logger.warning(f"失败兜底标记失败（{doc_id}）：{e}")


def node_publish_version(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    LangGraph 节点：版本发布（导入链路最后一个节点）。

    前置：node_import_milvus 已以 staging 状态写入全部 chunk。
    执行：当前版本 → active；同文档旧版本 → superseded。
    失败：抛出异常，由上层（API 异常分支）标记 failed，旧 active 不受影响。

    :param state: 导入链路全局状态（需含 document_id / document_version / task_id）
    :return: 原状态（附加 publish_stats 便于日志/调试）
    """
    func_name = "node_publish_version"
    task_id = state.get("task_id", "")
    add_running_task(task_id, func_name)

    document_id = state.get("document_id", "")
    document_version = int(state.get("document_version") or 1)

    if not document_id:
        logger.warning(f"[{task_id}] 发布节点跳过：document_id 为空")
        add_done_task(task_id, func_name)
        return state

    logger.info(f"[{task_id}] 开始发布：{document_id} v{document_version}")
    try:
        stats = publish_version(document_id, document_version)
        state["publish_stats"] = stats
        logger.info(f"[{task_id}] 发布完成：{stats}")
    except Exception as e:
        logger.error(f"[{task_id}] 发布失败：{e}", exc_info=True)
        add_done_task(task_id, func_name)
        raise

    add_done_task(task_id, func_name)
    return state
