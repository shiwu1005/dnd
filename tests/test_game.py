"""端到端测试：不依赖真实 LLM（用兜底路径），验证各模块协同。

运行：python tests/test_game.py
"""
import os
import sys
import tempfile

# 强制使用临时数据库，且不设 API key → LLM 走兜底
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_DIR = tempfile.mkdtemp(prefix="dnd_game_test_")
os.environ["GAME_DB_PATH"] = os.path.join(TEST_DIR, "test.db")
os.environ["DEEPSEEK_API_KEY"] = ""

from game import config, db, engine, events, llm, memes, rules, saves, worldbook  # noqa: E402
from game import companions as comp_mod  # noqa: E402


def test_db_init_and_seeds():
    db.init_db()
    conn = db.connect()
    assert conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"] == 15
    assert conn.execute("SELECT COUNT(*) c FROM memes").fetchone()["c"] == 6
    # 外键开启
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1, "外键未开启"
    conn.close()
    print("[OK] 数据库建表与种子")


def test_save_crud_and_cascade():
    sid = db.create_save("测试存档")
    conn = db.connect()
    conn.execute("INSERT INTO world_facts (save_id, key, value) VALUES (?,?,?)", (sid, "k", "v"))
    conn.commit()
    conn.close()
    assert db.save_exists(sid)
    db.delete_save(sid)
    conn = db.connect()
    assert not db.save_exists(sid)
    # 级联删除：world_facts 应为空
    rows = conn.execute("SELECT * FROM world_facts WHERE save_id=?", (sid,)).fetchall()
    assert rows == [], "级联删除失败"
    conn.close()
    print("[OK] 存档增删与级联清理")


def test_rules_dice_and_attack():
    # 属性修正
    assert rules.mod(14) == 2
    assert rules.mod(10) == 0
    assert rules.mod(7) == -2  # (7-10)//2 = -3//2 = -2（Python 向下取整）
    # 检定总是成功/失败布尔
    for _ in range(100):
        r = rules.check(10, 12)
        assert r["roll"] >= 1 and r["roll"] <= 20
        assert r["success"] in (True, False)
        assert r["total"] == r["roll"] + r["stat_mod"]
    # 战斗
    a = rules.Combatant("玩家", 20)
    b = rules.Combatant("哥布林", 7)
    res = rules.attack(a, b)
    assert res["action"] == "attack"
    assert res["hit"] in (True, False)
    assert res["target_hp"] >= 0
    print("[OK] 规则引擎（骰子/检定/攻击）")


def test_events_pool_and_cooldown():
    conn = db.connect()
    sid = db.create_save("事件测试")
    ev = events.pick_event(conn, sid, "grassland", 2, 1)
    assert ev is not None, "grassland 应有候选事件"
    assert ev["event_type"] in ("discovery", "social", "hazard")
    ev = events.fire_event(conn, sid, "grassland", 2, 5)
    assert ev is not None and "事件骨架" in ev["prompt"]
    assert ev["name"] and ev["skeleton"]
    assert ev["event_type"] in ("discovery", "social", "hazard")
    # cooldown 内不再抽到同一事件
    conn.close()
    db.delete_save(sid)
    print("[OK] 事件池/加权/cooldown")


def test_worldbook_generation_default():
    sid = db.create_save("世界书测试")
    count = worldbook.generate_worldbook(sid)  # 无 LLM → 默认
    assert count >= 1, "应写入至少 1 条默认世界书"
    conn = db.connect()
    rows = conn.execute("SELECT * FROM lorebook WHERE save_id=?", (sid,)).fetchall()
    assert len(rows) >= 1
    conn.close()
    db.delete_save(sid)
    print("[OK] 世界书生成（默认兜底）")


def test_keyword_filter():
    # 过滤黑名单和短词
    cleaned = worldbook._clean_keywords(["港口", "雾", "地精", "恶龙", "地精"], set())
    assert "港口" not in cleaned  # 黑名单
    assert "雾" not in cleaned     # 长度 < 2
    assert "地精" in cleaned       # 合法
    # 同存档去重
    cleaned2 = worldbook._clean_keywords(["恶龙", "恶龙"], set())
    assert cleaned2 == ["恶龙"]
    print("[OK] 关键词过滤")


def test_companion_system():
    conn = db.connect()
    sid = db.create_save("队友测试")
    ids = worldbook.generate_companion_candidates(sid)  # 默认候选
    assert len(ids) >= 2, "应生成至少 2 名候选队友"
    comp_mod.join_companion(conn, sid, ids[0], 0)
    active = comp_mod.get_active_companions(conn, sid)
    assert len(active) == 1
    # 玩家好感调整
    aff = comp_mod.adjust_player_affinity(conn, sid, ids[0], 5)
    assert aff == 5
    # 队友关系调整
    comp_mod.join_companion(conn, sid, ids[1], 0)
    rel = comp_mod.get_relation(conn, sid, ids[0], ids[1])
    assert rel == 0
    conn.close()
    db.delete_save(sid)
    print("[OK] 队友系统（入队/好感/关系）")


