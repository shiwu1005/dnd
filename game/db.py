"""SQLite 数据库：连接、建表、种子数据。每次连接后执行 PRAGMA foreign_keys = ON。"""
import json
import os
import sqlite3

from . import config

# ---- 事件种子（14+1 条） ----
EVENT_SEEDS = [
    ("冰封湖泊", "discovery", '["arctic"]', 11, 16,
     "一片广阔的冻结湖面出现在前方，冰层下隐约可见巨大生物的轮廓，被永远封在冰里。", 2, 20),
    ("垮塌冰城堡", "discovery", '["arctic"]', 11, 16,
     "一座半塌的冰造城堡，散落着蓝色皮肤类人生物的尸体，似乎发生过激烈战斗。", 2, 20),
    ("暴风雪", "hazard", '["arctic"]', 11, 16,
     "突如其来的暴风雪，视野骤降至数尺，持续数小时，方向感被完全剥夺。", 3, 10),
    ("冰上裂缝", "hazard", '["arctic"]', 11, 16,
     "冰面出现延伸数里的巨大裂缝，深处传来风声，通过需要谨慎。", 2, 15),
    ("孤独的冰钓者", "social", '["arctic"]', 11, 16,
     "一个在冰面上独自垂钓的身影，对来者既警惕又好奇。", 1, 25),
    ("林地神龛", "discovery", '["forest"]', 5, 10,
     "一座小型林地神龛，供奉名为 Siswa 的神秘教派，周围有新鲜的祭品。", 2, 20),
    ("古树空心", "discovery", '["forest"]', 5, 10,
     "一棵巨树的空心内部，空间足以容纳数人过夜，内壁有旧刻痕。", 2, 15),
    ("立石圆环", "discovery", '["grassland"]', 1, 4,
     "一圈立石，环内空气完全静止，连风都绕行。", 2, 20),
    ("游牧商队", "social", '["grassland"]', 1, 4,
     "一支缓慢移动的游牧商队，愿意用货物交换情报或保护。", 2, 25),
    ("山间隘口", "hazard", '["hill"]', 1, 10,
     "狭窄的隘口，两侧岩壁陡峭，落石风险高。", 2, 15),
    ("废弃矿坑", "discovery", '["hill"]', 1, 10,
     "一个被遗弃的矿坑入口，木支架腐朽，深处有微光。", 2, 20),
    ("高山垭口", "hazard", '["mountain"]', 1, 10,
     "海拔极高的垭口，空气稀薄，风雪随时可能封路。", 2, 15),
    ("鹰巢", "discovery", '["mountain"]', 1, 10,
     "悬崖上一处巨大的鹰巢，巢中有反光的物件，但接近需要攀爬。", 1, 20),
    ("旅人求助", "social", '["arctic","forest","grassland","hill","mountain"]', 1, 20,
     "一名疲惫的旅人拦路求助，声称自己与同伴走散，但言辞间有些闪烁。", 2, 20),
    ("天气突变", "hazard", '["forest","grassland","hill","mountain"]', 1, 20,
     "天气骤然恶化，能见度下降，队伍需要决定是继续前进还是就地扎营。", 2, 10),
]

