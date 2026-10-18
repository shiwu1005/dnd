"""文本工具：清洗控制台输入中可能出现的非法代理字符。

Windows 控制台在代码页不匹配时，`input()` 可能产生孤立代理字符
（lone surrogate，如 '\\udcaf'），这类字符既无法写入 UTF-8 编码的
SQLite，也无法序列化为 LLM 请求 JSON。统一在入口处清洗。
"""


def safe_text(value):
    """把任意字符串转为可安全编码为 UTF-8 的文本。

    孤立代理字符会被替换为 '?'，其余字符保持不变。
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    # 含代理字符时 encode 会报错，errors='replace' 将其替换为 '?'
    return value.encode("utf-8", "replace").decode("utf-8")


def setup_console():
    """在 Windows 下把控制台代码页切到 UTF-8，避免中文输入输出乱码。

    非 Windows 平台或失败时静默跳过。
    """
    import sys

    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    try:
        import ctypes

        k32 = ctypes.windll.kernel32
        k32.SetConsoleOutputCP(65001)
        k32.SetConsoleCP(65001)
    except Exception:  # noqa: BLE001
        pass