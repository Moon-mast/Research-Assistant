"""交互式 REPL：python -m harness.cli

内置斜杠命令：
    /help /tools /config /collab on|off /folder <路径> /clear /exit
"""
from __future__ import annotations

import json
import sys
import webbrowser
from pathlib import Path

from . import __version__, providers
from .agent import Agent, IterationLimitError
from .config import HarnessConfig
from .skills import (
    SkillNotFoundError,
    activate_skills,
    default_skills_dir,
    discover,
    load_skill,
    skill_instructions,
)
from .team import Collaboration, guess_critic_model
from .tools import build_ask_tool
from .workspace import FolderWorkspace, build_folder_tools

# Windows 老控制台默认 GBK 编码，遇到某些符号可能崩；统一按 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass

BANNER = r"""
  _   _  ____ ____  ____  ____  _____
 | | | || __ ) ___||  _ \|  _ \| ____|
 | |_| ||  _ \___ \| |_) | |_) |  _|
 |  _  || |_) |__) |  _ <|  _ <| |___
 |_| |_||____/____/|_| \_\_| \_\_____|
"""
HELP = """可用命令：
  /tools      —— 查看当前注册的工具及其 schema
  /config     —— 查看当前模型接入配置
  /collab on|off|? —— 打开/关闭「主答+审查」多模型协作（持久化到 .env）
  /folder <路径>    —— 选定工作目录，模型可 list_files / read_file / write_file 读写其中文件
  /skills     —— 列出本地已安装的技能
  /skill <名> —— 把某技能（如 galaxy-ui）的说明书预载进当前会话
  /clear      —— 清空对话历史（开始新话题）
  /help       —— 显示本帮助
  /exit       —— 退出

直接输入文字即可与模型对话（它可自主调用工具）；输入末尾加 \\ 可换行继续输入。
"""