# ---- 梗种子（6 条） ----
MEME_SEEDS = [
    ("兽人智者", '{"stat":"int","stat_value_max":3,"check":"success","roll":20}',
     "该角色智力极低却在智力检定中掷出大成功。用荒诞而克制的笔调描写反差：他误打误撞得出正确答案，自己也不明白为什么。不要让他突然变聪明。", 30, 1),
    ("魅力一诗人", '{"stat":"cha","stat_value_max":3,"check":"success","roll":20}',
     "该角色魅力极低却在魅力检定中大成功。对方被他的真诚到离谱打动，而非被魅力吸引。保持荒诞感。", 30, 1),
    ("幸运摔跤", '{"stat":"dex","stat_value_max":3,"check":"success","roll":20}',
     "该角色敏捷极低却在敏捷检定中大成功。他摔了一跤恰好躲过危险，自己却以为是技术高超。", 30, 1),
    ("自然一", '{"stat":"any","check":"fail","roll":1}',
     "大失败。用幽默笔调描写一次惨烈但无害的失败，不造成致命后果，不叠加规则惩罚。", 20, 1),
    ("三连空", '{"same_actor":"true","consecutive_miss":3}',
     "同一角色连续三轮攻击未命中。描写他开始怀疑人生，或武器“似乎跟他有仇”，纯喜剧，不改变数值。", 30, 1),
    ("关系归零", '{"affinity_cross":0,"direction":"down"}',
     "某队友对玩家好感降到 0。描写一次“我们只是同事”式的尴尬疏离，语气平淡但好笑。", 40, 1),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS saves (
    save_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lorebook (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    save_id     INTEGER NOT NULL REFERENCES saves(save_id) ON DELETE CASCADE,
    keywords    TEXT NOT NULL,           -- JSON 数组字符串
    content     TEXT NOT NULL,
    constant    INTEGER NOT NULL DEFAULT 0,
    priority    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS world_facts (
    save_id     INTEGER NOT NULL REFERENCES saves(save_id) ON DELETE CASCADE,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    PRIMARY KEY (save_id, key)
);

CREATE TABLE IF NOT EXISTS summaries (
    save_id     INTEGER NOT NULL REFERENCES saves(save_id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    content     TEXT NOT NULL,
    PRIMARY KEY (save_id, seq)
);

CREATE TABLE IF NOT EXISTS turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    save_id     INTEGER NOT NULL REFERENCES saves(save_id) ON DELETE CASCADE,
    role        TEXT NOT NULL,           -- 'player' / 'narrator'
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    event_type      TEXT NOT NULL CHECK (event_type IN ('combat','discovery','social','hazard','lore')),
    terrain         TEXT NOT NULL,       -- JSON 数组字符串
    level_min       INTEGER NOT NULL DEFAULT 1,
    level_max       INTEGER NOT NULL DEFAULT 20,
    skeleton        TEXT NOT NULL,
    weight          INTEGER NOT NULL DEFAULT 1,
    cooldown        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS event_history (
    save_id     INTEGER NOT NULL REFERENCES saves(save_id) ON DELETE CASCADE,
    event_id    INTEGER NOT NULL REFERENCES events(id),
    fired_turn  INTEGER NOT NULL,
    PRIMARY KEY (save_id, event_id, fired_turn)
);

CREATE TABLE IF NOT EXISTS companions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    race            TEXT NOT NULL,
    class           TEXT NOT NULL,
    personality     TEXT NOT NULL,
    speech_style    TEXT NOT NULL,
    background      TEXT NOT NULL,
    str INTEGER, dex INTEGER, con INTEGER,
    int INTEGER, wis INTEGER, cha INTEGER,
    max_hp          INTEGER NOT NULL,
    default_weapon  TEXT
);

CREATE TABLE IF NOT EXISTS companion_state (
    save_id         INTEGER NOT NULL REFERENCES saves(save_id) ON DELETE CASCADE,
    companion_id    INTEGER NOT NULL REFERENCES companions(id),
    current_hp      INTEGER NOT NULL,
    joined_turn     INTEGER NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (save_id, companion_id)
);

CREATE TABLE IF NOT EXISTS companion_relations (
    save_id         INTEGER NOT NULL REFERENCES saves(save_id) ON DELETE CASCADE,
    companion_a     INTEGER NOT NULL REFERENCES companions(id),
    companion_b     INTEGER NOT NULL REFERENCES companions(id),
    affinity        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (save_id, companion_a, companion_b)
);

CREATE TABLE IF NOT EXISTS companion_player_affinity (
    save_id         INTEGER NOT NULL REFERENCES saves(save_id) ON DELETE CASCADE,
    companion_id    INTEGER NOT NULL REFERENCES companions(id),
    affinity        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (save_id, companion_id)
);

CREATE TABLE IF NOT EXISTS companion_interactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    save_id         INTEGER NOT NULL REFERENCES saves(save_id) ON DELETE CASCADE,
    companion_a     INTEGER NOT NULL,
    companion_b     INTEGER NOT NULL,
    turn            INTEGER NOT NULL,
    summary         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    trigger         TEXT NOT NULL,       -- JSON 触发条件
    prompt_hint     TEXT NOT NULL,
    cooldown        INTEGER NOT NULL DEFAULT 0,
    weight          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS meme_history (
    save_id     INTEGER NOT NULL REFERENCES saves(save_id) ON DELETE CASCADE,
    meme_id     INTEGER NOT NULL REFERENCES memes(id),
    fired_turn  INTEGER NOT NULL,
    PRIMARY KEY (save_id, meme_id, fired_turn)
);

CREATE TABLE IF NOT EXISTS player_state (
    save_id         INTEGER PRIMARY KEY REFERENCES saves(save_id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    race            TEXT NOT NULL DEFAULT '',
    klass           TEXT NOT NULL DEFAULT '',
    level           INTEGER NOT NULL DEFAULT 1,
    str INTEGER, dex INTEGER, con INTEGER,
    int INTEGER, wis INTEGER, cha INTEGER,
    current_hp      INTEGER NOT NULL,
    max_hp          INTEGER NOT NULL,
    current_terrain  TEXT NOT NULL DEFAULT 'grassland',
    gold            INTEGER NOT NULL DEFAULT 0
);
"""


def connect(db_path=None):
    """建立到 SQLite 的连接，并确保外键约束开启。"""
    path = db_path or config.DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path=None):
    """建表并写入种子数据（仅当相应表为空）。"""
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        # 事件种子
        if conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"] == 0:
            conn.executemany(
                "INSERT INTO events (name, event_type, terrain, level_min, level_max, skeleton, weight, cooldown) "
                "VALUES (?,?,?,?,?,?,?,?)",
                EVENT_SEEDS,
            )
        # 梗种子
        if conn.execute("SELECT COUNT(*) AS c FROM memes").fetchone()["c"] == 0:
            conn.executemany(
                "INSERT INTO memes (name, trigger, prompt_hint, cooldown, weight) VALUES (?,?,?,?,?)",
                MEME_SEEDS,
            )
        conn.commit()
    finally:
        conn.close()


def create_save(name, db_path=None):
    """新建一个存档，返回 save_id。"""
    conn = connect(db_path)
    try:
        now = _now()
        cur = conn.execute(
            "INSERT INTO saves (name, created_at, updated_at) VALUES (?,?,?)",
            (name, now, now),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _now():
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def touch_save(save_id, db_path=None):
    """更新存档 updated_at。"""
    conn = connect(db_path)
    try:
        conn.execute("UPDATE saves SET updated_at=? WHERE save_id=?", (_now(), save_id))
        conn.commit()
    finally:
        conn.close()


def list_saves(db_path=None):
    """列出所有存档。"""
    conn = connect(db_path)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT save_id, name, created_at, updated_at FROM saves ORDER BY save_id"
        ).fetchall()]
    finally:
        conn.close()


def delete_save(save_id, db_path=None):
    """删除存档，依赖外键 ON DELETE CASCADE 清理关联数据。"""
    conn = connect(db_path)
    try:
        conn.execute("DELETE FROM saves WHERE save_id=?", (save_id,))
        conn.commit()
    finally:
        conn.close()


def save_exists(save_id, db_path=None):
    conn = connect(db_path)
    try:
        return conn.execute("SELECT 1 FROM saves WHERE save_id=?", (save_id,)).fetchone() is not None
    finally:
        conn.close()