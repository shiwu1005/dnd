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
            content = resp.choices[0].message.content or ""
            return content
        except Exception as e:  # noqa: BLE001 —— 网络/限流/解析错误都降级
            last_err = e
            log.warning("LLM 调用失败(第%d次): %s", attempt + 1, e)
    raise LLMError(f"LLM 调用在 {config.LLM_MAX_RETRIES} 次重试后仍失败: {last_err}")


def _parse_json(text):
    """尽力从 LLM 输出中提取 JSON 对象。"""
    text = text.strip()
    # 去除可能的 markdown 代码围栏
    if text.startswith("```"):
        text = text.strip("`")
        text = text.strip()
        if text.startswith("json"):
            text = text[4:].strip()
    # 截取第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("输出中未找到 JSON 对象")
    return json.loads(text[start:end + 1])


# ---- 兜底文本 ----
FALLBACK_NARRATIVE = (
    "你继续前行，四周的景物在变化，风声中似乎藏着什么，"
    "但你一时无法完全辨认。你意识到这场冒险还远未结束。"
)
FALLBACK_OPTIONS = ["继续前进", "观察四周", "稍作休息"]


def narrate(prompt_user_text, options_hint=None):
    """叙事调用：给定本轮用户输入（Prompt 末尾内容），返回 (narrative, options)。

    重试逻辑：
    - JSON 解析失败或 narrative 为空 → 重试一次；
    - 仍失败 → 返回预设兜底文本。

    返回:
        dict: {"narrative": str, "options": [str,...]}
    """
    messages = [
        {"role": "system", "content": config.NARRATOR_SYSTEM_PROMPT},
        {"role": "user", "content": prompt_user_text},
    ]

    # 第一次尝试
    content = _safe_completion(messages)
    parsed = _try_narrative(content)
    if parsed is not None:
        return parsed

    # 重试一次（重新调用）
    content = _safe_completion(messages)
    parsed = _try_narrative(content)
    if parsed is not None:
        return parsed

    log.warning("叙事 JSON 两次解析失败，使用兜底文本。")
    return {"narrative": FALLBACK_NARRATIVE, "options": FALLBACK_OPTIONS}


def _safe_completion(messages):
    """带兜底的 chat_completion：调用失败时返回空串（由调用方判定重试/兜底）。"""
    try:
        return chat_completion(messages, response_format={"type": "json_object"})
    except LLMError:
        return ""


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
    narrative = data.get("narrative")
    options = data.get("options")
    if not narrative or not isinstance(narrative, str) or not narrative.strip():
        return None
    if not isinstance(options, list) or not options:
        options = FALLBACK_OPTIONS
    options = [str(o) for o in options if str(o).strip()][:4]
    if not options:
        options = FALLBACK_OPTIONS
    return {"narrative": narrative.strip(), "options": options}


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
    for _ in range(2):  # 最多两次尝试
        last = _safe_completion(messages)
        if not last:
            continue
        try:
            data = _parse_json(last)
            if isinstance(data, dict):
                return data
        except Exception:  # noqa: BLE001
            continue
    return {}