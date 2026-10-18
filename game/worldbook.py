"""世界书生成模块。

仅在新游戏开局调用一次：
- 输入二选一：用户描述世界设定；或用户输入关键词从预设 Wiki 搜索。
- 用 Flash JSON Output 生成 5-8 条 lorebook 条目写入当前存档。
- 代码过滤关键词（长度、黑名单、同存档不重复）。
- 顺带生成 2-3 名候选队友写入 companions。
"""
import json
import logging
import re
import urllib.parse
import urllib.request

from . import config, db, llm

log = logging.getLogger("game.worldbook")

WORLDBOOK_SYSTEM = (
    "你是 D&D 世界书编撰者。根据给定的世界设定或 Wiki 资料，"
    "提炼出 5-8 条『世界书』条目。每条包含："
    "keywords（2-4 个专有名词组成的数组）、content（3-5 句背景描述）、"
    "constant（布尔，是否常驻注入）。只输出 JSON 对象，格式为："
    '{"entries":[{"keywords":[...],"content":"...","constant":true}]}。'
)

COMPANION_SYSTEM = (
    "你是 D&D 角色创建者。基于给定的世界设定，创建 2-3 名候选队友。"
    "每位队友包含：name、race、class、personality（性格）、speech_style（说话风格）、"
    "background（背景）、stats（含 str/dex/con/int/wis/cha，范围 8-18）、"
    "max_hp、default_weapon。只输出 JSON 对象，格式为："
    '{"companions":[{"name":"...","race":"...","class":"...","personality":"...",'
    '"speech_style":"...","background":"...","stats":{"str":10,"dex":10,"con":10,'
    '"int":10,"wis":10,"cha":10},"max_hp":15,"default_weapon":"..."}]}。'
)

# ---- 预设默认条目（兜底） ----
DEFAULT_WORLDBOOK_ENTRIES = [
    {
        "keywords": ["无名大陆", "北方冻土"],
        "content": "这片大陆被人类称为无名大陆。北方是永冻的冰原，中部是葱郁的林地与开阔草原，"
                   "山岭与丘陵纵横交错，东西两侧靠海。",
        "constant": True,
    },
    {
        "keywords": ["古老的遗迹", "失落文明"],
        "content": "大陆各处散布着远古文明的遗迹，石环、神龛、废弃矿坑中仍能找到未被发掘的秘密。"
                   "传说这些文明曾掌握远超当世的力量。",
        "constant": False,
    },
    {
        "keywords": ["旅行者", "商队"],
        "content": "旅行者与商队往来于草原与山地之间，他们交换情报与货物，偶尔需要护卫。"
                   "贸易路线时常因天气与野兽而中断。",
        "constant": False,
    },
]

DEFAULT_COMPANIONS = [
    {
        "name": "艾琳", "race": "半精灵", "class": "游侠", "personality": "冷静而敏锐，话不多但观察入微。",
        "speech_style": "简短、直接，多用短句。", "background": "曾在北地猎手团中长大，擅长野外生存。",
        "stats": {"str": 12, "dex": 16, "con": 13, "int": 10, "wis": 15, "cha": 11}, "max_hp": 16,
        "default_weapon": "长弓",
    },
    {
        "name": "布隆", "race": "矮人", "class": "战士", "personality": "豪爽耿直，忠诚，爱喝酒。",
        "speech_style": "粗犷、热情，常带矮人口头禅。", "background": "曾是商队护卫，因故独自游历。",
        "stats": {"str": 17, "dex": 11, "con": 16, "int": 9, "wis": 12, "cha": 10}, "max_hp": 20,
        "default_weapon": "战斧",
    },
    {
        "name": "塞莉娅", "race": "人类", "class": "牧师", "personality": "温和耐心，坚定，有信仰。",
        "speech_style": "温柔、劝勉，引用教义。", "background": "来自南方教团，为寻找圣物而北上。",
        "stats": {"str": 10, "dex": 10, "con": 12, "int": 13, "wis": 16, "cha": 14}, "max_hp": 15,
        "default_weapon": "钉头锤",
    },
]


# ---- 关键词过滤 ----
def _clean_keywords(keywords, existing):
    """过滤关键词：长度>=2、不在黑名单、同存档不重复。"""
    seen = set(existing)
    result = []
    for kw in keywords:
        if not isinstance(kw, str):
            continue
        kw = kw.strip()
        if len(kw) < 2:
            continue
        if kw in config.KEYWORD_BLACKLIST:
            continue
        if kw in seen:
            continue
        seen.add(kw)
        result.append(kw)
    return result


