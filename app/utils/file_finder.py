#!/usr/bin/env python3
"""
递归查找指定目录下，文件名以给定前缀开头的所有文件。
直接在 main 函数中设置 directory 和 prefix 变量即可。
"""

import os
from pathlib import Path

def find_files_by_prefix(directory: str, prefix: str, case_sensitive: bool = True):
    """
    递归遍历 directory，返回所有文件名以 prefix 开头的文件路径列表。
    """
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise NotADirectoryError(f"目录不存在或不是目录: {directory}")

    matches = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            if case_sensitive:
                if file.startswith(prefix):
                    matches.append(os.path.join(root, file))
            else:
                if file.lower().startswith(prefix.lower()):
                    matches.append(os.path.join(root, file))
    return matches

def main():
    # ========== 在这里修改你要搜索的目录和前缀 ==========
    directory = "/Users/sheepjin/Documents/study/尚硅谷大模型速成班/05项目_掌柜智库"       # 你要搜索的目录，例如 /root 或 /Users/yourname
    prefix = ".env"           # 文件名前缀，例如 .env
    case_sensitive = True     # 是否大小写敏感，True 为敏感，False 为忽略大小写
    # ===================================================

    try:
        results = find_files_by_prefix(directory, prefix, case_sensitive)
    except Exception as e:
        print(f"错误: {e}")
        return

    if results:
        print(f"找到 {len(results)} 个以 '{prefix}' 开头的文件:")
        for path in results:
            print(path)
    else:
        print(f"未找到以 '{prefix}' 开头的文件。")

if __name__ == "__main__":
    main()