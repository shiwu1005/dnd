"""存档管理与 Prompt 组装。

- 多存档：新建、加载、删除、列表。
- 加载：读 saves → 读该 save_id 的 lorebook/world_facts/summaries/最近 turns/队伍状态 → 组装 Prompt → 继续。
- 加载不重新生成世界书。
"""
import json
import logging

from . import config, db, memory

log = logging.getLogger("game.saves")


def new_save(name, worldbook_ok=True):
    """新建存档（调用方需随后生成世界书并初始化玩家/队友）。返回 save_id。"""
    return db.create_save(name)


def load_save_state(save_id):
    """读取存档的完整状态，供游戏循环继续。返回 dict。"""
    conn = db.connect()
    try:
        save = conn.execute("SELECT * FROM saves WHERE save_id=?", (save_id,)).fetchone()
        if save is None:
            return None
        player = conn.execute(
            "SELECT * FROM player_state WHERE save_id=?", (save_id,)
        ).fetchone()
        return {
            "save": dict(save),
            "player": dict(player) if player else None,
            "world_facts": {
                r["key"]: r["value"]
                for r in conn.execute("SELECT key, value FROM world_facts WHERE save_id=?", (save_id,))
            },
        }
    finally:
        conn.close()


# ---- Prompt 组装 ----
def build_prompt(conn, save_id, player_input, extra_event=None, meme_hints=None, talk_companion=None):
    """按固定顺序组装本轮 Prompt。

    顺序：常驻设定 → 命中世界书 → 长期摘要 → 队伍状态 → 最近 N 轮 → 玩家输入。
    返回 (messages_user_text, context_text)。
    """
    sections = []

    # 1. 常驻设定（world_facts + constant 世界书条目）
    constant = _constant_settings(conn, save_id)
    if constant:
        sections.append("【常驻设定】\n" + constant)

    # 2. 命中世界书（关键词扫描最近对话）
    lore = _matched_lore(conn, save_id)
    if lore:
        sections.append("【世界书·命中条目】\n" + lore)

    # 3. 长期摘要
    summary = memory.load_long_summary(conn, save_id)
    if summary:
        sections.append("【长期记忆摘要】\n" + summary)

    # 4. 队伍状态
    party = _party_status(conn, save_id)
    if party:
        sections.append("【队伍状态】\n" + party)

    # 5. 固定事件
    if extra_event:
        sections.append("【本轮事件】\n" + extra_event)

    # 6. 队友对话（若玩家主动交谈）
    if talk_companion:
        sections.append("【与队友对话】\n" + talk_companion)

    # 7. 最近 N 轮
    recent = memory.recent_turns(conn, save_id, config.RECENT_TURNS_MAX)
    if recent:
        recent_block = "\n".join(
            (("玩家" if t["role"] == "player" else "叙述者") + "：" + t["content"])
            for t in recent
        )
        sections.append("【最近对话】\n" + recent_block)

    # 8. 玩家输入（末尾）
    sections.append("【玩家行动】\n" + player_input)

    # 9. 梗提示（追加到末尾，优先级最低）
    if meme_hints:
        sections.append("【叙事风格提示】\n" + "\n".join(meme_hints))

    return "\n\n".join(sections)


def _constant_settings(conn, save_id):
    """常驻世界书条目 + world_facts。精简控制在几百字内。"""
    rows = conn.execute(
        "SELECT content FROM lorebook WHERE save_id=? AND constant=1 ORDER BY priority DESC",
        (save_id,),
    ).fetchall()
    parts = [r["content"] for r in rows]
    facts = conn.execute(
        "SELECT key, value FROM world_facts WHERE save_id=?", (save_id,)
    ).fetchall()
    for f in facts:
        parts.append(f"{f['key']}：{f['value']}")
    return "\n".join(parts)


def _matched_lore(conn, save_id):
    """扫描最近 3-5 条对话（不含已注入设定文本），命中非 constant 世界书条目。"""
    recent = memory.recent_turns(conn, save_id, 5)
    if not recent:
        return ""
    text = " ".join(t["content"] for t in recent)
    rows = conn.execute(
        "SELECT keywords, content, constant FROM lorebook WHERE save_id=? AND constant=0",
        (save_id,),
    ).fetchall()
    hits = []
    for r in rows:
        try:
            kws = json.loads(r["keywords"])
        except Exception:  # noqa: BLE001
            continue
        if any(kw and kw in text for kw in kws):
            hits.append(r["content"])
    return "\n".join(hits)


def _party_status(conn, save_id):
    """队伍状态一行：玩家 + 队友。"""
    from . import companions
    lines = []
    player = conn.execute(
        "SELECT name, level, current_hp, max_hp, current_terrain FROM player_state WHERE save_id=?",
        (save_id,),
    ).fetchone()
    if player:
        lines.append(
            f"你：{player['name']}(Lv{player['level']},HP {player['current_hp']}/{player['max_hp']},"
            f"地形:{player['current_terrain']})"
        )
    comp = companions.get_active_companions(conn, save_id)
    for c in comp:
        status = "良好" if c["current_hp"] > c["max_hp"] * 0.5 else "受伤"
        lines.append(f"{c['name']}(HP {c['current_hp']}/{c['max_hp']},{status})")
    if comp:
        lines.append(companions.build_relations_summary(conn, save_id))
    return "；".join(lines)