# ---- Wiki 搜索 ----
def _http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "text-dnd-game/1.0"})
    with urllib.request.urlopen(req, timeout=config.LLM_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wiki_search_titles(api, keyword):
    """?action=query&list=search&srsearch=<kw>&format=json 取标题。返回标题列表。"""
    url = (
        api + "?action=query&list=search&srsearch=" + urllib.parse.quote(keyword) +
        "&format=json&srlimit=5"
    )
    data = _http_get_json(url)
    hits = data.get("query", {}).get("search", [])
    return [h.get("title") for h in hits if h.get("title")]


def wiki_fetch_extract(api, title):
    """?action=query&prop=extracts&titles=<标题>&format=json 取正文，截前 2000-3000 字符。"""
    url = (
        api + "?action=query&prop=extracts&exintro=1&explaintext=1&titles=" +
        urllib.parse.quote(title) + "&format=json"
    )
    data = _http_get_json(url)
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        extract = page.get("extract") or ""
        if extract:
            return extract[:config.WORLDBOOK_WIKI_EXTRACT_CHARS]
    return ""


def search_wiki(api, keyword, limit=3):
    """搜索关键词并拼接若干条目正文。返回一段文本。"""
    try:
        titles = wiki_search_titles(api, keyword)
        chunks = []
        for title in titles[:limit]:
            text = wiki_fetch_extract(api, title)
            if text:
                chunks.append(f"[{title}]\n{text}")
        return "\n\n".join(chunks)
    except Exception as e:  # noqa: BLE001
        log.warning("Wiki 搜索失败: %s", e)
        return ""


# ---- 生成 ----
def generate_worldbook(save_id, world_description=None, wiki_api=None, wiki_keyword=None):
    """为新存档生成世界书条目并写入 lorebook。返回条目数。

    输入二选一：
      - world_description: 用户描述世界设定（优先）
      - wiki_api + wiki_keyword: 从 Wiki 搜索
    失败重试一次，仍失败用预设默认条目。
    """
    conn = db.connect()
    try:
        existing = _existing_keywords(conn, save_id)

        if world_description:
            source = "用户描述：\n" + world_description
        elif wiki_api and wiki_keyword:
            fetched = search_wiki(wiki_api, wiki_keyword)
            source = f"Wiki 关键词「{wiki_keyword}」资料：\n" + (fetched or "（未获取到内容）")
        else:
            source = "无额外设定，使用默认世界观。"

        entries = _llm_worldbook(source)
        if not entries:
            log.info("世界书 LLM 生成失败，使用预设默认条目。")
            entries = DEFAULT_WORLDBOOK_ENTRIES

        count = 0
        for ent in entries:
            keywords = _clean_keywords(ent.get("keywords", []), existing)
            if not keywords:
                continue
            content = (ent.get("content") or "").strip()
            if len(content) < 20:
                continue
            constant = 1 if ent.get("constant") else 0
            priority = ent.get("priority", 0)
            conn.execute(
                "INSERT INTO lorebook (save_id, keywords, content, constant, priority) VALUES (?,?,?,?,?)",
                (save_id, json.dumps(keywords, ensure_ascii=False), content, constant, priority),
            )
            existing.update(keywords)
            count += 1

        conn.commit()
        return count
    finally:
        conn.close()


def _llm_worldbook(source):
    """用 Flash 生成世界书条目；失败返回空列表。"""
    data = llm.json_completion(WORLDBOOK_SYSTEM, source, temperature=0.9)
    entries = data.get("entries")
    if isinstance(entries, list):
        return entries
    return []


def _existing_keywords(conn, save_id):
    """收集该存档已有关键词（含默认，防止重复）。"""
    seen = set()
    rows = conn.execute(
        "SELECT keywords FROM lorebook WHERE save_id=?", (save_id,)
    ).fetchall()
    for r in rows:
        try:
            seen.update(json.loads(r["keywords"]))
        except Exception:  # noqa: BLE001
            continue
    return seen


# ---- 候选队友 ----
def generate_companion_candidates(save_id, world_source=None):
    """生成 2-3 名候选队友写入 companions，返回候选 id 列表。"""
    conn = db.connect()
    try:
        source = world_source or "通用 D&D 冒险背景。"
        candidates = _llm_companions(source)
        if not candidates:
            log.info("队友 LLM 生成失败，使用预设默认队友。")
            candidates = DEFAULT_COMPANIONS

        ids = []
        for c in candidates[:3]:
            stats = c.get("stats", {})
            name = (c.get("name") or "").strip()
            if not name:
                continue
            cur = conn.execute(
                "INSERT INTO companions (name, race, class, personality, speech_style, background, "
                "str, dex, con, int, wis, cha, max_hp, default_weapon) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    name, c.get("race", ""), c.get("class", ""), c.get("personality", ""),
                    c.get("speech_style", ""), c.get("background", ""),
                    _stat(stats.get("str", 10)), _stat(stats.get("dex", 10)),
                    _stat(stats.get("con", 10)), _stat(stats.get("int", 10)),
                    _stat(stats.get("wis", 10)), _stat(stats.get("cha", 10)),
                    int(c.get("max_hp") or 15), c.get("default_weapon", ""),
                ),
            )
            ids.append(cur.lastrowid)
        conn.commit()
        return ids
    finally:
        conn.close()


def _stat(v):
    """将属性值限制在 3-20。"""
    try:
        return max(3, min(20, int(v)))
    except (TypeError, ValueError):
        return 10


def _llm_companions(source):
    data = llm.json_completion(COMPANION_SYSTEM, source, temperature=0.9)
    comps = data.get("companions")
    if isinstance(comps, list):
        return comps
    return []