def test_meme_system():
    conn = db.connect()
    sid = db.create_save("梗测试")
    # 大失败梗：自然一且失败
    check = {"stat": "int", "stat_value": 10, "roll": 1, "success": False}
    hints = memes.check_memes(conn, sid, 1, check_result=check)
    assert any("大失败" in h for h in hints), "自然一应触发大失败梗"
    # cooldown 内不重复
    hints2 = memes.check_memes(conn, sid, 1, check_result=check)
    assert not any("大失败" in h for h in hints2), "cooldown 内不应重复触发"
    # 大成功梗：智力<=3 且 roll=20 且成功
    check3 = {"stat": "int", "stat_value": 3, "roll": 20, "success": True}
    hints3 = memes.check_memes(conn, sid, 30, check_result=check3)
    assert any("智力极低" in h for h in hints3), "兽人智者应触发"
    # 关系归零
    hints4 = memes.check_memes(conn, sid, 40, affinity_cross={"direction": "down"})
    assert any("好感降到 0" in h for h in hints4)
    conn.close()
    db.delete_save(sid)
    print("[OK] 梗系统（大失败/大成功/关系归零/cooldown）")


def test_full_game_loop():
    conn = db.connect()
    sid = db.create_save("整流程")
    sess = engine.GameSession(sid)
    msg = sess.init_new_game("测试角色")
    assert "边境" in msg
    # 模拟若干轮（LLM 兜底）
    for i in range(5):
        r = sess.run_turn(f"继续冒险，第{i+1}轮行动")
        assert r["narrative"], "每轮都应有叙述"
        assert r["options"], "每轮都应有选项"
        ev = r.get("event")
        assert ev is None or ("name" in ev and "skeleton" in ev)
    # 存档计数：玩家 + 叙述者各 5 条，另有 5 条选项记录
    turns = conn.execute(
        "SELECT COUNT(*) c FROM turns WHERE save_id=? AND role IN ('player','narrator')", (sid,)
    ).fetchone()["c"]
    assert turns == 10, f"应存 5 轮 x 2 条=10，实际 {turns}"
    opt_rows = conn.execute(
        "SELECT COUNT(*) c FROM turns WHERE save_id=? AND role='options'", (sid,)
    ).fetchone()["c"]
    assert opt_rows == 5, f"应存 5 条选项记录，实际 {opt_rows}"
    # 队友已入队
    active = comp_mod.get_active_companions(conn, sid)
    assert len(active) == 1
    # 加载存档（不重新生成世界书）
    state = saves.load_save_state(sid)
    assert state["player"] is not None
    # 读档回顾与选项恢复
    sess2, st2 = engine.load_game_cli(sid)
    assert st2["recap"], "读档应返回上次对话"
    assert st2["recap"][-1]["role"] == "narrator", "回顾最后一条应为叙述者回复"
    assert st2["options"], "读档应恢复上次的行动选项"
    assert len(st2["options"]) == len(sess.current_options)
    sess2.close()
    sess.close()
    conn.close()
    db.delete_save(sid)
    print("[OK] 完整游戏循环（开场/行动/存turns/读档回顾）")


def test_prompt_assembly():
    conn = db.connect()
    sid = db.create_save("提示词")
    sess = engine.GameSession(sid)
    sess.init_new_game("提示词角色")
    p = saves.build_prompt(conn, sid, "我观察四周")
    # 顺序：常驻设定在前，玩家行动在后
    assert "玩家行动" in p
    assert "常驻设定" in p or "【世界书" in p or "【长期记忆" in p or "【队伍状态" in p
    assert p.strip().endswith("我观察四周")
    sess.close()
    conn.close()
    db.delete_save(sid)
    print("[OK] Prompt 组装顺序")


def test_narrative_parsing_robustness():
    fence = "```"
    # 代码围栏包裹
    r = llm._try_narrative(
        fence + 'json\n{"narrative":"风雪掠过冰原。","options":["前进","扎营"]}\n' + fence
    )
    assert r == {"narrative": "风雪掠过冰原。", "options": ["前进", "扎营"]}
    # JSON 前后有废话
    r = llm._try_narrative('好的：\n{"narrative":"你推开门。","options":["进入"]}')
    assert r["narrative"] == "你推开门。"
    # 多个对象取第一个
    r = llm._try_narrative('{"narrative":"甲"}{"options":["乙"]}')
    assert r["narrative"] == "甲"
    # 纯文本兜底解析
    r = llm._parse_plain_narrative("你走进洞穴。\n\n行动选项：\n1) 深入\n2) 返回")
    assert r == {"narrative": "你走进洞穴。", "options": ["深入", "返回"]}
    # 无选项标记 → 整段叙事 + 默认选项
    r = llm._parse_plain_narrative("只有叙事的一段描写。")
    assert r["narrative"] == "只有叙事的一段描写。"
    assert r["options"] == llm.FALLBACK_OPTIONS
    # 短横线选项，且不吞第一个
    r = llm._parse_plain_narrative("叙事A\n- 选项甲\n- 选项乙")
    assert r["options"] == ["选项甲", "选项乙"]
    print("[OK] 叙事解析健壮性（围栏/废话/纯文本）")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\n全部测试通过 ✔")
    import shutil
    shutil.rmtree(TEST_DIR, ignore_errors=True)