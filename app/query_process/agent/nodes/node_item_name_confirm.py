import time
import sys

from app.clients.mongo_history_utils_new import save_chat_message
from app.utils.task_utils import add_running_task, add_done_task


def node_item_name_confirm(state):
    """
    节点功能：确认用户问题中的核心商品名称。
    输入：state['original_query']
    输出：更新 state['item_names']
    节点作用：
        1. 提取与改写 ：结合历史对话提取商品名，并将模糊问题改写为完整独立的精准问题。
        2. 向量化检索 ：将提取出的商品名在 Milvus 向量库中进行混合搜索。
        3. 标准化对齐 ：根据评分高低自动对齐标准型号，或生成反问让用户手动确认。
        4. 同步历史记录 ：将改写后的问题、确认的商品名和处理状态实时写入 MongoDB 数据库。
    """
    print(f"---node_item_name_confirm 处理")

    add_running_task(state['session_id'], sys._getframe().f_code.co_name, state.get("is_stream"))
    add_done_task(state['session_id'], sys._getframe().f_code.co_name, state.get("is_stream"))

    save_chat_message(state['session_id'], "user", state['original_query'], "", state.get("item_names", []))

    print(f"---已保存对话 处理完成")

