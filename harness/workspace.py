"""选定文件夹 -> 暴露为模型可用的文件工具（可读、可写入）。

用户指定一个工作目录后，模型就可以 list_files / read_file 浏览其内容，并用
write_file 把生成的代码/文本写回该目录。所有访问都限定在工作目录内：绝对路径越界、
.. 逃逸、符号链接指向目录外等都会抛错并以文本回传模型。只读工具 read 不会改动文件；
write_file 只会写入所选目录内（自动创建缺失的子目录），不会删除任何文件。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .tools import Tool, make_tool

MAX_READ_CHARS = 12000       # 单次读取字符上限，防止模型把大文件全读爆
MAX_LIST_ITEMS = 2000        # 单次列出的条目上限


class FolderWorkspace:
    """一个可变根目录的工作区；绑定在该实例上的工具始终跟随最新 root。"""

    def __init__(self, root: str | Path):
        self.set_root(root)

    def set_root(self, root: str | Path) -> None:
        p = Path(root).expanduser()
        if not p.is_dir():
            raise NotADirectoryError(f"不是可用的文件夹：{p}")
        self._root = p.resolve()

    @property
    def root(self) -> Path:
        return self._root

    @property
    def root_str(self) -> str:
        return str(self._root)

    # ---- 内部：解析并守住边界 ----
    def _resolve(self, raw: str) -> Path:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = self._root / p
        target = p.resolve(strict=False)
        try:
            target.relative_to(self._root)
        except ValueError:
            raise ValueError(
                f"路径越界：{raw!r} 解析到 {target}，不在工作目录 {self._root} 内"
            ) from None
        return target

    # ---- 模型可调用的工具（绑定方法，root 动态跟随） ----
    def list_files(self, path: str = ".", recursive: bool = False) -> str:
        """列出文件夹内容（默认一层；recursive 则给出相对路径的完整文件树）。

        Args:
            path (str): 要列出的子目录相对路径，默认 "." 表示工作目录本身。
            recursive (bool): 是否递归列出所有子目录的文件。
        """
        target = self._resolve(path)
        if not target.is_dir():
            raise ValueError(f"{path} 不是目录")
        lines: list[str] = []

        def walk(d: Path, rel_prefix: str = "") -> None:
            try:
                entries = sorted(d.iterdir(), key=lambda x: x.name.lower())
            except PermissionError as e:
                raise ValueError(f"无权限读取 {d}") from e
            for i, child in enumerate(entries):
                if len(lines) >= MAX_LIST_ITEMS:
                    lines.append(f"...(已达 {MAX_LIST_ITEMS} 条上限，请换更小的目录)")
                    return
                name = child.name + ("/" if child.is_dir() else "")
                shown = (rel_prefix + name) if rel_prefix else name
                lines.append(shown)
                if recursive and child.is_dir():
                    walk(child, rel_prefix + child.name + "/")

        walk(target)
        if not lines:
            return "（空目录）"
        return "\n".join(lines)

    def read_file(self, path: str, max_chars: int = MAX_READ_CHARS) -> str:
        """读取工作目录内一个文本文件的内容。

        Args:
            path (str): 文件路径（相对工作目录，或以工作目录为根的绝对路径）。
            max_chars (int): 最多读取的字符数，超出会截断并注明。
        """
        target = self._resolve(path)
        if not target.is_file():
            raise ValueError(f"{path} 不是文件或不存在")
        if max_chars < 1:
            max_chars = MAX_READ_CHARS
        try:
            data = target.read_bytes()[: max_chars * 4]  # 多取一点字节以对齐字符
            text = data.decode("utf-8", errors="replace")
        except OSError as e:
            raise ValueError(f"读取失败：{e}") from None
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n…[已截断，超过 {max_chars} 字符]"
        return text

    def write_file(self, path: str, content: str) -> str:
        """把内容写入所选工作目录内的一个文件（不存在则新建，已存在则覆盖）。

        Args:
            path (str): 目标文件路径（相对工作目录，或以工作目录为根的绝对路径）。
            content (str): 要写入的完整内容。
        """
        target = self._resolve(path)
        if target.exists() and target.is_dir():
            raise ValueError(f"{path} 是目录，不能当作文件写入")
        content = content or ""
        if len(content) > 2_000_000:
            raise ValueError("内容过长（超过 200 万字符），请分段写入")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as e:
            raise ValueError(f"写入失败：{e}") from None
        return f"✔ 已写入 {len(content)} 字符 -> {target}"

    def describe(self) -> str:
        return f"工作目录：{self._root}"


def build_folder_tools(ws: FolderWorkspace) -> list[Tool]:
    """根据工作区生成可注入 Agent 的三个文件工具。"""
    return [
        make_tool(
            ws.list_files,
            name="list_files",
            description="列出所选工作目录（或其子目录）里的内容。路径必须在该工作目录内，默认看根目录。",
        ),
        make_tool(
            ws.read_file,
            name="read_file",
            description="读取所选工作目录内文本文件的内容（UTF-8）。路径必须在该工作目录内。",
        ),
        make_tool(
            ws.write_file,
            name="write_file",
            description="把文本/代码完整写入所选工作目录内的一个文件；文件不存在会新建（含缺失的子目录），已存在则覆盖。只能写进该工作目录内，无法删除文件。该操作需要用户确认。",
            confirm=True,
        ),
    ]


def build_workspace_tools(ws: FolderWorkspace) -> list[Tool]:
    """完整工作区能力包：文件工具 + PDF 工具（pypdf 解压 FlateDecode 流）+ 受确认门控的脚本执行。

    Agent.add_tools 按工具名去重，同一引擎上重复挂载是安全的。
    """
    from .pdf_tools import build_pdf_tools
    from .python_tools import build_python_tools
    return [*build_folder_tools(ws), *build_pdf_tools(ws), *build_python_tools()]
