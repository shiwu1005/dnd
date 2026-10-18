"""梗系统。

- 梗触发条件由代码判定，Flash 不参与触发决策。
- 仅在检定发生的那一轮检查；命中后写 meme_history，该梗进入 cooldown。
- 命中时 prompt_hint 追加到 Prompt 末尾，优先级低于系统指令。
- 梗只影响叙事语气，不影响规则结果和剧情分支。
- 大失败类梗必须标注『无害』。
"""
import json
import logging
import random

from . import config, db

log = logging.getLogger("game.memes")

# 关系归零梗需要在好感变化后判定（见 companions 调整处）
RELATION_ZERO_MEME_NAME = "关系归零"


def all_memes(conn):
    rows = conn.execute("SELECT * FROM memes").fetchall()
    return [dict(r) for r in rows]


def _in_cooldown(conn, save_id, meme_id, current_turn):
    row = conn.execute(
        "SELECT fired_turn FROM meme_history WHERE save_id=? AND meme_id=? ORDER BY fired_turn DESC LIMIT 1",
        (save_id, meme_id),
    ).fetchone()
    if row is None:
        return False
    meme = conn.execute("SELECT cooldown FROM memes WHERE id=?", (meme_id,)).fetchone()
    cd = meme["cooldown"] if meme else 0
    return (current_turn - row["fired_turn"]) < cd


def check_memes(conn, save_id, current_turn, check_result=None, affinity_cross=None):
    """在检定发生后检查梗。

    参数:
        check_result: 最近一次属性检定结果（dict），形如 {"stat","stat_value","roll","success"}。
        affinity_cross: 若好感跨到 0 且方向向下，则传 {"companion_name": str}。

    返回:
        命中的 prompt_hint 列表（每条命中写入 meme_history 并进入 cooldown）。
    """
    hints = []
    for meme in all_memes(conn):
        if _in_cooldown(conn, save_id, meme["id"], current_turn):
            continue
        trigger = _parse_trigger(meme["trigger"])
        if _match(trigger, check_result, affinity_cross):
            conn.execute(
                "INSERT OR IGNORE INTO meme_history (save_id, meme_id, fired_turn) VALUES (?,?,?)",
                (save_id, meme["id"], current_turn),
            )
            hints.append(meme["prompt_hint"])
            log.info("触发梗：%s", meme["name"])
    if hints:
        conn.commit()
    return hints


def _parse_trigger(raw):
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _match(trigger, check_result, affinity_cross):
    # 关系归零梗
    if "affinity_cross" in trigger and affinity_cross:
        if trigger.get("affinity_cross") == 0 and trigger.get("direction") == "down":
            return True
        return False

    if check_result is None:
        return False

    # 大失败 / 大成功
    stat = trigger.get("stat", "any")
    if stat != "any" and stat != check_result.get("stat"):
        return False
    roll = check_result.get("roll")
    success = check_result.get("success")

    # 大失败：自然一（无害）
    if trigger.get("check") == "fail" and trigger.get("roll") == 1:
        if roll == 1 and not success:
            return True
        return False

    # 大成功：roll == 20 且成功，且属性值 <= 上限（如 <=3）
    if trigger.get("check") == "success" and trigger.get("roll") == 20:
        stat_value_max = trigger.get("stat_value_max")
        if roll != 20 or not success:
            return False
        if stat_value_max is not None:
            stat_value = check_result.get("stat_value")
            if stat_value is None or stat_value > stat_value_max:
                return False
        return True

    return False