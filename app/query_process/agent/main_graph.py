from langgraph.graph import StateGraph, END

from app.core.node_hooks import node_hook, default_retry_policy
from app.query_process.agent.nodes.node_answer_output import node_answer_output
from app.query_process.agent.nodes.node_item_name_confirm import node_item_name_confirm
from app.query_process.agent.nodes.node_rerank import node_rerank
from app.query_process.agent.nodes.node_rrf import node_rrf
from app.query_process.agent.nodes.node_search_embedding import node_search_embedding
from app.query_process.agent.nodes.node_search_embedding_hyde import node_search_embedding_hyde
from app.query_process.agent.nodes.node_web_search_mcp import node_web_search_mcp
from app.query_process.agent.state import QueryGraphState

builder = StateGraph(QueryGraphState)

# 注册节点：统一用 node_hook 包装（相呼应日志/耗时指标/任务状态）；
# 调用外部服务（LLM / Milvus / MCP Web）的节点挂载默认重试策略。
builder.add_node("node_item_name_confirm", node_hook(node_item_name_confirm),
                 retry_policy=default_retry_policy())  # LLM + Milvus + Mongo，可重试
builder.add_node("node_search_embedding", node_hook(node_search_embedding),
                 retry_policy=default_retry_policy())  # BGE 向量化 + Milvus，可重试
builder.add_node("node_search_embedding_hyde", node_hook(node_search_embedding_hyde),
                 retry_policy=default_retry_policy())  # LLM + Milvus，可重试
builder.add_node("node_web_search_mcp", node_hook(node_web_search_mcp),
                 retry_policy=default_retry_policy())  # MCP Web 搜索，可重试
builder.add_node("node_rrf", node_hook(node_rrf))  # 本地融合，不重试
builder.add_node("node_rerank", node_hook(node_rerank))  # 本地 Rerank，不重试
builder.add_node("node_answer_output", node_hook(node_answer_output),
                 retry_policy=default_retry_policy())  # LLM 生成，可重试

# 入口
builder.set_entry_point("node_item_name_confirm")

# 条件路由
def route_after_item_confirm(state: QueryGraphState):
    """
    根据 node_item_name_confirm 的结果决定后续路径：

    - 若 state 中已存在 answer（反问用户 / 拒绝回答），说明无需检索，
      直接进入答案输出节点结束流程：
        1. 多选一（反问用户）：用户问题模糊，库中存在多个候选型号且置信度不足，
           节点生成反问句，如"您是想问以下哪个产品：华为P60 128G、华为P60 Art？"
        2. 查无此人（拒绝回答）：库中无匹配产品或评分过低（<0.6），
           节点生成拒答句，如"抱歉，未找到相关产品，请提供准确型号。"
    - 否则说明商品名已确认，并行进入三路检索：普通向量检索 / HyDE 检索 / Web 搜索。
    """
    if state.get("answer"):
        return "node_answer_output"
    # 直接进入并发搜索
    return "node_search_embedding", "node_search_embedding_hyde", "node_web_search_mcp"

builder.add_conditional_edges(
    "node_item_name_confirm",
    route_after_item_confirm,
    {
        "node_answer_output": "node_answer_output",
        "node_search_embedding": "node_search_embedding",
        "node_search_embedding_hyde": "node_search_embedding_hyde",
        "node_web_search_mcp": "node_web_search_mcp",
    }
)

# 三个搜索 → 直接汇合到 RRF
builder.add_edge("node_search_embedding", "node_rrf")
builder.add_edge("node_search_embedding_hyde", "node_rrf")
builder.add_edge("node_web_search_mcp", "node_rrf")

# 正常流程
builder.add_edge("node_rrf", "node_rerank")
builder.add_edge("node_rerank", "node_answer_output")
builder.add_edge("node_answer_output", END)

query_app = builder.compile()

if __name__ == "__main__":
    import json

    from app.query_process.agent.main_graph import query_app
    from app.query_process.agent.state import create_query_default_state
    from app.core.logger import logger

    logger.info("===== 开始测试 =====")

    initial_state = create_query_default_state(session_id="test_001",
                                               original_query="华为P60怎么样?")
    final_state = None

    # 只输出更最终的状态值（字典形式），不包含节点名称、执行日志、元数据等额外信息
    for event in query_app.stream(initial_state):
        for key, value in event.items():
            logger.info(f"节点: {key}")
            final_state = value

    # 格式化输出最终状态
    logger.info(f"最终状态: {json.dumps(final_state, indent=4, ensure_ascii=False)}")

    logger.info("图结构:")
    # uv add grandalf
    query_app.get_graph().print_ascii()