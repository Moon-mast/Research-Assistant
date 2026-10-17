"""离线自检：技能系统 + galaxy-ui 技能（不依赖 API key、不发任何网络请求）。

验证：
  1. skills/ 目录能被发现，galaxy-ui 技能解析正确、素材库在且够大；
  2. CATALOG 参考文件已生成并覆盖 11 个分类；
  3. make_skill_tools 产出 list_skills / use_skill / library_list_*/library_read_*/preview_html；
  4. 素材库只读工具的越界防护与正常读取路径；
  5. activate_skills 能接到 Agent（与 Collaboration 的主答）上并注入技能清单。

运行：.venv\\Scripts\\python examples\\skills_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 项目根目录

# Windows 老控制台默认 GBK，打印中文会乱码；统一按 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass

from harness import (
    Agent,
    Collaboration,
    HarnessConfig,
    SkillNotFoundError,
    activate_skills,
    catalog_block,
    discover,
    load_skill,
    make_skill_tools,
)

GALAXY_CATEGORIES = [
    "Buttons", "Cards", "Checkboxes", "Forms", "Inputs", "Notifications",
    "Patterns", "Radio-buttons", "Toggle-switches", "Tooltips", "loaders",
]


def _has_tool(agent, name: str) -> bool:
    return any(t.name == name for t in agent.tools)


def main() -> None:
    # 1) 发现与解析
    print("== 1. 技能发现 ==")
    skills = discover()
    names = [s.name for s in skills]
    assert "galaxy-ui" in names, names
    assert not any(n.startswith("_") for n in names), "下划线开头目录应被当作模板跳过"
    print("  发现技能：", names)

    g = load_skill("galaxy-ui")
    assert g.library is not None and g.library.is_dir(), g.library
    assert g.library_list_tool == "library_list_galaxy_ui"
    assert g.library_read_tool == "library_read_galaxy_ui"
    assert [t for t, _ in g.references] == ["ADAPT", "CATALOG"], g.references
    html_files = sum(1 for _ in g.library.rglob("*.html"))
    assert html_files > 3000, f"素材库 HTML 数异常：{html_files}"
    print(f"  素材库：{g.library}（{html_files} 个组件）")
    print(f"  参考文件：{[t for t, _ in g.references]}")
    try:
        load_skill("no-such-skill")
        raise AssertionError("load_skill 应抛 SkillNotFoundError")
    except SkillNotFoundError:
        print("  load_skill 找不到时抛错：OK")

    # 2) CATALOG 已生成且覆盖全部分类
    print("\n== 2. CATALOG 覆盖 ==")
    catalog_text = dict(g.references)["CATALOG"]
    for cat in GALAXY_CATEGORIES:
        assert f"## {cat}（" in catalog_text, f"CATALOG 缺分类 {cat}"
    assert "合计 3802 个" in catalog_text, "合计行缺失/数量不对"
    print(f"  覆盖 {len(GALAXY_CATEGORIES)} 个分类，共 {len(catalog_text)} 字符")

    # 3) 工具集
    print("\n== 3. 工具集 ==")
    base = make_skill_tools()
    assert {t.name for t in base} == {
        "list_skills", "use_skill",
        "library_list_galaxy_ui", "library_read_galaxy_ui",
    }, [t.name for t in base]
    opened: list[str] = []
    full = make_skill_tools(get_workspace=lambda: None, open_html=lambda p: opened.append(str(p)))
    assert any(t.name == "preview_html" for t in full), "提供了宿主回调应生成 preview_html"
    print("  工具：", [t.name for t in full])

    # 4) 素材库只读工具的越界防护 + 正常读取
    print("\n== 4. 素材库浏览 ==")
    list_tool = next(t for t in base if t.name == g.library_list_tool)
    read_tool = next(t for t in base if t.name == g.library_read_tool)
    listing = list_tool.invoke('{"path": "Buttons"}')
    assert listing.ok and "0x-Sarthak" in listing.content, listing.display[:120]
    print("  library_list(Buttons)：", listing.display[:60].replace("\n", " "), "…")

    sample = list_tool.invoke('{"path": ".", "recursive": false}')
    assert sample.ok and "Buttons" in sample.content and "Cards" in sample.content
    print("  library_list(根)：", sample.display.splitlines()[0][:60], "…")

    escape = read_tool.invoke('{"path": "../../requirements.txt"}')
    assert not escape.ok and "越界" in escape.content, escape.display[:120]
    print("  越界读取被拦：", escape.display[:60])

    # 取一个真实纯 css 组件读回来验证
    btn = Path(g.library) / "Buttons"
    sample_file = None
    for f in btn.glob("*.html"):
        head = f.read_bytes()[:1024].decode("utf-8", errors="replace")
        if "<style" in head:
            sample_file = f
            break
    assert sample_file is not None
    ok_read = read_tool.invoke(f'{{"path": "{sample_file.relative_to(g.library).as_posix()}"}}')
    assert ok_read.ok and "style" in ok_read.content.lower()
    print(f"  正常读取：{sample_file.name} OK")

    # 5) activate_skills 接到 Agent / Collaboration
    print("\n== 5. 激活 ==")
    cfg = HarnessConfig(api_key="sk-dummy")  # 只做离线逻辑，绝不联网
    a = Agent(cfg, tools=[])
    assert activate_skills(a) == 1
    assert _has_tool(a, "use_skill") and _has_tool(a, "library_list_galaxy_ui")
    extra = "\n".join(a._extra_system)
    assert "galaxy-ui" in extra and "本机已启用以下技能" in extra
    assert a.messages == [], "空历史时不应立刻 seed 出消息"
    print("  Agent：已加技能工具 + 清单注入，history 仍为空")

    colab = Collaboration(cfg)
    assert activate_skills(colab) == 1
    assert _has_tool(colab.main, "use_skill")
    assert "galaxy-ui" in "\n".join(colab.main._extra_system)
    print("  Collaboration：技能已接到主答模型")

    # 6) 再次激活不重复注入
    n_before = len(a._extra_system)
    assert activate_skills(a) == 1
    assert len(a._extra_system) == n_before, "重复激活不应重复注入清单"
    print("  重复激活幂等：OK")

    print("\n✅ 技能系统自检全部通过。")


if __name__ == "__main__":
    main()
