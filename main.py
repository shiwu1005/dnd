"""命令行交互入口（文字 D&D 游戏）。

用法：
    python main.py              进入主菜单（新建/加载/删除/列表）
    python main.py --new 名字    直接新建
"""
import argparse
import logging
import os
import sys

# 确保可独立运行
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from game import config, db, engine, worldbook  # noqa: E402
from game.textutil import safe_text, setup_console  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# 抑制底层 HTTP 客户端的逐请求日志，保持界面清爽
for _noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "openai"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# Windows 下切换到 UTF-8 控制台代码页，避免中文乱码/代理字符
setup_console()


class _Exit(Exception):
    """输入流结束或用户中止时用于干净退出。"""


def ask(text):
    """安全的输入封装：EOF / Ctrl+C 时抛出 _Exit，避免打印 traceback。"""
    try:
        return safe_text(input(text)).strip()
    except (EOFError, KeyboardInterrupt):
        raise _Exit from None


def banner():
    print("=" * 50)
    print("  文字 D&D 冒险（DeepSeek 驱动）")
    print("=" * 50)


def main_menu():
    while True:
        print("\n主菜单：")
        print("  1) 新建游戏")
        print("  2) 加载存档")
        print("  3) 列出存档")
        print("  4) 删除存档")
        print("  0) 退出")
        choice = ask("请选择：")
        if choice == "1":
            new_game_flow()
        elif choice == "2":
            load_game_flow()
        elif choice == "3":
            list_saves_flow()
        elif choice == "4":
            delete_save_flow()
        elif choice == "0":
            break


def new_game_flow():
    name = ask("存档名称：") or "无名冒险"
    print("\n世界设定来源（二选一）：")
    print("  1) 输入你自己的世界描述")
    print("  2) 从预设 Wiki 搜索关键词")
    src = ask("选择(1/2)：")
    world_desc = None
    wiki_api = None
    wiki_kw = None
    if src == "1":
        world_desc = ask("简要描述你的世界设定：") or None
    elif src == "2":
        print("可选 Wiki：")
        for k, w in config.PRESET_WIKIS.items():
            print(f"  {k}: {w['label']}")
        wkey = ask("输入 Wiki 键（如 dnd_cn / forgottenrealms / criticalrole / custom）：")
        wiki_cfg = config.PRESET_WIKIS.get(wkey)
        if wiki_cfg is None:
            print("无效 Wiki，使用默认世界观。")
        else:
            wiki_api = wiki_cfg["api"]
            if wkey == "custom":
                wiki_api = ask("输入自定义 api.php 地址：") or None
            wiki_kw = ask("输入搜索关键词：") or "dungeons dragons"
    print("\n正在生成世界书…（可能需要一点时间）")
    sess, msg = engine.new_game_cli(name, world_desc, wiki_api, wiki_kw)
    play_loop(sess, msg)


def load_game_flow():
    saves = db.list_saves()
    if not saves:
        print("暂无存档。")
        return
    for s in saves:
        print(f"  [{s['save_id']}] {s['name']} ({s['created_at']})")
    try:
        sid = int(ask("输入要加载的存档编号："))
    except ValueError:
        print("无效编号。")
        return
    sess, state = engine.load_game_cli(sid)
    if sess is None:
        print("存档不存在。")
        return
    play_loop(
        sess,
        f"继续你的冒险…（存档：{state['save']['name']}）",
        recap=state.get("recap"),
        options=state.get("options"),
    )


def list_saves_flow():
    saves = db.list_saves()
    if not saves:
        print("暂无存档。")
        return
    for s in saves:
        print(f"  [{s['save_id']}] {s['name']} (创建 {s['created_at']}, 更新 {s['updated_at']})")


def delete_save_flow():
    list_saves_flow()
    try:
        sid = int(ask("输入要删除的存档编号："))
    except ValueError:
        print("无效编号。")
        return
    db.delete_save(sid)
    print("已删除。")


def print_options(options):
    if not options:
        return
    print("\n行动选项：")
    for i, o in enumerate(options, 1):
        print(f"  {i}) {o}")


def print_recap(recap):
    """读档时显示上次的进度（玩家最后行动 + 叙述者最后回复）。"""
    if not recap:
        print("（这是新存档，还没有历史记录。）")
        return
    print("\n" + "=" * 50)
    print("　上次冒险回顾")
    print("=" * 50)
    for t in recap:
        if t["role"] == "player":
            print(f"\n你：{t['content']}")
        else:
            print(f"\n{t['content']}")
    print("\n" + "=" * 50)


def play_loop(sess, opening, recap=None, options=None):
    if recap is not None:
        print_recap(recap)
    else:
        print("\n" + opening)
    print("-" * 50)

    current_options = list(options) if options else list(getattr(sess, "current_options", []) or [])
    if recap is not None:
        print_options(current_options)
        print("（可输入编号选择，或直接输入你的行动）")

    while True:
        try:
            act = safe_text(input("你的行动：")).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not act:
            continue
        if act.lower() in ("退出", "quit", "exit"):
            print("冒险暂停。再见！")
            break
        if act.lower() in ("保存", "save"):
            db.touch_save(sess.save_id)
            print("已保存。")
            continue
        if act in ("选项", "options"):
            print_options(current_options)
            continue

        # 编号选择：把 "1" 映射为对应的选项文本
        if act.isdigit() and current_options:
            idx = int(act)
            if 1 <= idx <= len(current_options):
                act = current_options[idx - 1]
                print(f"（你选择了：{act}）")
            else:
                print(f"没有第 {idx} 个选项。")
                continue

        # 玩家主动交谈语法：/talk 队友编号
        talk_id = None
        if act.startswith("/talk "):
            try:
                talk_id = int(act.split(" ", 1)[1])
                act = "（继续与队友交谈）"
            except ValueError:
                pass

        result = sess.run_turn(act, talk_companion_id=talk_id)
        print("\n" + result["narrative"])
        if result.get("companion_interaction"):
            print(f"\n〔同伴互动〕{result['companion_interaction']}")
        ev = result.get("event")
        if ev:
            print(f"\n〔突发事件·{ev['name']}〕{ev['skeleton']}")
        current_options = list(result.get("options") or [])
        print_options(current_options)


def ensure_api_key():
    """若未设置 DEEPSEEK_API_KEY，则提示用户输入（存到进程环境，供本次运行使用）。

    返回是否已就绪（有 key 或用户愿意无 key 使用兜底）。
    """
    if config.DEEPSEEK_API_KEY:
        return True
    print("\n检测到未设置 DEEPSEEK_API_KEY。")
    print("DeepSeek 需要 API Key 才能生成叙事（不设置则使用预设兜底文本，体验有限）。")
    ans = ask("是否现在输入 API Key？(y/n)：").lower()
    if ans in ("y", "yes", "是"):
        key = ask("请输入 DeepSeek API Key：")
        if key:
            os.environ["DEEPSEEK_API_KEY"] = key
            config.DEEPSEEK_API_KEY = key
            return True
    print("将使用兜底文本模式（无实时叙事）。")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--new", help="直接新建游戏并给定名字")
    args = parser.parse_args()

    try:
        db.init_db()
        ensure_api_key()
        if args.new:
            sess, msg = engine.new_game_cli(args.new)
            play_loop(sess, msg)
            sess.close()
        else:
            banner()
            main_menu()
    except _Exit:
        # 输入流结束 / Ctrl+C：正常退出，不打印 traceback
        print("\n再见！")


if __name__ == "__main__":
    main()