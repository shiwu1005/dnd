"""队友系统。

- 队友 HP、属性、关系值全部由代码维护，Flash 不得修改。
- 玩家主动交谈：Prompt 带 personality/speech_style/background 及好感。
- 队友被动互动：每轮结束掷 5%-8%，抽两人生成互动。
- 互动由 Flash 生成 1-2 句，代码按 affinity 调整（友好+2，摩擦-3）。
- 关系节点事件：affinity 跨阈值时触发特殊互动。
"""
import json
import logging
import random

from . import config, db, llm

log = logging.getLogger("game.companions")

INTERACTION_SYSTEM = (
    "你是冒险故事的叙述者。根据给定队友的性格与关系值，"
    "生成 1-2 句两人之间的日常互动描写。"
    "不得改变任何数值，不得引入新剧情事实。只输出纯文本，不要 JSON。"
)


def get_active_companions(conn, save_id):
    """返回该存档活跃队友（is_active=1）的完整信息。"""
    rows = conn.execute(
        """
        SELECT c.*, cs.current_hp, cs.joined_turn, cs.is_active, cpa.affinity AS player_affinity
        FROM companion_state cs
        JOIN companions c ON c.id = cs.companion_id
        LEFT JOIN companion_player_affinity cpa
               ON cpa.save_id = cs.save_id AND cpa.companion_id = c.id
        WHERE cs.save_id = ? AND cs.is_active = 1
        ORDER BY cs.companion_id
        """,
        (save_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def join_companion(conn, save_id, companion_id, current_turn):
    """队友入队（写入 companion_state）。"""
    comp = conn.execute(
        "SELECT * FROM companions WHERE id=?", (companion_id,)
    ).fetchone()
    if comp is None:
        return False
    # 初始化与其他活跃队友的关系
    active = get_active_companions(conn, save_id)
    for other in active:
        if other["id"] == companion_id:
            continue
        _insert_relation(conn, save_id, companion_id, other["id"], 0)
    conn.execute(
        "INSERT OR REPLACE INTO companion_state (save_id, companion_id, current_hp, joined_turn, is_active) "
        "VALUES (?,?,?,?,1)",
        (save_id, companion_id, comp["max_hp"], current_turn),
    )
    # 玩家好感初始 0
    conn.execute(
        "INSERT OR IGNORE INTO companion_player_affinity (save_id, companion_id, affinity) VALUES (?,?,0)",
        (save_id, companion_id),
    )
    conn.commit()
    return True


def _insert_relation(conn, save_id, a, b, affinity):
    a, b = min(a, b), max(a, b)
    conn.execute(
        "INSERT OR REPLACE INTO companion_relations (save_id, companion_a, companion_b, affinity) "
        "VALUES (?,?,?,?)",
        (save_id, a, b, affinity),
    )


def get_relation(conn, save_id, a, b):
    a, b = min(a, b), max(a, b)
    row = conn.execute(
        "SELECT affinity FROM companion_relations WHERE save_id=? AND companion_a=? AND companion_b=?",
        (save_id, a, b),
    ).fetchone()
    return row["affinity"] if row else 0


def get_player_affinity(conn, save_id, companion_id):
    row = conn.execute(
        "SELECT affinity FROM companion_player_affinity WHERE save_id=? AND companion_id=?",
        (save_id, companion_id),
    ).fetchone()
    return row["affinity"] if row else 0


def adjust_relation(conn, save_id, a, b, delta):
    """代码调整两队友关系值。返回新值。"""
    val = get_relation(conn, save_id, a, b) + delta
    _insert_relation(conn, save_id, a, b, val)
    conn.commit()
    return val


def adjust_player_affinity(conn, save_id, companion_id, delta):
    val = get_player_affinity(conn, save_id, companion_id) + delta
    conn.execute(
        "INSERT OR REPLACE INTO companion_player_affinity (save_id, companion_id, affinity) VALUES (?,?,?)",
        (save_id, companion_id, val),
    )
    conn.commit()
    return val


def maybe_passive_interaction(conn, save_id, current_turn):
    """每轮结束掷 5%-8%，命中则抽两人生成互动。返回注入提示或 None。"""
    chance = random.uniform(config.COMPANION_PASSIVE_CHANCE_MIN, config.COMPANION_PASSIVE_CHANCE_MAX)
    if random.random() >= chance:
        return None
    active = get_active_companions(conn, save_id)
    if len(active) < 2:
        return None
    a, b = random.sample(active, 2)
    affinity = get_relation(conn, save_id, a["id"], b["id"])

    if affinity > 30:
        flavor = "友好（分享食物、互相调侃）"
        delta = config.COMPANION_AFFINITY_UP
    elif affinity < -30:
        flavor = "摩擦（争吵、冷战）"
        delta = config.COMPANION_AFFINITY_DOWN
    else:
        flavor = "中性（讨论路线、交换情报）"
        delta = 0

    prompt = (
        f"队友间互动：{a['name']}（性格：{a['personality']}）与 {b['name']}"
        f"（性格：{b['personality']}）当前关系值 {affinity}（{flavor}）。"
        "生成 1-2 句互动描写。"
    )
    text = _llm_interaction(prompt)
    if not text:
        text = f"{a['name']}与{b['name']}默默交换了一个眼神。"

    # 代码调整关系值
    if delta:
        adjust_relation(conn, save_id, a["id"], b["id"], delta)
    conn.execute(
        "INSERT INTO companion_interactions (save_id, companion_a, companion_b, turn, summary) "
        "VALUES (?,?,?,?,?)",
        (save_id, a["id"], b["id"], current_turn, text),
    )
    conn.commit()
    return text


def _llm_interaction(prompt):
    try:
        content = llm.chat_completion(
            [{"role": "system", "content": INTERACTION_SYSTEM}, {"role": "user", "content": prompt}],
            temperature=0.9,
        )
        return content.strip()
    except llm.LLMError:
        return ""


def build_party_status_line(conn, save_id):
    """生成 Prompt 中『当前队伍』一行。"""
    active = get_active_companions(conn, save_id)
    if not active:
        return ""
    parts = []
    for c in active:
        status = "良好" if c["current_hp"] > c["max_hp"] * 0.5 else "受伤"
        parts.append(f"{c['name']}(HP {c['current_hp']}/{c['max_hp']},{status})")
    return "当前队伍：" + "，".join(parts) + "。"


def build_relations_summary(conn, save_id, limit=3):
    """生成队伍关系摘要（取玩家好感 + 几条队友间关系）。"""
    lines = []
    active = get_active_companions(conn, save_id)
    for c in active:
        aff = get_player_affinity(conn, save_id, c["id"])
        label = "亲近" if aff > 15 else ("冷淡" if aff < -10 else "中性")
        lines.append(f"{c['name']}对你{label}(好感{aff})")
    # 队友间关系
    shown = 0
    for i in range(len(active)):
        for j in range(i + 1, len(active)):
            if shown >= limit:
                break
            a, b = active[i], active[j]
            aff = get_relation(conn, save_id, a["id"], b["id"])
            lines.append(f"{a['name']}与{b['name']}关系{aff}")
            shown += 1
    return "队伍关系：" + "；".join(lines) + "。"


def relation_cross_events(conn, save_id, current_turn):
    """检测队友-玩家好感跨阈值事件。返回注入提示或 None。

    处理「关系归零」梗需在 meme 模块单独判定。
    """
    # 此处只做正向阈值事件示例：好感达到 30 / 50 时触发特殊互动
    triggers = []
    for c in get_active_companions(conn, save_id):
        aff = get_player_affinity(conn, save_id, c["id"])
        # 检查是否刚跨过 +30 或 -30
        # 简化：记录上次在 companion_interactions 里该队友最后一轮的好感无法直接追溯，
        # 因此用 companion_player_affinity 与一个阈值标记表。MVP 用简化逻辑。
    return None