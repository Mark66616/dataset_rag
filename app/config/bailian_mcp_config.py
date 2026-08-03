# 导入核心依赖：数据类、环境变量读取、路径处理
from dataclasses import dataclass
import os
from dotenv import load_dotenv

import asyncio
import os
import json
import sys
from agents.mcp import MCPServerSse # pip install openai-agents
from agents.mcp import MCPServerStreamableHttp # pip install openai-agents

from app.conf.bailian_mcp_config import mcp_config
from app.utils.task_utils import add_running_task,add_done_task


load_dotenv()


# 定义mcp的服务配置
@dataclass
class McpConfig:
    mcp_sse_base_url: str
    mcp_base_url: str
    api_key : str

mcp_config = McpConfig(
    mcp_sse_base_url=os.getenv("MCP_DASHSCOPE_BASE_URL"),
    mcp_base_url=os.getenv("MCP_DASHSCOPE_BASE_URL_STREAMABLE"),
    api_key=os.getenv("OPENAI_API_KEY")
)

DASHSCOPE_BASE_URL_SSE = mcp_config.mcp_sse_base_url
DASHSCOPE_BASE_URL_STREAMABLE = mcp_config.mcp_base_url
DASHSCOPE_API_KEY = mcp_config.api_key

async def mcp_call(query):
    # 初始化 MCP
    search_mcp = MCPServerSse(
        name="search_mcp",
        params={
            "url": DASHSCOPE_BASE_URL_SSE,
            "headers": {"Authorization": DASHSCOPE_API_KEY},
            "timeout": 300,
            "sse_read_timeout": 300
        }
    )

    try:
        await search_mcp.connect()
        # 直接调用工具
        result = await search_mcp.call_tool(
            tool_name="bailian_web_search",
            arguments={"query": query, "count": 5}
            # arguments={"query": "今天北京的天气情况", "count": 5}
        )
        return result
    finally:
        await search_mcp.cleanup()

async def mcp_call_streamable(query):
    search_mcp = MCPServerStreamableHttp(
        name="search_mcp",
        params={
            "url": DASHSCOPE_BASE_URL_STREAMABLE,
            "headers": {"Authorization": DASHSCOPE_API_KEY},
            "timeout": 300,
            "sse_read_timeout": 300,
            "terminate_on_close": True,
        },
        max_retry_attempts=2,
    )
    try:
        await search_mcp.connect()
        result = await search_mcp.call_tool(
            tool_name="bailian_web_search",
            arguments={"query": query, "count": 5},
        )
        return result
    finally:
        await search_mcp.cleanup()

def node_web_search_mcp(state):
    print("---node_web_search_mcp处理---")
    add_running_task(state["session_id"], sys._getframe().f_code.co_name, state.get("is_stream"))

    query = state.get("rewritten_query", "")
    docs = []
    # 如果没有查询内容，直接返回
    if query:
        result = asyncio.run(mcp_call_streamable(query))
        if result:
            pages = json.loads(result.content[0].text).get("pages") or []
            # 统一输出结构化结果，供后续 rerank/引用使用
            # 每条：{title, url, snippet}

            for item in pages:
                snippet = (item.get("snippet") or "").strip()
                url = (item.get("url") or "").strip()
                title = (item.get("title") or "").strip()
                if not snippet:
                    continue
                docs.append({"title": title, "url": url, "snippet": snippet})

            print("MCP 搜索结果:", docs)
    add_done_task(state["session_id"], sys._getframe().f_code.co_name, state.get("is_stream"))
    if docs:
        return {"web_search_docs": docs}
    return {}

from dotenv import load_dotenv

if __name__ == '__main__':
    load_dotenv()
    test_state = {
        "session_id":"test_1234567890",
        "rewritten_query": "HAK180 在出厂默认状态下，若想在纸张上只把烫金膜转印到顶部 50 mm–170 mm 的局部区域，应在操作面板上如何设置"
    }

    # 调用 websearch_node 函数
    result_state = node_web_search_mcp(test_state)

    # 验证结果
    print("测试结果:")
    print(f"查询内容: {test_state.get('rewritten_query')}")

    # 输出搜索结果
    search_results = result_state.get('web_search_docs', [])
    print(f"搜索结果数量: {len(search_results)}")
    print("search_results", search_results)