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
        choice = input("请选择：").strip()
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
    name = safe_text(input("存档名称：")).strip() or "无名冒险"
    print("\n世界设定来源（二选一）：")
    print("  1) 输入你自己的世界描述")
    print("  2) 从预设 Wiki 搜索关键词")
    src = input("选择(1/2)：").strip()
    world_desc = None
    wiki_api = None
    wiki_kw = None
    if src == "1":
        world_desc = safe_text(input("简要描述你的世界设定：")).strip() or None
    elif src == "2":
        print("可选 Wiki：")
        for k, w in config.PRESET_WIKIS.items():
            print(f"  {k}: {w['label']}")
        wkey = input("输入 Wiki 键（如 dnd_cn / forgottenrealms / criticalrole / custom）：").strip()
        wiki_cfg = config.PRESET_WIKIS.get(wkey)
        if wiki_cfg is None:
            print("无效 Wiki，使用默认世界观。")
        else:
            wiki_api = wiki_cfg["api"]
            if wkey == "custom":
                wiki_api = safe_text(input("输入自定义 api.php 地址：")).strip() or None
            wiki_kw = safe_text(input("输入搜索关键词：")).strip() or "dungeons dragons"
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
        sid = int(input("输入要加载的存档编号：").strip())
    except ValueError:
        print("无效编号。")
        return
    sess, state = engine.load_game_cli(sid)
    if sess is None:
        print("存档不存在。")
        return
    play_loop(sess, "继续你的冒险…")


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
        sid = int(input("输入要删除的存档编号：").strip())
    except ValueError:
        print("无效编号。")
        return
    db.delete_save(sid)
    print("已删除。")


def play_loop(sess, opening):
    print("\n" + opening)
    print("-" * 50)
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
        # 玩家主动交谈语法：/talk 队友编号
        talk_id = None
        if act.startswith("/talk "):
            try:
                talk_id = int(act.split(" ", 1)[1])
                act = f"（继续与队友交谈）"
            except ValueError:
                pass

        result = sess.run_turn(act, talk_companion_id=talk_id)
        print("\n" + result["narrative"])
        if result.get("companion_interaction"):
            print(f"\n〔同伴互动〕{result['companion_interaction']}")
        if result.get("event_hint"):
            print(f"\n〔事件〕{result['event_hint']}")
        if result.get("options"):
            print("\n行动选项：")
            for i, o in enumerate(result["options"], 1):
                print(f"  {i}) {o}")


def ensure_api_key():
    """若未设置 DEEPSEEK_API_KEY，则提示用户输入（存到进程环境，供本次运行使用）。

    返回是否已就绪（有 key 或用户愿意无 key 使用兜底）。
    """
    if config.DEEPSEEK_API_KEY:
        return True
    print("\n检测到未设置 DEEPSEEK_API_KEY。")
    print("DeepSeek 需要 API Key 才能生成叙事（不设置则使用预设兜底文本，体验有限）。")
    ans = input("是否现在输入 API Key？(y/n)：").strip().lower()
    if ans in ("y", "yes", "是"):
        key = safe_text(input("请输入 DeepSeek API Key：")).strip()
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

    db.init_db()
    ensure_api_key()
    if args.new:
        name = args.new
        sess, msg = engine.new_game_cli(name)
        play_loop(sess, msg)
    else:
        banner()
        main_menu()


if __name__ == "__main__":
    main()