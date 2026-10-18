"""长线记忆（摘要）模块。

- 每 15-20 轮触发摘要：Flash 把最近 N 轮压缩成 200-300 字，写入 summaries（seq 递增）。
- 主循环『最近对话』只保留最近 5-8 轮。
- 长期摘要取最新一条（或拼接最近几条，总长 <= 500 字）。
- 摘要与叙事分开调用。
"""
import logging
import random

from . import config, db, llm

log = logging.getLogger("game.memory")

SUMMARY_SYSTEM = (
    "你是冒险故事的记录员。把给定的最近对话压缩成一份中文摘要，"
    f"控制在 {config.SUMMARY_LENGTH_MIN}-{config.SUMMARY_LENGTH_MAX} 字以内。"
    "摘要要保留：当前场景、事件进展、队伍状态、关键人物与关系、"
    "以及尚未解决的伏笔。只输出纯文本摘要，不要输出 JSON。"
)


def recent_turns(conn, save_id, n):
    """取最近 n 轮对话（narrator + player 交错），不含系统注入文本。"""
    rows = conn.execute(
        "SELECT role, content FROM turns WHERE save_id=? ORDER BY id DESC LIMIT ?",
        (save_id, n * 2),
    ).fetchall()
    rows = list(reversed(rows))
    return [dict(r) for r in rows]


def _turn_count(conn, save_id):
    return conn.execute(
        "SELECT COUNT(*) AS c FROM turns WHERE save_id=? AND role IN ('player','narrator')",
        (save_id,),
    ).fetchone()["c"]


def next_summary_seq(conn, save_id):
    row = conn.execute(
        "SELECT MAX(seq) AS m FROM summaries WHERE save_id=?", (save_id,)
    ).fetchone()
    return (row["m"] or 0) + 1


def maybe_trigger_summary(conn, save_id):
    """根据轮数随机触发摘要。返回是否触发。"""
    count = _turn_count(conn, save_id)
    window = random.randint(config.SUMMARY_EVERY_MIN, config.SUMMARY_EVERY_MAX)
    if count < window:
        return False
    # 距上一次摘要是否已够窗口：统计自上次摘要以来的轮数
    last = conn.execute(
        "SELECT content FROM summaries WHERE save_id=? ORDER BY seq DESC LIMIT 1",
        (save_id,),
    ).fetchone()
    # 简化：每窗口轮触发一次（用轮数取模）
    if last is None:
        return count >= window
    # 存在摘要则每 window 轮检查一次
    if count % window == 0:
        return True
    return False


def create_summary(conn, save_id):
    """用 Flash 压缩最近 N 轮并写入 summaries。失败时写占位。"""
    recent = recent_turns(conn, save_id, config.RECENT_TURNS_MAX)
    if not recent:
        return False
    transcript = "\n".join(
        (("玩家" if t["role"] == "player" else "叙述者") + "：" + t["content"])
        for t in recent
    )
    content = _llm_summary(transcript)
    if not content:
        content = "（摘要生成失败：无可靠内容。）"
    seq = next_summary_seq(conn, save_id)
    conn.execute(
        "INSERT INTO summaries (save_id, seq, content) VALUES (?,?,?)",
        (save_id, seq, content),
    )
    conn.commit()
    log.info("已写入摘要 seq=%d", seq)
    return True


def _llm_summary(transcript):
    try:
        content = llm.chat_completion(
            [
                {"role": "system", "content": SUMMARY_SYSTEM},
                {"role": "user", "content": transcript},
            ],
            temperature=0.4,
        )
        return content.strip()
    except llm.LLMError:
        return ""


def load_long_summary(conn, save_id, max_chars=None):
    """取最新一条摘要（或拼接最近几条，总长<=max_chars）。"""
    max_chars = max_chars or config.SUMMARY_TOTAL_MAX_CHARS
    rows = conn.execute(
        "SELECT content FROM summaries WHERE save_id=? ORDER BY seq DESC",
        (save_id,),
    ).fetchall()
    if not rows:
        return ""
    parts = []
    total = 0
    for r in rows:
        text = r["content"]
        if total + len(text) > max_chars and parts:
            break
        parts.append(text)
        total += len(text)
        if total >= max_chars:
            break
    return "\n".join(parts)