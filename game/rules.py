"""规则引擎（MVP）：六维属性、d20 检定、简化战斗。

铁律：数值全部由代码计算，LLM 只负责叙事。
结构化的检定/战斗结果交给叙事模块。

数值计算与 Prompt 组装分离，便于单独测试本模块。
"""
import logging
import random

from . import config

log = logging.getLogger("game.rules")

STAT_NAMES = ["str", "dex", "con", "int", "wis", "cha"]
STAT_LABELS = {
    "str": "力量", "dex": "敏捷", "con": "体质",
    "int": "智力", "wis": "感知", "cha": "魅力",
}


def d20():
    return random.randint(1, 20)


def roll(die=20):
    return random.randint(1, die)


def mod(score):
    """D&D 属性修正 = (score - 10) // 2。"""
    return (score - 10) // 2


def check(stat_value, dc, advantage=False, disadvantage=False):
    """一次 d20 属性检定。返回结构化结果。"""
    rolls = [d20()]
    if advantage or disadvantage:
        rolls.append(d20())
    raw = max(rolls) if advantage else (min(rolls) if disadvantage else rolls[0])
    total = raw + mod(stat_value)
    success = total >= dc
    return {
        "roll": raw,
        "rolls": rolls,
        "stat_mod": mod(stat_value),
        "dc": dc,
        "total": total,
        "success": success,
    }


class Combatant:
    """战斗单元（玩家 / 队友 / 敌人）。"""

    def __init__(self, name, hp, ac=None, attack_bonus=None, stats=None):
        self.name = name
        self.hp = hp
        self.max_hp = hp
        self.ac = ac if ac is not None else config.COMBAT_AC
        self.attack_bonus = attack_bonus if attack_bonus is not None else config.COMBAT_ATTACK_BONUS
        self.stats = stats or dict(config.DEFAULT_PLAYER_STATS)
        self.consecutive_miss = 0  # 供“三连空”梗
        self.is_player = False
        self.is_companion = False


def attack(attacker, target, damage_dice=(1, 8), extra=0):
    """一次攻击：先攻检定，命中后伤害。返回结构化 JSON。"""
    roll_val = d20()
    total = roll_val + attacker.attack_bonus
    hit = total >= target.ac
    damage = 0
    if hit:
        damage = _roll_dice(*damage_dice) + extra
        target.hp = max(0, target.hp - damage)

    if hit:
        attacker.consecutive_miss = 0
    else:
        attacker.consecutive_miss += 1

    return {
        "action": "attack",
        "attacker": attacker.name,
        "target": target.name,
        "roll": roll_val,
        "attack_total": total,
        "ac": target.ac,
        "hit": hit,
        "damage": damage,
        "target_hp": target.hp,
    }


def _roll_dice(count, sides):
    return sum(random.randint(1, sides) for _ in range(count))


def heal(target, amount):
    """治疗，不超 max_hp。返回结构化结果。"""
    before = target.hp
    target.hp = min(target.max_hp, target.hp + amount)
    return {
        "action": "heal",
        "target": target.name,
        "amount": amount,
        "hp_before": before,
        "hp_after": target.hp,
    }


def companion_ai_action(companion_state, enemies):
    """队友由代码控制行动的简单策略。

    策略：HP < 30% 优先撤退/治疗；否则攻击最近敌人。
    返回结构化 JSON（attack / retreat / heal）。
    """
    combatant, stats = companion_state
    hp_ratio = combatant.hp / combatant.max_hp if combatant.max_hp else 0

    if hp_ratio < 0.30 and enemies:
        # 撤退：离开战斗
        return {
            "action": "retreat",
            "actor": combatant.name,
            "reason": "生命值过低，选择撤退",
        }

    if enemies:
        # 攻击最近敌人
        target = enemies[0]
        return attack(combatant, target)

    # 无敌人，尝试自我治疗（这里只给叙事结果，不实际回血除非规则允许）
    return {
        "action": "hold",
        "actor": combatant.name,
        "reason": "无敌人且状态稳定，保持戒备",
    }