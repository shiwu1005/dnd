"""随机事件触发模块。

- 时机：场景切换掷 30%；每轮结束掷 8%-12%；同轮不重复。
- 按 weight 加权随机抽一条，写 event_history，返回注入提示。
"""
import json
import logging
import random

from . import config, db

log = logging.getLogger("game.events")


def roll_trigger():
    """按场景切换 30% 概率决定是否触发。"""
    return random.random() < config.SCENE_CHANGE_EVENT_CHANCE


def roll_turn_end():
    """按每轮结束 8%-12% 随机概率。"""
    chance = random.uniform(
        config.TURN_END_EVENT_CHANCE_MIN,
        config.TURN_END_EVENT_CHANCE_MAX,
    )
    return random.random() < chance


def pick_event(conn, save_id, terrain, level, current_turn):
    """从候选池按 weight 加权抽一条事件。返回事件 dict 或 None。"""
    terrain_like = f'%"{terrain}"%'
    rows = conn.execute(
        """
        SELECT e.* FROM events e
        WHERE e.terrain LIKE ?
          AND e.level_min <= ? AND e.level_max >= ?
          AND e.id NOT IN (
              SELECT event_id FROM event_history
              WHERE save_id = ? AND fired_turn > ? - e.cooldown
          )
        """,
        (terrain_like, level, level, save_id, current_turn),
    ).fetchall()
    if not rows:
        return None
    # 加权随机
    total = sum(r["weight"] for r in rows)
    r = random.uniform(0, total)
    upto = 0
    for row in rows:
        upto += row["weight"]
        if upto >= r:
            return dict(row)
    return dict(rows[-1])


def fire_event(conn, save_id, terrain, level, current_turn):
    """尝试触发一个事件。返回注入提示文本或 None。

    时机由调用方通过 roll_trigger / roll_turn_end 决定。
    同轮不重复：由调用方保证每轮只调用一次。
    """
    ev = pick_event(conn, save_id, terrain, level, current_turn)
    if ev is None:
        return None
    # 写历史
    conn.execute(
        "INSERT OR IGNORE INTO event_history (save_id, event_id, fired_turn) VALUES (?,?,?)",
        (save_id, ev["id"], current_turn),
    )
    conn.commit()
    prompt = (
        f"本轮触发固定事件：{ev['name']}。事件骨架：{ev['skeleton']}。"
        "请自然融入叙事，不得改变事件类型和核心事实。"
    )
    log.info("触发事件 %s（type=%s）", ev["name"], ev["event_type"])
    return prompt


# 供事件骨架使用的事件类型（关系节点事件等复用 events 表）
def event_type_terrain_any():
    """返回 terrain=['any'] 的 JSON 表示，供关系节点事件使用。"""
    return json.dumps(["any"], ensure_ascii=False)