def _preview(text: str, width: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[:width] + "…"


def main() -> None:
    config = HarnessConfig.from_env()

    def on_tool_start(name: str, args: dict) -> None:
        try:
            pretty = json.dumps(args, ensure_ascii=False)
        except (TypeError, ValueError):
            pretty = str(args)
        print(f"  ⚙ 调用 {name}({pretty})")

    def on_tool_end(name: str, result) -> None:
        status = "✔ 完成" if result.ok else "✘ 失败"
        print(f"    → {status}：{_preview(result.content)}")

    def confirm_write(name: str, args: dict) -> bool:
        args = args if isinstance(args, dict) else {}
        path = str(args.get("path", ""))
        content = str(args.get("content", ""))
        print(f"\n✍ 模型请求 {name} 写入文件：{path}")
        print("----- 内容预览（前 3000 字符） -----")
        print(content[:3000] if content else "（空内容）")
        print("-------------------------------------")
        try:
            ans = input("允许写入吗？(y=允许，其它=拒绝) > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return ans in ("y", "yes")

    def ask_user(name: str, args: dict) -> str:
        args = args if isinstance(args, dict) else {}
        question = str(args.get("question", "")).strip() or "（模型没有写出问题）"
        options = str(args.get("options", "")).strip()
        print(f"\n💬 模型提问：{question}")
        if options:
            print("  可选项：" + options.replace("/", " ｜ ") + "（也可自由输入）")
        try:
            return input("你的回答 > ").strip()
        except (EOFError, KeyboardInterrupt):
            return ""

    agent = Agent(config, on_tool_start=on_tool_start, on_tool_end=on_tool_end,
                  confirm_handler=confirm_write, ask_handler=ask_user)
    agent.add_tools([build_ask_tool()])  # 让模型可随时中途提问
    colab = None        # 懒创建：启用协作时才实例化
    folder_ws = None    # 懒创建：/folder 选择工作目录后注入文件工具

    # —— 技能系统：检测到 skills/ 即自动启用（技能工具 + 清单注入 + preview_html）——
    skills_dir = default_skills_dir()
    skill_list = discover(skills_dir)          # 启动时快照，/skills 用它展示
    loaded_skills: list[str] = []              # 用户 /skill 手动预载过的技能名
    skill_tool_kwargs = dict(
        skills_dir=skills_dir,
        get_workspace=lambda: folder_ws,
        open_html=lambda p: webbrowser.open(Path(p).as_uri()),
    )

    def ensure_skills(target) -> None:
        """给单模型 agent 或协作对象把技能（含已预载项）都接上；可重复调用。"""
        if skill_list:
            activate_skills(target, preload=tuple(loaded_skills), **skill_tool_kwargs)

    ensure_skills(agent)

    print(BANNER)
    print(f"harness v{__version__} —— 接入大模型的 agent 工具循环")
    print(f"  {providers.summary()}")
    print(f"  模型: {config.model} @ {config.base_url}")
    mode = "协作（主答+审查）" if config.collab_enabled else "单模型"
    print(f"  模式: {mode}（用 /collab on|off 切换）")
    if skill_list:
        print(f"  技能: {len(skill_list)} 个已自动启用（{'、'.join(s.name for s in skill_list)}）。"
              "/skills 查看，/skill <名> 预载。")
    print("  输入 /help 查看命令，/exit 退出。\n")

    problems = config.problems()
    if problems:
        for p in problems:
            print(f"⚠ {p}")
        print("  提示：运行 python -m harness.select 可交互选择厂商并填入密钥，一键写进 .env。\n")

    while True:
        try:
            raw = input("\n你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            break
        if not raw:
            continue

        # 行尾加 \ 继续输入
        while raw.endswith("\\"):
            raw = raw[:-1]
            try:
                cont = input("   ... ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            raw += "\n" + cont

        cmd, _, arg = raw.partition(" ")
        cmd = cmd.lower()
        if cmd in ("/exit", "/quit"):
            print("再见。")
            break
        if cmd == "/help":
            print(HELP)
            continue
        if cmd == "/tools":
            tools = agent.tool_list()
            print("\n已注册工具：" if tools else "\n当前没有可用工具。")
            for line in tools:
                print(f"  • {line}")
            print(f"\n传给模型的 schema 共 {len(agent.tool_schemas())} 个。")
            continue
        if cmd == "/config":
            print("\n" + config.summary())
            continue
        if cmd == "/skills":
            if not skill_list:
                print("\n当前没有安装技能。把技能放到项目根 skills/ 目录（内含 SKILL.md）即可。")
            else:
                print("\n可用技能：")
                for s in skill_list:
                    print(f"  • {s.name} —— {s.description}")
                    if s.when_to_use:
                        print(f"      触发：{s.when_to_use}")
                    if s.library:
                        print(f"      素材库：{s.library}")
                print("\n模型需要时会在任务中自行调用 use_skill 拉取说明；带素材库的技能附")
                print("  library_list_*/library_read_* 只读浏览工具。")
                print("把某技能直接预载进当前会话：/skill <名>")
            continue
        if cmd == "/skill":
            name = arg.strip()
            if not name:
                print("用法：/skill <技能名>（先 /skills 查看有哪些）")
                continue
            try:
                skill = load_skill(name, skills_dir)
            except SkillNotFoundError as e:
                print(f"✘ {e}")
                continue
            if name not in loaded_skills:
                loaded_skills.append(name)
            # 注入到接下来真正跑主循环的那个模型（单模型 or 协作主答）
            if config.collab_enabled:
                if colab is not None:
                    ensure_skills(colab)     # 已在跑的协作：即刻生效
                # colab 尚未创建时：下次创建会用 loaded_skills 预载
            else:
                ensure_skills(agent)
            print(f"✔ 已把技能「{skill.name}」说明书注入当前会话，接下来的任务它会带路。")
            print("  提示：新开一段对话(/clear)后，该技能会随首条 system 一并带上，效果更稳。")
            continue
        if cmd == "/clear":
            agent.reset()
            if colab is not None:
                colab.reset()
            print("已清空对话历史。")
            continue
        if cmd == "/folder":
            if not arg.strip():
                if folder_ws is not None:
                    print(f"当前工作目录：{folder_ws.root_str}")
                else:
                    print("未设置工作目录。用法：/folder D:\\某个文件夹")
                continue
            try:
                folder_ws = FolderWorkspace(arg.strip()) if folder_ws is None else folder_ws
                folder_ws.set_root(arg.strip())
                tools = build_folder_tools(folder_ws)
                agent.add_tools(tools)
                if colab is not None:
                    colab.add_tools(tools)
            except Exception as e:
                print(f"✘ 无法使用该文件夹：{type(e).__name__}: {e}")
                continue
            print(f"✔ 工作目录已设为：{folder_ws.root_str}")
            print("  模型现在可调用 list_files / read_file / write_file 读写其中的文件。")
            continue
        if cmd == "/collab":
            sub = arg.strip().lower()
            if sub in ("on", "1", "true", "yes"):
                critic = config.collab_critic_model or providers.critic_default_for(config.base_url) or config.model
                config.collab_enabled = True
                config.collab_critic_model = critic
                providers.set_collab(True, critic)
                colab = None
                print(f"✔ 已开启「主答+审查」协作：主答 {config.model} / 审查 {critic}")
            elif sub in ("off", "0", "false", "no"):
                config.collab_enabled = False
                config.collab_critic_model = None
                providers.set_collab(False, "")
                colab = None
                print("已关闭协作，回到单模型模式。")
            else:
                cur_critic = config.collab_critic_model or providers.critic_default_for(config.base_url) or "？"
                print("用法：/collab on | off")
                print(f"当前：{'协作开（主答 ' + config.model + ' / 审查 ' + cur_critic + '）' if config.collab_enabled else '协作关（单模型）'}")
            continue
        if raw.startswith("/"):
            print(f"未知命令 {raw!r}，输入 /help 查看。")
            continue

        # 正常对话（协作或单模型）
        try:
            if config.collab_enabled:
                if colab is None:
                    colab = Collaboration(
                        config,
                        critic_model=guess_critic_model(config),
                        on_event=lambda txt: print(f"  ↳ {txt}"),
                        on_tool_start=on_tool_start,
                        on_tool_end=on_tool_end,
                        confirm_handler=confirm_write,
                        ask_handler=ask_user,
                    )
                    extra_tools = [build_ask_tool()]
                    if folder_ws is not None:
                        extra_tools += build_folder_tools(folder_ws)
                    colab.add_tools(extra_tools)
                    ensure_skills(colab)      # 技能工具 + 清单 + 已预载技能，接给主答
                answer = colab.run(raw)
            else:
                answer = agent.run(raw)
        except IterationLimitError as e:
            print(f"\n⚠ {e}")
            continue
        except KeyboardInterrupt:
            print("\n（已中断本次请求，历史保留）")
            continue
        except Exception as e:  # noqa: BLE001 —— 把 API/网络错误友好地展示
            print(f"\n✘ 请求失败：{type(e).__name__}: {e}")
            if "401" in str(e) or "Authentication" in type(e).__name__:
                print("  提示：检查 .env 里的 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL 是否正确。")
            continue

        if answer:
            print(f"\n模型 > {answer}")
        else:
            print("\n模型 > （空回答）")


if __name__ == "__main__":
    main()
