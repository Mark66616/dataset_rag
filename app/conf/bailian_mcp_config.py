# 导入核心依赖：数据类、环境变量读取、路径处理
import os
from dataclasses import dataclass


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