"""重建 galaxy-ui 技能的素材库目录文件（skills/galaxy-ui/references/CATALOG.md）。

扫 galaxy-main 下各分类的 *.html，按文件头部判断每类是：
    css      —— 自带 <style> 的纯 CSS，可直接搬进页面；
    tailwind —— 用 Tailwind 工具类，通常需引 CDN 或改写成纯 CSS；
    other    —— 需打开确认（可能外链样式 / 仅内联工具类等）。
输出每类数量 + 风格比例 + 若干示例文件名（含风格、作者、标签），供模型先看分布、
再决定读取哪几个文件。纯 stdlib，可重复运行且输出稳定（按序 + 等距抽样）。

用法：.venv\\Scripts\\python scripts\\build_galaxy_catalog.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Windows 老控制台默认 GBK，打印含中文/符号会崩；统一按 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]          # 项目根目录
GALAXY = ROOT / "galaxy-main"
OUT = ROOT / "skills" / "galaxy-ui" / "references" / "CATALOG.md"

HEAD_BYTES = 4096            # 读文件头用于判断（够看到 <style> 与标签注释）
TAG_SPLIT = re.compile(r"[\s,;|/]+")


def classify(head: str, tags_low: str) -> str:
    """按内容与头部标签粗判风格。仅为目录参考，个别文件可能误判。"""
    if "<style" in head:
        return "css"
    if "tailwind" in head or "tailwindcss" in head or "tailwind" in tags_low:
        return "tailwind"
    return "other"


def describe(path: Path) -> tuple[str, str, str]:
    """返回 (风格, 作者, 前几个标签串)。"""
    try:
        head = path.read_bytes()[:HEAD_BYTES].decode("utf-8", errors="replace")
    except OSError:
        return "other", "", ""
    m = re.search(r"<!--(.*?)-->", head, re.S)
    comment = m.group(1) if m else ""
    author = ""
    am = re.search(r"Uiverse\.io\s+by\s+(.+?)\s*-?\s*Tags\s*:", comment, re.I)
    if am:
        author = am.group(1).strip()
    elif not author and path.stem:
        author = path.stem.split("_", 1)[0]
    tm = re.search(r"Tags\s*:\s*([^\n]*)", comment, re.I)
    tags = tm.group(1).strip() if tm else ""
    tags = " ".join(dict.fromkeys(p for p in TAG_SPLIT.split(tags) if p))[:80]
    if not tags:
        tags = "（无标签）"
    return classify(head, comment.lower()), author, tags


def pick_examples(info: list[tuple], n: int = 9) -> list[tuple]:
    """确定性地挑 n 个示例：先各风格取首个代表，再按原顺序等距补足。"""
    by_style: dict[str, list[tuple]] = {}
    for row in info:
        by_style.setdefault(row[1], []).append(row)
    chosen: list[tuple] = []
    for st in ("css", "tailwind", "other"):
        if by_style.get(st):
            chosen.append(by_style[st][0])
    seen = set(id(c) for c in chosen)
    rest = [c for c in info if id(c) not in seen]
    if rest:
        step = len(rest) / max(1, n - len(chosen))
        i = 0.0
        while len(chosen) < n and i < len(rest):
            c = rest[int(i)]
            if id(c) not in seen:
                chosen.append(c)
                seen.add(id(c))
            i += step
    return chosen[:n]


def human_size(path: Path) -> str:
    kb = path.stat().st_size / 1024
    return f"{kb:.1f}KB" if kb < 1000 else f"{kb / 1024:.1f}MB"


def main() -> None:
    if not GALAXY.is_dir():
        raise SystemExit(f"找不到组件库目录：{GALAXY}（galaxy-main 是否仍在项目根？）")

    categories = sorted(
        (d for d in GALAXY.iterdir()
         if d.is_dir() and not d.name.startswith((".", "_")) and any(d.glob("*.html"))),
        key=lambda d: d.name.lower(),
    )
    lines: list[str] = [
        "# Galaxy（Uiverse.io）组件库 · 分类目录",
        "",
        "> 本文件由 scripts/build_galaxy_catalog.py 自动生成，统计仅按文件头部粗判、用于参考。",
        f"> 库根：`{GALAXY.relative_to(ROOT)}`；浏览用 `library_list_galaxy_ui`（列目录）/"
        "`library_read_galaxy_ui`（读文件），路径相对库根，例如 `Buttons/xxx.html`。",
        "",
        "风格标记：",
        "- `css` 自带 `<style>` 的纯 CSS —— 可整体搬进页面，首选；",
        "- `tailwind` 用 Tailwind 工具类 —— 需引 CDN（`https://cdn.tailwindcss.com`）或改写成纯 CSS；",
        "- `other` 需打开确认（可能外链样式/内联工具类）。",
        "",
        "示例只是**抽样候选**，决定前先 `library_read_galaxy_ui` 打开实际看一眼。",
        "",
    ]
    total = 0
    for cat in categories:
        files = sorted(cat.glob("*.html"))
        if not files:
            continue
        info = []
        for f in files:
            style, author, tags = describe(f)
            info.append((f, style, author, tags))
        total += len(info)
        n_css = sum(1 for row in info if row[1] == "css")
        n_tw = sum(1 for row in info if row[1] == "tailwind")
        n_oth = len(info) - n_css - n_tw
        pct = lambda n: round(n * 100 / len(info)) if info else 0
        lines.append(f"## {cat.name}（{len(info)} 个）")
        lines.append(f"纯CSS ~{pct(n_css)}% · tailwind ~{pct(n_tw)}% · 其他 ~{pct(n_oth)}%")
        lines.append("示例：")
        for f, style, author, tags in pick_examples(info):
            author = author or "?"
            lines.append(
                f"- {f.relative_to(GALAXY).as_posix()} · {style} · {author} · "
                f"{tags} · {human_size(f)}"
            )
        lines.append("")
    lines.append(
        f"**合计 {total} 个组件 · {len(categories)} 类**。"
        "要遍历某类全部文件，用 `library_list_galaxy_ui` 列出该目录即可。"
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"✔ 已写入 {OUT}")
    print(f"  组件 {total} 个 / {len(categories)} 类，输出 {OUT.stat().st_size} 字节")


if __name__ == "__main__":
    main()
