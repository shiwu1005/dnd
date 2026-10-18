# 文字 D&D 冒险游戏（DeepSeek 驱动）

一个完整的文字 D&D 游戏，核心逻辑（数值/规则/状态）由 Python 计算，
DeepSeek（`deepseek-flash`）只负责叙事。支持 SQLite 多存档、世界书、
预设 Wiki 搜索、固定事件池、长线摘要、队友系统、梗系统。

## 技术栈

- Python 3.10+，SQLite（`sqlite3`，每次连接后执行 `PRAGMA foreign_keys = ON;`）
- LLM：DeepSeek V4.1-Flash，模型名 `deepseek-flash`，Base URL `https://api.deepseek.com`，OpenAI SDK 兼容
- 交互层（CLI）与核心逻辑解耦

## 安装

```bash
pip install -r requirements.txt   # 即 openai
```

设置 API Key：

```bash
# Windows (PowerShell)
$env:DEEPSEEK_API_KEY="你的key"

# Linux / macOS
export DEEPSEEK_API_KEY="你的key"
```

> ⚠️ **安全提醒**：API Key 通过环境变量传入，**不要写进代码或提交到 Git**。
> 本仓库已配置 `.gitignore`，会忽略 `.env`、`*.db`、`key.dat` 等敏感/数据文件。
> 首次运行 exe 时若未设置环境变量，程序会提示输入 Key，并**自动记住**
> （Windows 下用 DPAPI 加密，只能由当前 Windows 用户解密），下次启动不再询问。

## 本地构建可执行文件（PyInstaller）

```bash
pip install pyinstaller
pyinstaller --onefile --console --name TextDND --exclude-module numpy --exclude-module pandas main.py
# 产物在 dist/TextDND.exe；game.db 会在运行时自动创建在 exe 旁
```

## 运行

```bash
python main.py                  # 进入主菜单（新建/加载/列表/删除）
python main.py --new 存档名      # 直接新建
python main.py --set-key KEY    # 设置并保存 API Key（下次免输入）
python main.py --forget-key     # 删除本机保存的 API Key
```

游戏内指令：

- 直接输入文本 → 作为玩家行动（本作不提供预设选项，行动完全自由）
- `/talk 队友编号` → 主动与某队友交谈
- `保存` / `save` → 手动保存
- `退出` / `quit` → 退出（回到主菜单）

**读档**时会先显示「上次冒险回顾」：你最后的行动与叙述者最后的回复，
接着直接输入你的下一步行动即可。

## 项目结构

```
game/
  config.py      全局配置、预设 Wiki、关键词黑名单
  db.py          SQLite 连接、建表、事件/梗种子
  llm.py         DeepSeek 调用封装（JSON Output、重试、兜底）
  secrets.py     API Key 本机加密保存（Windows DPAPI）
  worldbook.py   世界书生成（用户描述 / Wiki 搜索）+ 候选队友
  memory.py      长线摘要（15-20 轮触发）
  events.py      固定事件池、加权随机、cooldown
  rules.py       规则引擎（六维、d20、检定、简化战斗）
  companions.py  队友系统（HP/关系由代码维护、被动互动）
  memes.py       梗系统（触发判定、cooldown）
  saves.py       存档加载与 Prompt 组装
  textutil.py    控制台 UTF-8 与非法字符清洗
  engine.py      游戏主循环（GameSession）
tests/
  test_game.py   端到端测试（不依赖真实 LLM）
main.py          命令行入口
```

## 核心铁律（实现要点）

1. **数值由代码计算**：骰子、命中、伤害、状态变更都在 Python（`rules.py`）完成，DeepSeek 只叙事。
2. **世界事实存 SQLite**：每轮 Prompt 由代码按固定顺序组装，不依赖 LLM 记忆。
3. **加载存档不重新生成世界书**：世界书在“新游戏开局”生成一次并固化到该存档。
4. **按需注入，不整表投喂**：事件、世界书、队友背景只注入本轮相关切片。

## 测试

```bash
python tests/test_game.py
```

测试不设置 API Key，会自动走 LLM 兜底路径，验证全部模块协同工作。
若配置了真实 API Key 并可联网，会走真实叙事路径。

## 依赖的开放数据（世界书 Wiki 搜索）

- D&D 中文 Wiki（灰机）：https://dnd.huijiwiki.com/api.php
- 被遗忘国度（英文）：https://forgottenrealms.fandom.com/api.php
- Critical Role 设定（英文）：https://criticalrole.fandom.com/api.php
- 自定义：用户填 api.php

## 常见问题

**Q：只给一个 `TextDND.exe` 就能运行吗？**

可以。`dist/TextDND.exe` 是 PyInstaller 单文件程序，已内置 Python 解释器与
所需运行库，**目标机器无需安装 Python 或任何依赖**。但请注意：

- 需要 **Windows 10/11 64 位**（打包于 64 位环境）。
- 需要**联网**访问 `api.deepseek.com`，并准备 **DeepSeek API Key**；
  没有 Key 也能启动，但只能使用预设兜底文本（无真实叙事）。
- 首次启动会向临时目录解压，**启动需数秒**；之后更快。
- 存档数据库 `game.db` 会自动创建在 **exe 同目录**，复制 exe 到别处即独立存档。
- 若 Windows 提示「未知发布者」，属正常的未签名程序提示，选择「仍要运行」即可。

**Q：中文显示乱码？**

程序启动时会自动把控制台切到 UTF-8 代码页（65001），一般不会乱码。
若仍异常，可先执行 `chcp 65001` 再运行。

**Q：叙事总是重复同一段兜底文字？**

说明模型返回的内容没能解析出叙事。程序会依次尝试：JSON 模式两次 →
纯文本模式一次 → 预设兜底。若仍失败，可设置环境变量 `DND_DEBUG_LLM=1`
再运行一次，原始响应会写入 exe 同目录的 `llm_debug.log`，便于定位。