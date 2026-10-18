"""游戏主循环：把各模块串联起来。

- 交互层（CLI）与核心逻辑解耦：本模块只暴露 GameSession 与命令函数。
- 每轮：组装 Prompt → LLM 叙事 → 存 turns → 摘要 → 事件 → 队友互动 → 梗。
"""
import logging
import random

from . import config, db, events, llm, memory, rules, saves
from . import companions as comp_mod
from . import memes as meme_mod
from . import worldbook

log = logging.getLogger("game.engine")


class GameSession:
    """一个进行中的游戏会话（绑定某个存档）。"""

    def __init__(self, save_id, conn=None):
        self.save_id = save_id
        self._own_conn = conn is None
        self.conn = conn or db.connect()
        self.turn_number = self._count_turns()
        self.current_combat = None  # 简化：当前战斗敌人列表（含 HP）

    def _count_turns(self):
        return self.conn.execute(
            "SELECT COUNT(*) AS c FROM turns WHERE save_id=? AND role='player'",
            (self.save_id,),
        ).fetchone()["c"]

    def close(self):
        if self._own_conn:
            self.conn.close()

    # ---- 新游戏初始化 ----
    def init_new_game(self, name, world_description=None, wiki_api=None, wiki_keyword=None,
                      companion_id=None):
        """新开局：初始化玩家、生成世界书、入队队友。返回开场消息。"""
        # 玩家
        self.conn.execute(
            "INSERT OR REPLACE INTO player_state "
            "(save_id, name, race, klass, level, str, dex, con, int, wis, cha, current_hp, max_hp, current_terrain) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                self.save_id, name, "", "", 1,
                config.DEFAULT_PLAYER_STATS["str"], config.DEFAULT_PLAYER_STATS["dex"],
                config.DEFAULT_PLAYER_STATS["con"], config.DEFAULT_PLAYER_STATS["int"],
                config.DEFAULT_PLAYER_STATS["wis"], config.DEFAULT_PLAYER_STATS["cha"],
                config.DEFAULT_PLAYER_MAX_HP, config.DEFAULT_PLAYER_MAX_HP,
                "grassland",
            ),
        )
        self.conn.commit()

        # 生成世界书（失败用默认）
        count = worldbook.generate_worldbook(
            self.save_id,
            world_description=world_description,
            wiki_api=wiki_api,
            wiki_keyword=wiki_keyword,
        )
        log.info("世界书生成 %d 条", count)

        # 生成候选队友
        cand = worldbook.generate_companion_candidates(self.save_id)
        if companion_id is None:
            companion_id = cand[0] if cand else None
        if companion_id:
            comp_mod.join_companion(self.conn, self.save_id, companion_id, 0)

        return self._opening_message()

    def _opening_message(self):
        return (
            "你站在无名大陆的边境。远方是待探索的原野、森林与山脉。"
            "冒险者们的故事正等待你亲手写下。"
        )

    # ---- 玩家主动交谈 ----
    def talk_companion(self, companion_id):
        """玩家主动与某队友交谈。返回注入 Prompt 片段。"""
        c = self.conn.execute("SELECT * FROM companions WHERE id=?", (companion_id,)).fetchone()
        if c is None:
            return None
        aff = comp_mod.get_player_affinity(self.conn, self.save_id, companion_id)
        mood = "亲近" if aff > 15 else ("冷淡" if aff < -10 else "中性")
        return (
            f"你正与 {c['name']} 交谈（好感:{aff}，态度:{mood}）。"
            f"性格：{c['personality']}。说话风格：{c['speech_style']}。背景：{c['background']}。"
        )

    # ---- 一轮 ----
    def run_turn(self, player_input, talk_companion_id=None):
        """处理一轮玩家输入，返回叙述结果 dict。

        返回: {"narrative": str, "options": [str,...], "event_hint": str|None,
               "companion_interaction": str|None, "meme_hints":[str] or None}
        """
        self.turn_number += 1

        # 清洗玩家输入：控制台可能产生孤立代理字符，既不能存库也不能发请求
        from .textutil import safe_text
        player_input = safe_text(player_input)

        # 玩家主动交谈注入
        talk = None
        if talk_companion_id:
            talk = self.talk_companion(talk_companion_id)

        # 组装 Prompt（不含事件/梗，先判定事件和梗）
        prompt = saves.build_prompt(
            self.conn, self.save_id, player_input, talk_companion=talk,
        )
        result = llm.narrate(prompt)

        # 存 turns（玩家 + 叙述者）
        self._store_turn("player", player_input)
        self._store_turn("narrator", result["narrative"])
        db.touch_save(self.save_id)

        # 摘要
        if memory.maybe_trigger_summary(self.conn, self.save_id):
            memory.create_summary(self.conn, self.save_id)

        # 事件触发（每轮结束 8%-12%）
        event_hint = None
        player = self._player()
        if events.roll_turn_end():
            event_hint = events.fire_event(
                self.conn, self.save_id, player["current_terrain"], player["level"], self.turn_number
            )

        # 队友被动互动（5%-8%）
        comp_interaction = None
        if player and self._has_active_companions():
            comp_interaction = comp_mod.maybe_passive_interaction(
                self.conn, self.save_id, self.turn_number
            )

        result["event_hint"] = event_hint
        result["companion_interaction"] = comp_interaction
        return result

    def _store_turn(self, role, content):
        from datetime import datetime
        from .textutil import safe_text
        now = datetime.now().isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO turns (save_id, role, content, created_at) VALUES (?,?,?,?)",
            (self.save_id, role, safe_text(content), now),
        )
        self.conn.commit()

    def _player(self):
        row = self.conn.execute(
            "SELECT * FROM player_state WHERE save_id=?", (self.save_id,)
        ).fetchone()
        return dict(row) if row else None

    def _has_active_companions(self):
        return comp_mod.get_active_companions(self.conn, self.save_id)


# ---- 命令行辅助 ----
def list_saves_cli():
    return db.list_saves()


def delete_save_cli(save_id):
    db.delete_save(save_id)


def new_game_cli(name, world_description=None, wiki=None, keyword=None, choose_companion=None):
    """命令行新建游戏。返回 GameSession。"""
    sid = db.create_save(name)
    sess = GameSession(sid)
    cand = worldbook.generate_companion_candidates(sid)
    chosen = choose_companion or (cand[0] if cand else None)
    msg = sess.init_new_game(name, world_description, wiki, keyword, chosen)
    return sess, msg


def load_game_cli(save_id):
    """命令行加载存档。返回 GameSession。"""
    state = saves.load_save_state(save_id)
    if state is None:
        return None, None
    sess = GameSession(save_id)
    return sess, state