"""DeepSeek 调用封装。

- 使用 OpenAI SDK，base_url/config 见 config.py。
- 叙事调用用 JSON Output，返回 {"narrative":..., "options":[...]}。
- 所有调用带超时与重试上限，失败降级为兜底，不允许无限等待。
"""
import json
import logging

from . import config

log = logging.getLogger("game.llm")


class LLMError(Exception):
    pass


def _client():
    """懒加载 OpenAI 客户端（openai>=1 兼容接口）。"""
    from openai import OpenAI
    if not config.DEEPSEEK_API_KEY:
        log.warning("未设置 DEEPSEEK_API_KEY，LLM 调用将使用兜底文本。")
    return OpenAI(
        api_key=config.DEEPSEEK_API_KEY or "missing-key",
        base_url=config.DEEPSEEK_BASE_URL,
        timeout=config.LLM_TIMEOUT_SECONDS,
    )


def chat_completion(messages, response_format=None, temperature=0.8, max_tokens=None):
    """发起一次 chat.completions 调用，带重试上限。

    参数:
        messages: OpenAI 消息列表。
        response_format: 可选，如 {"type":"json_object"}。
    返回:
        str 文本内容。
    抛出:
        LLMError: 全部重试失败。
    """
    client = _client()
    kwargs = {
        "model": config.DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    if response_format is not None:
        kwargs["response_format"] = response_format
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    last_err = None
    for attempt in range(config.LLM_MAX_RETRIES):
        try:
            resp = client.chat.completions.create(**kwargs)
            msg = resp.choices[0].message
            content = msg.content or ""
            if not content:
                # 部分模型把最终文本放在 reasoning_content 字段
                content = getattr(msg, "reasoning_content", None) or ""
            return content
        except Exception as e:  # noqa: BLE001 —— 网络/限流/解析错误都降级
            last_err = e
            log.warning("LLM 调用失败(第%d次): %s", attempt + 1, e)
    raise LLMError(f"LLM 调用在 {config.LLM_MAX_RETRIES} 次重试后仍失败: {last_err}")


def _strip_fences(text):
    """去除 markdown 代码围栏。"""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text[:4].lower() == "json":
            text = text[4:].strip()
    return text


def _iter_json_objects(text):
    """扫描文本中所有可解析的 JSON 对象（容忍前后缀、多个对象、代码围栏）。"""
    text = _strip_fences(text)
    decoder = json.JSONDecoder()
    idx = 0
    found = []
    while True:
        start = text.find("{", idx)
        if start == -1:
            break
        try:
            obj, end = decoder.raw_decode(text[start:])
        except ValueError:
            idx = start + 1
            continue
        found.append(obj)
        idx = start + end
    return found


def _parse_json(text):
    """尽力从 LLM 输出中提取 JSON 对象。

    依次尝试：去围栏后的整体解析 → 扫描出的第一个字典。
    """
    text = _strip_fences(text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:  # noqa: BLE001
        pass
    for obj in _iter_json_objects(text):
        if isinstance(obj, dict):
            return obj
    raise ValueError("输出中未找到可解析的 JSON 对象")


# ---- 兜底文本 ----
FALLBACK_NARRATIVE = (
    "你继续前行，四周的景物在变化，风声中似乎藏着什么，"
    "但你一时无法完全辨认。你意识到这场冒险还远未结束。"
)


# 连续 JSON 模式失败计数：达到上限后本进程不再浪费一次调用去试 JSON 模式
_json_mode_failures = 0


def narrate(prompt_user_text, options_hint=None):
    """叙事调用：给定本轮用户输入，返回 {"narrative": str}。

    重试逻辑：
    - JSON 模式失败 → 按规格重试一次；
    - 仍失败 → 改用纯文本模式再生成（仍先尝试按 JSON 解析，再按散文解析）；
    - 连续失败达上限后，本进程后续轮次直接走纯文本模式，节省一次请求；
    - 仍失败 → 返回预设兜底文本。
    """
    global _json_mode_failures

    messages = [
        {"role": "system", "content": config.NARRATOR_SYSTEM_PROMPT},
        {"role": "user", "content": prompt_user_text},
    ]

    # JSON 模式：失败两次即降级（已降级则跳过）
    for _ in range(2 if _json_mode_failures < 2 else 0):
        content = _safe_completion(messages, json_mode=True)
        parsed = _try_narrative(content)
        if parsed is not None:
            _json_mode_failures = 0
            return parsed
        _json_mode_failures += 1
        _dump_debug("narrate-json", content)

    # 纯文本模式：模型多半仍会输出 JSON，所以先按 JSON 解析，失败再按散文解析
    content = _safe_completion(messages, json_mode=False)
    parsed = _try_narrative(content) or _parse_plain_narrative(content)
    if parsed is not None:
        log.info("已改用纯文本模式生成叙事。")
        return parsed
    _dump_debug("narrate-text", content)

    log.warning("叙事 JSON 模式与纯文本模式均失败，使用兜底文本。")
    return {"narrative": FALLBACK_NARRATIVE}


def _safe_completion(messages, json_mode=True):
    """带兜底的 chat_completion：调用失败时返回空串（由调用方判定重试/兜底）。"""
    try:
        fmt = {"type": "json_object"} if json_mode else None
        return chat_completion(messages, response_format=fmt)
    except LLMError:
        return ""


def _dump_debug(tag, content):
    """在设置 DND_DEBUG_LLM=1 时，把原始响应写入 llm_debug.log 便于排查。"""
    import os

    if os.environ.get("DND_DEBUG_LLM") not in ("1", "true", "yes"):
        return
    try:
        path = os.path.join(config.APP_DIR, "llm_debug.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n===== {tag} =====\n{content!r}\n")
    except Exception:  # noqa: BLE001
        pass


_NARRATIVE_KEYS = ("narrative", "text", "story", "description", "scene")


def _try_narrative(content):
    """尝试把文本解析成叙事结果；失败返回 None。"""
    if not content:
        return None
    try:
        data = _parse_json(content)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    narrative = None
    for key in _NARRATIVE_KEYS:
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            narrative = val.strip()
            break
    if not narrative:
        return None
    return {"narrative": narrative}


def _is_option_header(line):
    """识别独占一行的『选项 / 行动选项』标题。"""
    import re

    s = line.strip().strip("*#-• ").strip()
    return bool(re.fullmatch(r"(?:行动)?选项[：:]?", s))


def _parse_plain_narrative(content):
    """把纯文本输出归一化为叙事。

    本作不再提供行动选项；若模型仍附带「选项」段落，直接丢弃该段。
    """
    text = (content or "").strip()
    if not text:
        return None
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if _is_option_header(ln):
            lines = lines[:i]
            break
    narrative = "\n".join(lines).strip()
    if not narrative:
        return None
    return {"narrative": narrative}


def json_completion(system, user, temperature=0.8):
    """通用 JSON Output 调用（世界书生成、摘要、队友互动等）。

    返回:
        dict: 解析后的 JSON 对象；失败时返回 {}。
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    last = ""
    # 依次尝试：JSON 模式 → 纯文本模式（部分模型对 JSON 模式返回空）→ 再试一次
    for json_mode in (True, False, False):
        last = _safe_completion(messages, json_mode=json_mode)
        if not last:
            continue
        try:
            data = _parse_json(last)
            if isinstance(data, dict):
                return data
        except Exception:  # noqa: BLE001
            pass
        _dump_debug("json_completion", last)
    return {}