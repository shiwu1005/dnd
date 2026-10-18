"""全局配置：常量、环境变量、预设 Wiki 列表、黑名单等。"""
import os
import sys

# ---- DeepSeek ----
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
LLM_TIMEOUT_SECONDS = float(os.environ.get("LLM_TIMEOUT_SECONDS", "60"))
LLM_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "2"))

# 叙事系统提示词（固定，用于前缀缓存）
NARRATOR_SYSTEM_PROMPT = (
    "你是文字 D&D 游戏的叙述者。根据给定事实生成 2-3 句沉浸式描写，"
    "并提供 2-3 个行动选项。不得改变任何数值，不得宣布规则未产生的结果。输出 JSON。"
)

# ---- 打包 / 运行环境 ----
def _app_dir():
    """返回应用所在目录（含 PyInstaller 冻结场景）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


APP_DIR = _app_dir()

# ---- 数据库 ----
# 冻结环境下 DB 放在可执行文件旁，便于用户访问。
DB_PATH = os.environ.get(
    "GAME_DB_PATH",
    os.path.join(APP_DIR, "game.db"),
)

# ---- 预设 Wiki（用于世界书生成搜索）----
PRESET_WIKIS = {
    "dnd_cn": {"label": "D&D 中文 Wiki（灰机）", "api": "https://dnd.huijiwiki.com/api.php"},
    "forgottenrealms": {"label": "被遗忘国度（英文）", "api": "https://forgottenrealms.fandom.com/api.php"},
    "criticalrole": {"label": "Critical Role 设定（英文）", "api": "https://criticalrole.fandom.com/api.php"},
    "custom": {"label": "自定义（用户填 api.php）", "api": ""},
}

# ---- 关键词过滤黑名单 ----
KEYWORD_BLACKLIST = {
    "港口", "商会", "铁匠", "城镇", "迷雾", "战斗", "过去", "房间", "老师", "决定",
}

# ---- 摘要 / 对话窗口 ----
SUMMARY_EVERY_MIN = 15
SUMMARY_EVERY_MAX = 20
SUMMARY_LENGTH_MIN = 200
SUMMARY_LENGTH_MAX = 300
RECENT_TURNS_MIN = 5
RECENT_TURNS_MAX = 8
SUMMARY_TOTAL_MAX_CHARS = 500

# ---- 事件 ----
SCENE_CHANGE_EVENT_CHANCE = 0.30
TURN_END_EVENT_CHANCE_MIN = 0.08
TURN_END_EVENT_CHANCE_MAX = 0.12

# ---- 队友 ----
COMPANION_PASSIVE_CHANCE_MIN = 0.05
COMPANION_PASSIVE_CHANCE_MAX = 0.08
COMPANION_AFFINITY_UP = 2      # 友好互动 +2
COMPANION_AFFINITY_DOWN = -3   # 摩擦互动 -3

# ---- 世界书 ----
WORLDBOOK_ENTRIES_MIN = 5
WORLDBOOK_ENTRIES_MAX = 8
WORLDBOOK_WIKI_EXTRACT_CHARS = 3000  # 截取前 2000-3000 字符
WORLDBOOK_CANDIDATE_COMPANIONS = 3   # 生成候选队友数

# ---- 玩家默认属性（无种族加成） ----
DEFAULT_PLAYER_STATS = {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10}
DEFAULT_PLAYER_MAX_HP = 20

# ---- 战斗 ----
COMBAT_ATTACK_BONUS = 2       # 攻击加值简化
COMBAT_AC = 12                # 敌人基础护甲