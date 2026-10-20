"""PDF 工具：让 agent 能真正读取论文 PDF（正文是 FlateDecode 压缩流，文本读取工具无能为力）。

基于 pypdf：解压 zlib 流、抽取每页文本；支持页码范围（长文档分次读）、
元数据读取；扫描图片页会明确提示无可提取文本（OCR 需外部引擎，未装则如实告知）。

所有路径经 FolderWorkspace._resolve 守住工作目录边界。
"""
from __future__ import annotations

from .tools import Tool, make_tool

MAX_PDF_CHARS = 12000   # 单次抽取字符上限（与 read_file 对齐，防撑爆上下文）


def _require_pypdf():
    try:
        import pypdf  # noqa: F401
    except Exception as e:
        raise RuntimeError(
            "未安装 pypdf，无法解析 PDF。请执行：pip install pypdf"
        ) from e
    return pypdf


def build_pdf_tools(ws) -> list[Tool]:
    """根据工作区生成 PDF 工具（路径边界与文件工具一致）。ws 需提供 _resolve。"""
    resolve = ws._resolve

    def pdf_info(path: str) -> str:
        """读取 PDF 的页数、元数据与逐页文本量概况。

        Args:
            path (str): PDF 文件路径（相对工作目录，或以工作目录为根的绝对路径）。
        """
        pypdf = _require_pypdf()
        target = resolve(path)
        if not target.is_file():
            raise ValueError(f"{path} 不是文件或不存在")
        try:
            reader = pypdf.PdfReader(str(target))
        except Exception as e:
            raise ValueError(f"不是可解析的 PDF（{type(e).__name__}: {e}）") from None
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return "该 PDF 已加密且空口令解不开，无法读取内容。请提供未加密版本。"
        n = len(reader.pages)
        meta = reader.metadata or {}
        title = str(meta.get("/Title", "") or "")
        author = str(meta.get("/Author", "") or "")
        lines = [f"文件：{target.name}", f"页数：{n}"]
        if title:
            lines.append(f"标题：{title}")
        if author:
            lines.append(f"作者：{author}")
        lines.append("逐页文本量（字符数，0 多为扫描图片页）：")
        empty_pages = []
        for i, page in enumerate(reader.pages, start=1):
            try:
                t = (page.extract_text() or "").strip()
            except Exception:
                t = ""
            if len(t) < 10:
                empty_pages.append(i)
            if i <= 20:
                lines.append(f"  P{i}: {len(t)}")
        if n > 20:
            lines.append(f"  …（共 {n} 页，仅列前 20 页）")
        if empty_pages:
            head = ", ".join(map(str, empty_pages[:12]))
            more = "…" if len(empty_pages) > 12 else ""
            lines.append(f"提示：第 {head}{more} 页几乎没有文本层，可能是扫描图片（需 OCR）。")
        lines.append("提示：用 pdf_extract(path, first_page, last_page) 分页读取正文。")
        return "\n".join(lines)

    def pdf_extract(path: str, first_page: int = 1, last_page: int = 0,
                    max_chars: int = MAX_PDF_CHARS) -> str:
        """抽取 PDF 指定页范围的正文文本（自动解压 FlateDecode 流），带页码标记。

        Args:
            path (str): PDF 文件路径（相对工作目录，或以工作目录为根的绝对路径）。
            first_page (int): 起始页（1 开始）。
            last_page (int): 结束页（含）；0 表示到最后一页。
            max_chars (int): 最多返回的字符数，超出截断并提示换页范围续读。
        """
        pypdf = _require_pypdf()
        target = resolve(path)
        if not target.is_file():
            raise ValueError(f"{path} 不是文件或不存在")
        try:
            reader = pypdf.PdfReader(str(target))
        except Exception as e:
            raise ValueError(f"不是可解析的 PDF（{type(e).__name__}: {e}）") from None
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return "该 PDF 已加密且空口令解不开，无法读取内容。请提供未加密版本。"
        n = len(reader.pages)
        if first_page < 1 or first_page > n:
            raise ValueError(f"first_page 需在 1~{n} 之间，收到 {first_page}")
        last = n if (last_page is None or last_page <= 0) else min(last_page, n)
        if last < first_page:
            raise ValueError(f"last_page({last}) 不能小于 first_page({first_page})")
        if max_chars < 500:
            max_chars = MAX_PDF_CHARS

        parts: list[str] = []
        used = 0
        truncated = False
        stop_page = last
        for i in range(first_page, last + 1):
            try:
                t = (reader.pages[i - 1].extract_text() or "").strip()
            except Exception as e:
                t = f"[第 {i} 页解析失败：{e}]"
            if not t:
                t = ("[第 %d 页无可提取文本——很可能是扫描图片页，"
                     "本环境未装 OCR 引擎，无法识别。]" % i)
            block = f"———— P{i} ————\n{t}"
            if used + len(block) > max_chars and parts:
                truncated = True
                stop_page = i - 1
                break
            parts.append(block)
            used += len(block) + 1

        out = "\n".join(parts)
        if truncated:
            out += (f"\n…[已达 {max_chars} 字符上限，正文未读完。"
                    f"请用 pdf_extract(path, first_page={stop_page + 1}, last_page={last}) 续读。]")
        return out

    return [
        make_tool(
            pdf_info,
            name="pdf_info",
            description="读取 PDF 的页数/标题/作者与逐页文本量，判断是否扫描件，规划分页抽取。路径必须在工作目录内。",
        ),
        make_tool(
            pdf_extract,
            name="pdf_extract",
            description="抽取 PDF 指定页范围的正文文本（自动解压 FlateDecode 压缩流），带页码标记；长文档用 first_page/last_page 分次读取。路径必须在工作目录内。",
        ),
    ]
