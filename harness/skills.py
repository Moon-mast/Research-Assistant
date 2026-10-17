"""本地技能(skill)系统：把 ``skills/<name>/SKILL.md`` 包装成 agent 可用的能力包。

设计：技能 = 一个目录下的 SKILL.md（Markdown，``---`` 前端 + 正文），可带
``references/*.md`` 参考文件，也可声明一个**只读素材库**(frontmatter 的 ``library``)，
例如把 galaxy-main 组件库绑定给 galaxy-ui 技能。

- 模型在任务中途发现需要某项能力时，调用 ``use_skill()`` 把该技能的说明书
  （正文 + 参考文件）拉进上下文 —— 需要时才占 token，平时只注入极简清单。
- 声明了素材库的技能会额外注册两个只读浏览工具 ``library_list_<名>`` /
  ``library_read_<名>``，让模型翻阅素材而不必把它设成工作目录，也不污染工作目录。
- ``activate_skills()`` 把以上内容接到一个 Agent（或协作的主答模型）上：注入清单、
  注册工具，可再 ``preload`` 若干技能（把全文直接预载进系统提示）。

SKILL.md 前端格式（键: 值 平铺；``library`` 相对本文件所在目录解析）：

    ---
    name: galaxy-ui
    description: 一句用途简介
    when_to_use: 何时该用它（可选）
    library: ../../galaxy-main
    version: 1
    ---
    正文……
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from .tools import Tool, make_tool
from .workspace import FolderWorkspace

_NAME_RE = re.compile(r"^[a-zA-Z0-9-]{1,64}$")
MAX_LIBRARY_LIST_ITEMS = 1500
MAX_LIBRARY_READ_CHARS = 12000


class SkillError(Exception):
    """技能定义或使用层面的错误。"""


class SkillNotFoundError(SkillError, ValueError):
    """按名字找不到技能。"""


@dataclass
class Skill:
    """一个已解析的本地技能。"""

    name: str
    description: str
    path: Path                # SKILL.md 的绝对路径
    dir: Path                 # 技能目录（含 references/）
    body: str                 # frontmatter 之后的正文
    when_to_use: str = ""
    version: str = ""
    library: Optional[Path] = None      # 素材库绝对路径（若声明且存在）
    library_raw: str = ""               # frontmatter 里的原始路径（便于提示）
    references: list[tuple[str, str]] = field(default_factory=list)  # [(标题, 正文)]

    @property
    def slug(self) -> str:
        """技能名转工具名片段：galaxy-ui -> galaxy_ui。"""
        s = re.sub(r"[^0-9A-Za-z_]+", "_", self.name).strip("_").lower()
        return (s or "skill")[:60]

    @property
    def library_list_tool(self) -> str:
        return f"library_list_{self.slug}"

    @property
    def library_read_tool(self) -> str:
        return f"library_read_{self.slug}"


# --------------------------------------------------------------------------
# 前端 / 正文解析
# --------------------------------------------------------------------------
def _parse_skill_md(path: Path) -> Skill:
    """读取一个 SKILL.md 并解析成 Skill。解析失败抛 SkillError。"""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as e:
        raise SkillError(f"无法读取 {path}：{e}") from None

    meta: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---")
        if end != -1:
            head = text[3:end]
            for raw in head.splitlines():
                line = raw.strip()
                if not line or line.startswith(("#", "//")):
                    continue
                if ":" not in line:
                    continue
                k, _, v = line.partition(":")
                meta[k.strip().lower()] = v.strip()
            rest = text[end + 4:]
            if rest.startswith("\n"):
                rest = rest[1:]
            body = rest

    name = meta.get("name", "").strip() or path.parent.name.strip()
    if not _NAME_RE.match(name):
        raise SkillError(
            f"{path}：技能名 {name!r} 非法（只允许字母/数字/-，长度 1-64）"
        )
    description = meta.get("description", "").strip()
    if not description:
        raise SkillError(f"{path}：缺少 description（会进技能清单，必须写）")

    refs: list[tuple[str, str]] = []
    ref_dir = path.parent / "references"
    if ref_dir.is_dir():
        for ref in sorted(ref_dir.glob("*.md")):
            try:
                refs.append((ref.stem, ref.read_text(encoding="utf-8")))
            except OSError:
                continue

    raw_lib = meta.get("library", "").strip()
    library = None
    if raw_lib:
        library = (path.parent / raw_lib).resolve()
        if not library.is_dir():
            library = None

    return Skill(
        name=name,
        description=description,
        when_to_use=meta.get("when_to_use", "").strip(),
        version=meta.get("version", "").strip(),
        path=path,
        dir=path.parent,
        body=body.strip(),
        library=library,
        library_raw=raw_lib,
        references=refs,
    )


# --------------------------------------------------------------------------
# 目录 / 发现 / 装载
# --------------------------------------------------------------------------
def default_skills_dir() -> Path:
    """项目根下的 skills 目录（无需存在）。"""
    return Path(__file__).resolve().parent.parent / "skills"


def discover(skills_dir: Optional[str | Path] = None) -> list[Skill]:
    """扫描 skills 目录，返回按名排序的可用技能。以下划线/点开头的目录视为模板、跳过。

    skills_dir 缺省用项目默认 skills 目录；目录不存在返回 []。
    """
    base = Path(skills_dir) if skills_dir else default_skills_dir()
    if not base.is_dir():
        return []
    out: list[Skill] = []
    for child in sorted(base.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or child.name.startswith(("_", ".")):
            continue
        md = child / "SKILL.md"
        if not md.is_file():
            continue
        try:
            out.append(_parse_skill_md(md))
        except SkillError:
            continue  # 无效的技能目录直接跳过，不阻塞其它技能
    out.sort(key=lambda s: s.name.lower())
    return out


def load_skill(name: str, skills_dir: Optional[str | Path] = None) -> Skill:
    """按技能名（或目录名）装载一个技能；找不到抛 SkillNotFoundError。"""
    name = (name or "").strip()
    for s in discover(skills_dir):
        if s.name == name or s.dir.name == name:
            return s
    known = ", ".join(s.name for s in discover(skills_dir)) or "（暂无）"
    raise SkillNotFoundError(
        f"找不到技能 {name!r}。可用技能：{known}。"
        "技能目录放在 skills/ 下，目录里要有一个带 --- 前端的 SKILL.md。"
    )


# --------------------------------------------------------------------------
# 文本产出
# --------------------------------------------------------------------------
def catalog_block(skills_dir: Optional[str | Path] = None) -> str:
    """生成注入 system prompt 的极简技能清单（名 + 简介）。无技能时返回空串。"""
    skills = discover(skills_dir)
    if not skills:
        return ""
    lines = [
        "本机已启用以下技能（任务需要其中能力时，调用 use_skill(\"<名>\") 拉取完整说明书；"
        "技能若带只读素材库，另有 library_list_*/library_read_* 工具可翻阅）：",
    ]
    for s in skills:
        trigger = f" 触发场景：{s.when_to_use}" if s.when_to_use else ""
        lines.append(f"- {s.name}：{s.description}{trigger}")
    return "\n".join(lines)


def skill_instructions(skill: Skill, include_refs: bool = True) -> str:
    """把技能组装成给模型看的完整说明书（标题 + 素材库提示 + 正文 + 参考文件）。"""
    parts = [f"【技能：{skill.name}】{skill.description}"]
    if skill.when_to_use:
        parts.append(f"何时使用：{skill.when_to_use}")
    if skill.library:
        parts.append(
            f"素材库（只读）：{skill.library}\n"
            f"用 {skill.library_list_tool}（列出）/ {skill.library_read_tool}（读文件）浏览，"
            "路径相对素材库根目录；素材库不会被写入，只用于读取参考。"
        )
    elif skill.library_raw:
        parts.append(
            f"声明了素材库 {skill.library_raw!r} 但目录不存在（已跳过）。"
            "如需浏览请先让该目录存在后重建会话。"
        )
    parts.append("—— 操作指南 ——")
    parts.append(skill.body)
    if include_refs and skill.references:
        for title, text in skill.references:
            parts.append(f"—— 参考：{title} ——")
            parts.append(text.strip())
    return "\n\n".join(parts)


# --------------------------------------------------------------------------
# 工具：list_skills / use_skill / preview_html / library_*
# --------------------------------------------------------------------------
def _resolve_under(root: Path, raw: str) -> Path:
    """把 raw 解析为 root 内路径（沿用工作区的越界防护语义）。"""
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = root / p
    target = p.resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError:
        raise ValueError(
            f"路径越界：{raw!r} 解析到 {target}，不在 {root} 内"
        ) from None
    return target


def _build_library_tools(skill: Skill) -> list[Tool]:
    """为带素材库的技能注册两个只读浏览工具（复用 FolderWorkspace 实现）。"""
    if skill.library is None:
        return []
    ws = FolderWorkspace(skill.library)
    list_desc = (
        f"只读列出技能「{skill.name}」自带素材库（{skill.library}）里的内容；"
        "该操作不影响工作目录。路径相对素材库根目录，缺省看根。"
    )
    read_desc = (
        f"只读读取技能「{skill.name}」素材库里的一个文本文件（UTF-8）；"
        "该操作不影响工作目录。路径相对素材库根目录。"
    )
    return [
        make_tool(ws.list_files, name=skill.library_list_tool, description=list_desc),
        make_tool(ws.read_file, name=skill.library_read_tool, description=read_desc),
    ]


def make_skill_tools(
    skills_dir: Optional[str | Path] = None,
    *,
    get_workspace: Optional[Callable[[], Optional[FolderWorkspace]]] = None,
    open_html: Optional[Callable[[str], None]] = None,
) -> list[Tool]:
    """生成技能相关的模型工具。

    - list_skills / use_skill：始终生成；
    - 带素材库的技能：各生成 library_list_<名> / library_read_<名> 两个只读工具；
    - 提供 get_workspace + open_html 宿主回调时，额外生成 preview_html
      （把工作目录内的本地 HTML 用系统浏览器打开预览）。

    get_workspace / open_html 在每次调用时取值，因此支持运行期更换工作目录；
    均为只读/预览性质，不影响工作目录内容。
    """
    skills = discover(skills_dir)
    tools: list[Tool] = []

    def _list_skills() -> str:
        """列出本机已安装的全部技能及其用途，帮助判断当前任务该不该用技能。

        当任务涉及技能覆盖的能力（如套用 UI 组件库做界面）且不确定怎么做时，
        先调用它查看；随后用 use_skill 载入对应技能说明。
        """
        found = discover(skills_dir)
        if not found:
            return "（暂无已安装技能。技能目录应放在项目 skills/ 下，内含 SKILL.md。）"
        lines = [f"共 {len(found)} 个技能："]
        for s in found:
            lib = " · 带只读素材库" if s.library else ""
            lines.append(f"- {s.name}：{s.description}{lib}")
        return "\n".join(lines)

    def _use_skill(name: str) -> str:
        """把指定技能的完整说明书（操作指南 + 参考文件）载入上下文，然后严格按它行事。

        当任务需要某项技能覆盖的能力时调用。说明书可能较长，消化要点即可，
        不必向用户复述原文。

        Args:
            name (str): 技能名，例如 galaxy-ui。可用 list_skills 查看全部。
        """
        return skill_instructions(load_skill(name, skills_dir))

    tools.append(make_tool(_list_skills, name="list_skills"))
    tools.append(make_tool(_use_skill, name="use_skill"))

    for s in skills:
        tools += _build_library_tools(s)

    if get_workspace is not None and open_html is not None:
        def _preview_html(path: str) -> str:
            """把工作目录里的一个本地 HTML 文件用系统默认浏览器打开预览，方便用户查看成品。

            生成完整网页/界面并写盘（write_file）后调用它做预览；用户反馈后再迭代。
            文件必须位于所选工作目录内。还没选工作目录时会提示先选择。

            Args:
                path (str): 要预览的文件，相对工作目录或在工作目录内的绝对路径。
            """
            ws = get_workspace()
            if ws is None:
                return (
                    "还没有工作目录，无法预览。请先让用户选定一个工作目录"
                    "（CLI: /folder <路径>；GUI: 点左下「文件夹」），"
                    "把生成的 HTML 写进去后再调用本工具。"
                )
            target = _resolve_under(ws.root, path)
            if not target.is_file():
                raise ValueError(f"{path} 不是文件或不存在（工作目录 {ws.root} 内）")
            open_html(str(target))
            return f"✔ 已在系统默认浏览器打开预览：{target}"

        tools.append(make_tool(_preview_html, name="preview_html"))

    return tools


# --------------------------------------------------------------------------
# 激活：接到 Agent / Collaboration
# --------------------------------------------------------------------------
def _main_model(target) -> "object":
    """返回真正跑主循环的 Agent：传入 Agent 返回自身，传入 Collaboration 返回其 main。"""
    if hasattr(target, "tool_schemas") and hasattr(target, "add_tools"):
        return target
    if hasattr(target, "main"):
        return target.main
    raise SkillError("activate_skills 需要 Agent 或 Collaboration 实例")


def activate_skills(
    target,
    *,
    skills_dir: Optional[str | Path] = None,
    get_workspace: Optional[Callable[[], Optional[FolderWorkspace]]] = None,
    open_html: Optional[Callable[[str], None]] = None,
    preload: Iterable[str] = (),
) -> int:
    """把技能系统接到一个 Agent（或协作主答模型）上，返回发现的技能数。

    - 注册技能工具（可重复调用，Agent 端按名去重）；
    - 把技能清单注入主模型的 system（空历史时并入首条 system 消息）；
    - preload 里的技能：全文直接注入（跳过一次 use_skill 工具往返）。
    """
    skills = discover(skills_dir)
    main = _main_model(target)
    if not skills:
        return 0

    tools = make_skill_tools(
        skills_dir, get_workspace=get_workspace, open_html=open_html
    )
    main.add_tools(tools)

    extra = list(getattr(main, "_extra_system", None) or [])
    existing = set(extra)

    def _inject(text: str) -> None:
        if not text or text in existing:
            return
        main.inject_system(text)
        existing.add(text)

    _inject(catalog_block(skills_dir))
    for name in preload or ():
        try:
            skill = load_skill(str(name), skills_dir)
        except SkillNotFoundError:
            continue
        _inject(f"【已预载技能：{skill.name}】\n" + skill_instructions(skill))
    return len(skills)


__all__ = [
    "Skill",
    "SkillError",
    "SkillNotFoundError",
    "activate_skills",
    "catalog_block",
    "default_skills_dir",
    "discover",
    "load_skill",
    "make_skill_tools",
    "skill_instructions",
]
