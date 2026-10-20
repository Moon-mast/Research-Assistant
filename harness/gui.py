"""桌面窗口版（tkinter，零第三方依赖）：python -m harness.gui

复用 harness.Agent。Agent.run() 是阻塞的，因此在后台线程执行，通过
queue + after() 轮询把工具过程 / 回答 / 错误安全地显示到界面，避免 UI 卡死。

顶栏「设置」打开厂商接入面板（预设各厂商 base_url/推荐模型，只需填密钥），
保存后写入 .env 并就地重建 Agent —— CLI 下次启动读取同一份 .env。
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import webbrowser
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import colorchooser, filedialog, font as tkfont, messagebox, ttk
except Exception as e:  # 无图形环境
    tk = None  # type: ignore[assignment]
    _TK_ERROR = e
else:
    _TK_ERROR = None

from . import __version__, providers
from .agent import Agent, IterationLimitError
from .config import HarnessConfig
from .skills import (
    SkillNotFoundError,
    activate_skills,
    default_skills_dir,
    discover,
    load_skill,
)
from .team import Collaboration, guess_critic_model
from .tools import build_ask_tool
from .workspace import FolderWorkspace, build_folder_tools, build_workspace_tools

try:
    from PIL import Image, ImageTk  # type: ignore
    _PIL = True
except Exception:
    Image = ImageTk = None  # type: ignore[assignment]
    _PIL = False

try:
    import cv2  # type: ignore
    _CV2 = True
except Exception:
    cv2 = None  # type: ignore[assignment]
    _CV2 = False

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass


# --------------------------------------------------------------------------
# 复制 / 粘贴支持（"只读但可选中复制"的文本区实现）
# --------------------------------------------------------------------------
def _block_transcript_edit(event):
    """让对话区只读但可选中/可导航：放行 Ctrl 组合键与方向键，拦截输入与删除。"""
    if event.state & 0x0004:  # 按住 Ctrl：放行 Ctrl+A / Ctrl+C 等系统行为
        return None
    ks = event.keysym
    if ks in ("BackSpace", "Delete", "Insert"):
        return "break"
    ch = event.char or ""
    if ch and (ch.isprintable() or ch in "\r\n\t"):
        return "break"
    return None  # 方向键 / Home / End / 翻页等放行


def _set_readonly_selectable(txt) -> None:
    """Text 保持 normal 以支持鼠标选中，但拦截一切编辑输入。"""
    txt.bind("<Key>", _block_transcript_edit)
    txt.bind("<<Paste>>", lambda e: "break")
    txt.bind("<Control-v>", lambda e: "break")
    txt.bind("<<Cut>>", lambda e: "break")
    txt.bind("<Control-x>", lambda e: "break")


def _copy_selection(w) -> None:
    """复制选中文本（Text / Entry 通用）；无选区则忽略。"""
    try:
        s = w.selection_get()
    except Exception:
        s = ""
    if s:
        w.clipboard_clear()
        w.clipboard_append(s)


def _copy_all_text(t) -> None:
    text = t.get("1.0", "end-1c")
    t.clipboard_clear()
    t.clipboard_append(text)


def _select_all_text(t) -> None:
    t.tag_remove("sel", "1.0", "end")
    t.tag_add("sel", "1.0", "end")


def _paste_text(t) -> None:
    t.event_generate("<<Paste>>")


def _text_cut(t) -> None:
    """剪切 Text 中选中内容；无选区则忽略。"""
    try:
        s = t.get("sel.first", "sel.last")
    except Exception:
        return
    if s:
        t.clipboard_clear()
        t.clipboard_append(s)
        t.delete("sel.first", "sel.last")


def _menu_at(widget, event, entries) -> str:
    """在右键位置弹出菜单；entries: [(标签, 命令)]。"""
    menu = tk.Menu(widget, tearoff=0)
    for label, cmd in entries:
        menu.add_command(label=label, command=cmd)
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        try:
            menu.grab_release()
        except Exception:
            pass
    return "break"


def _bind_entry_menu(entry, extra: list | None = None) -> None:
    """给 tk.Entry 绑定右键菜单：复制 / 粘贴 / 全选（可附加项）。"""

    def on_menu(e):
        items = [("复制", lambda: _copy_selection(entry)), ("粘贴", lambda: _paste_entry(entry))]
        items += extra or []
        items.append(("全选", lambda: _select_all_entry(entry)))
        return _menu_at(entry, e, items)

    entry.bind("<Button-3>", on_menu)


def _paste_entry(entry) -> None:
    try:
        s = entry.clipboard_get()
    except Exception:
        s = ""
    if s:
        entry.delete(0, "end")
        entry.insert(0, s)


def _select_all_entry(entry) -> None:
    entry.select_range(0, "end")
    entry.icursor("end")


def _copy_key_raw(entry) -> None:
    """复制输入框内的真实内容（即使 api_key 用 • 掩码显示）。"""
    value = entry.get()
    if value:
        entry.clipboard_clear()
        entry.clipboard_append(value)


# --------------------------------------------------------------------------
# “气泡卡通”主题色板 —— 取自 cartoon-harness-ui.html（galaxy-ui 技能原型稿），
# 转换成 tkinter 可表达的近似：奶油底 / 深葡萄粗描边 / 糖果多巴胺色。
# --------------------------------------------------------------------------
INK        = "#33295e"   # 深葡萄：描边 + 主文字
INK_SOFT   = "#6a5fa0"
CREAM      = "#fff7ea"   # 奶油页底
CREAM_2    = "#fffdf6"   # 面板奶油白（弹窗/输入框）
CARD       = "#ffffff"
SIDE_BG    = "#fff4fb"   # 左栏粉白
TITLE_BG   = "#ffeef4"   # 顶栏浅粉（近似 demo 渐变带主色）
PINK_BG    = "#ffc4de"
SUN_BG     = "#ffe29a"
MINT_BG    = "#c4f4dc"
SKY_BG     = "#bfe3ff"
GRAPE_BG   = "#e9e2ff"
USER_BUB   = "#e8f8ff"
USER_BUB2  = "#e2f6e9"
AV_AI_BG   = "#e9e2ff"
AV_USER_BG = "#fff0c9"
TAG_AI_BG  = "#6f5ce8"
TAG_USER_BG= "#2ec4a0"
SEND_TOP   = "#ffd166"
SEND_DARK  = "#ffb830"
MINT_OK    = "#2ec4a0"
MINT_OK_H  = "#27a98a"
BLUSH      = "#ff9db9"   # 腮红
WALL_CREAM = "#fff7ea"   # 未设置壁纸时聊天区默认底（贴合主题）
_FONT_FAM  = "Microsoft YaHei UI"


def _shade(hexc: str, factor: float = 0.84) -> str:
    """把 #rrggbb 加深 factor 倍，用于 hover / 按下态的糖果色。"""
    hexc = hexc.lstrip("#")
    r, g, b = (int(hexc[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v * factor))) for v in (r, g, b))


PALETTE = {
    "app_bg": CREAM,          # 窗口底色（奶油）
    "bar_bg": TITLE_BG,       # 顶栏
    "bar_fg": INK,            # 顶栏文字（深葡萄）
    "bar_border": INK,        # 顶栏下细线（葡萄描边）
    "btn_bg": CARD,           # 顶栏白胶囊按钮
    "btn_fg": INK,
    "btn_active": GRAPE_BG,
    "accent": MINT_OK,        # 薄荷绿强调（发送/允许）
    "accent_h": MINT_OK_H,
    "accent_soft": MINT_BG,
    "info": "#6f5ce8",        # 紫（模型/提问）
    "danger": "#ff7fb0",      # 粉（拒绝）
    "danger_h": _shade("#ff7fb0"),
    "user": TAG_USER_BG,      # 对话区标签色
    "asst": TAG_USER_BG,
    "tool": "#7a6420",        # 琥珀（工具胶囊文字）
    "meta": "#8a7bb8",
    "ok": MINT_OK,
    "error": "#ff6b8f",
    "text": INK,
    "transcript_bg": CREAM_2,
    "input_border": INK,
    "folder_bg": "#fff8e9",
    "folder_fg": INK_SOFT,
    "approve_bg": "#fff3c9",  # 写入确认条（黄油）
    "approve_fg": "#7a6420",
    "approve_sub": "#a17c2a",
}


# --------------------------------------------------------------------------
# 卡通控件工厂：糖果色胶囊按钮 / 左栏快捷 chip（浅底+粗描边+彩色图标块）
# --------------------------------------------------------------------------
def _cartoon_btn(parent, text, bg=CARD, cmd=None, *, fg=INK, size=10,
                 weight="bold", bd=2, padx=12, pady=4, hover=None, width=None,
                 disabled=False, overrelief="raised", relief="raised"):
    """3D 糖果胶囊按钮：葡萄粗描边 + 圆润浮起；按下自动下沉（近似 CSS 3D）。"""
    hov = hover or _shade(bg)
    kw = dict(text=text, command=cmd, bg=bg, fg=fg,
              activebackground=hov, activeforeground=fg,
              disabledforeground=fg,
              relief=relief, overrelief=overrelief, bd=bd,
              highlightthickness=bd, highlightbackground=INK, highlightcolor=INK,
              font=(_FONT_FAM, size, weight), padx=padx, pady=pady,
              cursor="hand2", takefocus=1)
    if width is not None:
        kw["width"] = width
    b = tk.Button(parent, **kw)
    if disabled:
        b.config(state="disabled")
    return b


def _chip(parent, icon, title, sub, cmd, *, ic_bg=SUN_BG, sub_color=INK_SOFT,
          title_color=INK, bd=2, bg=CARD, icon_size=12):
    """左栏『快捷命令』式 chip：彩色图标块 + 标题 + 副标题，整卡可点。"""
    wrap = tk.Frame(parent, bg=bg, highlightbackground=INK, highlightcolor=INK,
                    highlightthickness=bd, cursor="hand2")
    tile = tk.Label(wrap, text=icon, bg=ic_bg, fg=INK, width=2,
                    font=(_FONT_FAM, icon_size, "bold"), cursor="hand2")
    tile.pack(side="left", padx=(6, 7), pady=3)
    col = tk.Frame(wrap, bg=bg)
    col.pack(side="left", fill="x", expand=True, padx=(0, 6), pady=3)
    tk.Label(col, text=title, bg=bg, fg=title_color, anchor="w",
             font=(_FONT_FAM, 9, "bold"), cursor="hand2").pack(fill="x")
    sub_lbl = tk.Label(col, text=sub, bg=bg, fg=sub_color, anchor="w",
                       justify="left", font=(_FONT_FAM, 7), cursor="hand2")
    sub_lbl.pack(fill="x")
    for w in (wrap, tile, col, *col.winfo_children()):
        w.bind("<Button-1>", lambda e: cmd())
    return wrap, sub_lbl, tile


class _SettingsDialog:
    """厂商接入设置：选厂商 -> 填密钥 -> 保存（写 .env 并即时生效）。"""

    def __init__(self, parent: "tk.Tk", on_apply, current_config: HarnessConfig,
                 on_wallpaper=None, current_wall: str | None = None,
                 on_pick_image=None, on_clear_image=None,
                 on_pick_video=None, on_clear_video=None):
        self.on_apply = on_apply
        self.on_wallpaper = on_wallpaper or (lambda c: None)
        self.on_pick_image = on_pick_image
        self.on_clear_image = on_clear_image
        self.on_pick_video = on_pick_video
        self.on_clear_video = on_clear_video
        self.current_wall = (current_wall or providers.wallpaper()).lower()
        dlg = tk.Toplevel(parent)
        dlg.title("设置 · 厂商接入")
        dlg.transient(parent)
        dlg.resizable(False, False)
        dlg.configure(bg=CREAM_2)
        try:
            dlg.grab_set()
        except Exception:
            pass
        self.dlg = dlg
        fam = self._font_family()

        # —— ttk 下拉框卡通化（仅本弹窗内生效） ——
        try:
            _st = ttk.Style(dlg)
            _st.theme_use("clam")
            _st.configure("Cartoon.TCombobox", fieldbackground=CARD, background=CARD,
                          foreground=INK, bordercolor=INK, lightcolor=INK,
                          darkcolor=INK, arrowcolor=INK, selectbackground=GRAPE_BG,
                          padding=5)
            _st.map("Cartoon.TCombobox",
                    fieldbackground=[("readonly", CARD), ("focus", CARD)],
                    foreground=[("readonly", INK)])
            self._cb_style = "Cartoon.TCombobox"
        except Exception:
            self._cb_style = "TCombobox"

        # —— 卡通头带 ——
        hdr = tk.Frame(dlg, bg=CREAM_2)
        hdr.pack(fill="x", padx=18, pady=(14, 0))
        tk.Label(hdr, text="⚙", bg=SUN_BG, fg=INK, width=2, pady=4,
                 font=(fam, 15, "bold")).pack(side="left")
        tk.Label(hdr, text="厂商接入", bg=CREAM_2, fg=INK,
                 font=(fam, 15, "bold")).pack(side="left", padx=(8, 6))
        tk.Label(hdr, text="选厂商 → 填密钥 → 保存即生效", bg=CREAM_2, fg=INK_SOFT,
                 font=(fam, 8)).pack(side="left")
        _cartoon_btn(hdr, "✕ 关闭", bg=CARD, cmd=dlg.destroy, size=9,
                     padx=12, pady=2).pack(side="right")
        tk.Frame(dlg, bg=INK, height=2).pack(fill="x", padx=14, pady=(8, 0))

        # 密钥框每次打开都保持空白（不自动带出），由用户重新粘贴
        base, _saved_api, _model = providers.current()
        if not base:
            base = current_config.base_url
        _model = _model or current_config.model
        cur_prov = providers.match_provider(base)
        session_key_active = bool(current_config.api_key or _saved_api)

        body = tk.Frame(dlg, bg=CREAM_2, padx=18, pady=14)
        body.pack(fill="both", expand=True)

        def row(label, widget):
            f = tk.Frame(body, bg=CREAM_2)
            f.pack(fill="x", pady=6)
            tk.Label(f, text=label, width=10, anchor="e", bg=CREAM_2, fg=INK,
                     font=(fam, 9, "bold")).pack(side="left", padx=(0, 10))
            widget.pack(side="left", fill="x", expand=True, padx=(0, 2))
            return f

        # 厂商
        self.vendor_var = tk.StringVar(value=cur_prov.name if cur_prov else providers.CUSTOM_NAME)
        self.vendor_cb = ttk.Combobox(body, textvariable=self.vendor_var, state="readonly",
                                      values=providers.provider_names() + [providers.CUSTOM_NAME],
                                      style=self._cb_style)
        row("厂商", self.vendor_cb)
        self.vendor_cb.bind("<<ComboboxSelected>>", lambda e: self._on_vendor())

        # base_url / model / api key
        self.base_var = tk.StringVar(value=base or "https://api.openai.com/v1")
        base_entry = tk.Entry(body, textvariable=self.base_var, relief="flat", bd=0,
                              highlightthickness=2, highlightbackground=INK,
                              highlightcolor=INK, bg=CARD, fg=INK, insertbackground=INK,
                              font=(fam, 9))
        row("base_url", base_entry)
        _bind_entry_menu(base_entry)

        cur_model = _model or current_config.model or ""
        self.model_var = tk.StringVar(value=cur_model or (cur_prov.models[0] if cur_prov and cur_prov.models else ""))
        self.model_cb = ttk.Combobox(body, textvariable=self.model_var,
                                     values=cur_prov.models if cur_prov else [], width=30,
                                     style=self._cb_style)
        row("model", self.model_cb)

        self.key_var = tk.StringVar(value="")  # 每次打开都空白，不自动带出旧密钥
        key_entry = tk.Entry(body, textvariable=self.key_var, show="•", relief="flat", bd=0,
                             highlightthickness=2, highlightbackground=INK,
                             highlightcolor=INK, bg=CARD, fg=INK, insertbackground=INK,
                             font=(fam, 9))
        row("api_key", key_entry)
        key_entry.bind("<Return>", lambda e: self._save())
        _bind_entry_menu(key_entry, extra=[("复制密钥原文", lambda: _copy_key_raw(key_entry))])

        key_hint_text = (
            "窗口每次启动都会清空磁盘上已保存的密钥；本框也每次打开都空白。"
            "当前会话已有生效密钥：留空点保存 = 继续沿用；要更换就粘贴新的。"
            if session_key_active else
            "窗口每次启动都会清空磁盘上已保存的密钥；本框也每次打开都空白。"
            "请粘贴本次要用的 API key，再点「保存并应用」。"
        )
        tk.Label(body, text=key_hint_text, justify="left", anchor="w", bg=CREAM_2, fg=INK_SOFT,
                 wraplength=470, font=(self._font_family(), 8)).pack(fill="x", pady=(0, 2))

        # 提示
        self.note_lbl = tk.Label(body, text="", justify="left", anchor="w", bg=CREAM_2,
                                 fg="#8a5a00", wraplength=470, font=(self._font_family(), 9))
        self.note_lbl.pack(fill="x", pady=(4, 0))

        # —— 多模型协作（主答 + 审查改进）——
        fam = self._font_family()
        tk.Label(body, text="🤝 多模型协作", anchor="w", bg=CREAM_2, fg=PALETTE["info"],
                 font=(fam, 9, "bold")).pack(fill="x", pady=(10, 0))
        self.collab_var = tk.BooleanVar(value=current_config.collab_enabled)
        tk.Checkbutton(
            body, text="启用「主答 + 审查改进」：主模型带工具作答后，用另一个模型把关，不满意则打回修改",
            variable=self.collab_var, bg=CREAM_2, activebackground=CREAM_2,
            selectcolor=MINT_BG, font=(fam, 9), wraplength=460, justify="left",
            anchor="w", fg=INK,
        ).pack(fill="x", anchor="w")
        crit = tk.Frame(body, bg=CREAM_2)
        crit.pack(fill="x", pady=(4, 0))
        tk.Label(crit, text="审查模型", width=10, anchor="e", bg=CREAM_2, fg=INK,
                 font=(fam, 9, "bold")).pack(side="left", padx=(0, 8))
        self.critic_var = tk.StringVar(value="")
        self.critic_cb = ttk.Combobox(crit, textvariable=self.critic_var,
                                      values=cur_prov.models if cur_prov else [], width=30,
                                      style=self._cb_style)
        self.critic_cb.pack(side="left", fill="x", expand=True)
        tk.Label(
            body,
            text="审查用同厂商的轻量模型即可（同一密钥）；例如 DeepSeek 主答 v4-pro、审查 v4-flash。留空＝自动选该厂商默认轻量模型。",
            justify="left", anchor="w", bg=CREAM_2, fg=INK_SOFT, wraplength=470,
            font=(fam, 8),
        ).pack(fill="x", pady=(2, 0))
        self._on_vendor()  # 根据当前厂商填好 base_url/模型/审查模型与提示

        # —— 聊天区壁纸 ——
        tk.Label(body, text="🎨 聊天区壁纸", anchor="w", bg=CREAM_2, fg=PALETTE["info"],
                 font=(fam, 9, "bold")).pack(fill="x", pady=(10, 0))
        swatches = tk.Frame(body, bg=CREAM_2)
        swatches.pack(fill="x", pady=(4, 0))
        presets = [(WALL_CREAM, "奶油"), ("#ffffff", "默认白"), ("#fbf6ee", "暖白"),
                   ("#eaf2fb", "雾蓝"), ("#e9f7f0", "薄荷"), ("#f3eefa", "淡紫"),
                   ("#23272e", "碳黑")]
        for hexc, name in presets:
            sel = hexc == self.current_wall
            btn = tk.Button(swatches, text="✓" if sel else name[:2], bg=hexc,
                            relief="flat", width=6, pady=2, bd=0,
                            activebackground=hexc, cursor="hand2", font=(fam, 7, "bold"),
                            fg="#33295e", command=lambda h=hexc: self._pick_wall(h))
            btn.pack(side="left", padx=(0, 6))
            if sel:
                btn.config(highlightbackground="#33295e", highlightthickness=2)
        tk.Label(body, text="选整块底色即自动切深/浅文字配色；点选后即时应用、写 .env 持久化。",
                 anchor="w", bg=CREAM_2, fg=INK_SOFT, font=(fam, 8), wraplength=470,
                 justify="left").pack(fill="x", pady=(2, 0))
        wall_row = tk.Frame(body, bg=CREAM_2)
        wall_row.pack(fill="x", pady=(4, 0))
        _cartoon_btn(wall_row, "🎨 自定义颜色…", bg=SUN_BG, cmd=self._pick_custom,
                     size=9, padx=8, pady=2).pack(side="left", padx=(0, 6))
        _cartoon_btn(wall_row, "↺ 恢复默认奶油", bg=CARD, cmd=lambda: self._pick_wall(WALL_CREAM),
                     size=9, padx=8, pady=2).pack(side="left")

        # —— 图片 / 动态 GIF 壁纸 ——
        tk.Label(body, text="🖼 图片 / GIF 壁纸", anchor="w", bg=CREAM_2, fg=PALETTE["info"],
                 font=(fam, 9, "bold")).pack(fill="x", pady=(8, 0))
        img_row = tk.Frame(body, bg=CREAM_2)
        img_row.pack(fill="x", pady=(4, 0))
        _cartoon_btn(img_row, "📁 从文件夹选图片 / GIF…", bg=SKY_BG,
                     cmd=self._pick_image_btn, size=9, padx=8, pady=2).pack(side="left", padx=(0, 6))
        _cartoon_btn(img_row, "清除图片", bg=CARD, cmd=self._clear_image_btn,
                     size=9, padx=8, pady=2).pack(side="left")
        tk.Label(body,
                 text="支持 png/jpg/bmp/webp；动态 GIF 会自动播放。图片壁纸会铺满聊天区、气泡盖在图上。",
                 anchor="w", bg=CREAM_2, fg=INK_SOFT, font=(fam, 8), wraplength=470,
                 justify="left").pack(fill="x", pady=(2, 0))

        # —— 视频动态壁纸 ——
        tk.Label(body, text="🎬 视频动态壁纸", anchor="w", bg=CREAM_2, fg=PALETTE["info"],
                 font=(fam, 9, "bold")).pack(fill="x", pady=(8, 0))
        vid_row = tk.Frame(body, bg=CREAM_2)
        vid_row.pack(fill="x", pady=(4, 0))
        _cartoon_btn(vid_row, "🎞 选择视频 (mp4/avi…)…", bg=MINT_BG,
                     cmd=self._pick_video_btn, size=9, padx=8, pady=2).pack(side="left", padx=(0, 6))
        _cartoon_btn(vid_row, "清除视频", bg=CARD, cmd=self._clear_video_btn,
                     size=9, padx=8, pady=2).pack(side="left")
        tk.Label(body,
                 text="用 OpenCV 后台解码并循环播放（需 opencv-python）；选视频会覆盖图片/GIF 壁纸。",
                 anchor="w", bg=CREAM_2, fg=INK_SOFT, font=(fam, 8), wraplength=470,
                 justify="left").pack(fill="x", pady=(2, 0))

        # 按钮
        btns = tk.Frame(dlg, bg=CREAM_2)
        btns.pack(fill="x", pady=(8, 14))
        _cartoon_btn(btns, "💾 保存并应用", bg=MINT_BG, cmd=self._save,
                     size=11, padx=18, pady=5).pack(side="right", padx=8)
        _cartoon_btn(btns, "取消", bg=CARD, cmd=dlg.destroy, size=10,
                     padx=16, pady=4).pack(side="right")

    @staticmethod
    def _font_family() -> str:
        return "Microsoft YaHei UI"

    def _on_vendor(self) -> None:
        name = self.vendor_var.get()
        prov = providers.provider_by_name(name)
        if name == providers.CUSTOM_NAME or prov is None:
            self.note_lbl.config(text="自定义模式：手动填 base_url（含 /v1 等路径前缀）与 model。")
            return
        if self.base_var.get() != prov.base_url:
            self.base_var.set(prov.base_url)
        # 模型下拉换成该厂商建议列表；当前值仍在列表里则保留
        vals = prov.models
        self.model_cb.config(values=vals)
        cur = self.model_var.get()
        if cur not in vals and vals:
            self.model_var.set(vals[0])
        # 审查模型下拉同样跟随厂商，默认取该厂商的轻量模型
        self.critic_cb.config(values=vals)
        ccur = self.critic_var.get()
        cdefault = prov.light or (vals[0] if vals else "")
        if (not ccur) or (ccur not in vals):
            self.critic_var.set(cdefault)
        note = prov.note or ""
        if not prov.needs_key:
            note = (note + " " if note else "") + "本地接口，api_key 可填任意占位（如 ollama）。"
        self.note_lbl.config(text=note)

    def _pick_wall(self, color: str) -> None:
        """选中壁纸色：持久化到 .env 并即时应用到主窗口。"""
        try:
            providers.set_wallpaper(color)
        except ValueError as e:
            messagebox.showerror("壁纸设置失败", str(e))
            return
        eff = (color or "#ffffff").lower()
        self.current_wall = eff
        self.on_wallpaper(eff)

    def _pick_custom(self) -> None:
        try:
            _rgb, hexc = colorchooser.askcolor(color=self.current_wall, parent=self.dlg)
        except Exception:
            hexc = None
        if hexc:
            self._pick_wall(hexc)

    def _pick_image_btn(self) -> None:
        if self.on_pick_image is not None:
            self.on_pick_image()

    def _clear_image_btn(self) -> None:
        if self.on_clear_image is not None:
            self.on_clear_image()

    def _pick_video_btn(self) -> None:
        if self.on_pick_video is not None:
            self.on_pick_video()

    def _clear_video_btn(self) -> None:
        if self.on_clear_video is not None:
            self.on_clear_video()

    def _save(self) -> None:
        base_url = self.base_var.get().strip()
        model = self.model_var.get().strip()
        api_key = self.key_var.get().strip()
        name = self.vendor_var.get()

        if not base_url.startswith("http"):
            messagebox.showerror("无法保存", "base_url 需要以 http(s):// 开头。")
            return
        if not model:
            messagebox.showerror("无法保存", "请填写 model（或从下拉选择）。")
            return
        prov = providers.provider_by_name(name)
        if prov is not None and prov.needs_key and not api_key:
            messagebox.showerror("无法保存",
                                 f"{prov.name} 需要 API key。\n（本地 Ollama 之类可填任意占位，见提示）")
            return

        providers.apply_credentials(base_url=base_url, api_key=api_key, model=model)

        # 多模型协作开关 + 审查模型（写 .env / 同步环境变量）
        collab_on = bool(self.collab_var.get())
        critic = self.critic_var.get().strip()
        if collab_on and not critic:
            critic = self.model_var.get().strip()
        providers.set_collab(enabled=collab_on, critic_model=critic)

        vendor_label = prov.name if prov else ("自定义" if name == providers.CUSTOM_NAME else name)
        self.dlg.destroy()
        self.on_apply(vendor_label)

_SIDEBAR = PALETTE["app_bg"]


class _ChatWindow:
    """DeepSeek 式窗口：左侧会话栏 + 主区气泡对话（仍为 tkinter 原生）。"""

    FONT = "Microsoft YaHei UI"
    SIDEBAR_W = 232
    MAX_RECORDS = 500

    def __init__(self, root: "tk.Tk", config: HarnessConfig):
        self.root = root
        self.config = config
        self.events: "queue.Queue[tuple]" = queue.Queue()
        self._busy = False
        self._settings_dialog = None
        self.workspace: "FolderWorkspace | None" = None
        self._pending_ask = None       # (answer_dict, event) —— 等待用户内联决定写入
        self._pending_q = None         # (answer_dict, event) —— 等待用户回答模型提问
        self._auto_allow_writes = False
        # 聊天区壁纸：仅在 .env 显式配置过 CHAT_WALLPAPER 时用其值，否则默认奶油底贴合卡通主题
        import os as _os
        _env_wall = (_os.environ.get("CHAT_WALLPAPER") or "").strip()
        self.wall = _env_wall if (_env_wall.startswith("#") and len(_env_wall) == 7) \
            else WALL_CREAM
        self._wall_dark = False
        # —— 壁纸图片 / 动态 GIF ——
        self._wall_image_path = None       # 正在使用的图片文件
        self._wall_photo = None            # 当前帧 tk.PhotoImage
        self._wall_frames: list = []       # GIF 动画各帧
        self._wall_index = -1
        self._wall_job: str | None = None  # after() 任务 id
        self._fonts: dict = {}             # 字体缓存：样式 -> tkfont.Font
        # —— 视频动态壁纸 ——
        self._video_path: str | None = None
        self._video_thread: threading.Thread | None = None
        self._video_stop = False
        self._video_q: "queue.Queue" = queue.Queue(maxsize=3)

        # —— 会话（侧栏可切换的多段对话）——
        self.sessions: list[dict] = []
        self.cur: dict | None = None
        self._cfg_version = 0

        # —— 本地技能（skills/ 下检测到即自动启用）——
        self.skill_dir = default_skills_dir()
        self.skills = discover(self.skill_dir)
        self._loaded_skills: list[str] = []      # /skill 手动预载过的技能名

        # DPI
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
        except Exception:
            pass

        self._build_ui()
        self._apply_wallpaper(self.wall)             # 先应用壁纸配色
        self._new_session(title="新对话", announce=False)   # 建立首个会话
        self._recreate_agent(config, vendor_label=None, announce=False)
        self.root.after(100, self._poll_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._show_startup()
        self.root.after(200, self._restore_saved_wall_pref)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        self.root.title(f"harness v{__version__} —— 大模型 agent（气泡卡通）")
        self.root.geometry("1080x720")
        self.root.minsize(760, 520)
        self.root.configure(bg=CREAM)
        fam = self.FONT
        try:
            base = tkfont.nametofont("TkDefaultFont")
            base.configure(family=fam, size=11)
        except Exception:
            pass

        def tinted_bar(parent):
            return tk.Frame(parent, bg=CARD, highlightbackground=INK,
                            highlightcolor=INK, highlightthickness=2)

        # ============ 顶栏：logo pill + 模型胶囊 + 糖果按钮 ============
        bar = tk.Frame(self.root, bg=TITLE_BG)
        bar.pack(side="top", fill="x")

        logo = tinted_bar(bar)
        tk.Label(logo, text="🦄", bg=SUN_BG, fg=INK, font=(fam, 12, "bold")).pack(
            side="left", padx=2, pady=2)
        tk.Label(logo, text="harness · 气泡卡通版", bg=CARD, fg=INK,
                 font=(fam, 11, "bold")).pack(side="left", padx=(6, 14), pady=4)
        logo.pack(side="left", padx=(12, 8), pady=6)

        model_chip = tinted_bar(bar)
        self.model_text = tk.StringVar(value="")
        tk.Label(model_chip, text="●", bg=CARD, fg=MINT_OK,
                 font=(fam, 9)).pack(side="left")
        tk.Label(model_chip, textvariable=self.model_text, bg=CARD, fg=INK,
                 font=(fam, 9, "bold")).pack(side="left", padx=(5, 12), pady=4)
        model_chip.pack(side="left", padx=(0, 8), pady=6)

        tool_right = tk.Frame(bar, bg=TITLE_BG)
        tool_right.pack(side="right", padx=10, pady=6)
        for text, bg, cmd in (("📁 文件夹", SUN_BG, self._choose_folder),
                              ("🧩 技能", GRAPE_BG, lambda: self._command("skills")),
                              ("⚙ 设置", PINK_BG, self._open_settings)):
            _cartoon_btn(tool_right, text, bg=bg, cmd=cmd, size=10, padx=13, pady=4
                         ).pack(side="left", padx=3)
        tk.Frame(self.root, bg=INK, height=3).pack(side="top", fill="x")

        # ============ 主体：左栏 + 右主区 ============
        body = tk.Frame(self.root, bg=CREAM)
        body.pack(side="top", fill="both", expand=True)

        side = tk.Frame(body, bg=SIDE_BG, width=self.SIDEBAR_W)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        # —— 左栏 · 新建对话 ——
        _cartoon_btn(side, "＋ 新对话", bg=MINT_BG, cmd=self._command_new,
                     size=11, padx=8, pady=5).pack(fill="x", padx=12, pady=(10, 6))

        # —— 左栏 · 快捷命令 ——
        tk.Label(side, text="快捷命令", bg=SIDE_BG, fg=INK_SOFT,
                 font=(fam, 8, "bold")).pack(fill="x", padx=14, pady=(2, 2))
        def quick_chip(icon, title, sub, cmd, ic_bg):
            w, s, t = _chip(side, icon, title, sub, cmd, ic_bg=ic_bg)
            w.pack(fill="x", padx=12, pady=3)
            return s, t

        self._skills_sub, self._skills_tile = quick_chip(
            "🧩", "查看技能", f"已启用 {len(self.skills)} 个",
            lambda: self._command("skills"), MINT_BG)
        self._folder_sub, self._folder_tile = quick_chip(
            "📁", "切换目录", "chdir 到新工作区", self._choose_folder, SUN_BG)
        self._collab_sub, self._collab_tile = quick_chip(
            "🤝", "多模型协作", "主答 + 审查", self._toggle_collab_chip, MINT_BG)
        quick_chip("🧹", "清空对话", "重置上下文",
                   lambda: self._command("clear"), GRAPE_BG)

        # —— 左栏 · 底部固定（文件夹 / 已激活工具 / 脚注，先占底）——
        side_bottom = tk.Frame(side, bg=SIDE_BG)
        side_bottom.pack(side="bottom", fill="x")
        self.folder_var = tk.StringVar(value="工作目录：未选择")
        tk.Label(side_bottom, textvariable=self.folder_var, bg=SIDE_BG, fg=INK_SOFT,
                 anchor="w", justify="left", font=(fam, 8),
                 wraplength=self.SIDEBAR_W - 30).pack(fill="x", padx=14, pady=(4, 0))
        tools_card = tk.Frame(side_bottom, bg="#fff8e9", highlightbackground=INK,
                              highlightcolor=INK, highlightthickness=2)
        tools_card.pack(fill="x", padx=12, pady=(4, 4))
        tk.Label(tools_card, text="🛠 已激活工具", bg="#fff8e9", fg=INK,
                 font=(fam, 8, "bold"), anchor="w").pack(fill="x", padx=8, pady=(4, 0))
        self._tools_var = tk.StringVar(value="✍ 写入确认 · 🧠 审查协作 · 📁 文件夹工具")
        tk.Label(tools_card, textvariable=self._tools_var, bg="#fff8e9", fg="#7a6420",
                 anchor="w", justify="left", font=(fam, 8),
                 wraplength=self.SIDEBAR_W - 40).pack(fill="x", padx=8, pady=(1, 5))
        tk.Label(side_bottom, text="Enter 发送 · Shift+Enter 换行\n右键对话区可复制",
                 bg=SIDE_BG, fg=INK_SOFT, justify="left", font=(fam, 8),
                 anchor="w").pack(fill="x", padx=14, pady=(0, 5))

        # —— 左栏 · 对话列表（中段可伸缩）——
        tk.Label(side, text="对话", bg=SIDE_BG, fg=INK_SOFT,
                 font=(fam, 8, "bold")).pack(fill="x", padx=14, pady=(6, 2))
        list_card = tk.Frame(side, bg=CARD, highlightbackground=INK,
                             highlightcolor=INK, highlightthickness=2)
        list_card.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.side_list = tk.Listbox(list_card, bg=CARD, fg=INK,
                                    selectbackground=MINT_BG, selectforeground=INK,
                                    activestyle="none", relief="flat", borderwidth=0,
                                    highlightthickness=0, font=(fam, 9),
                                    exportselection=False, bd=0)
        self.side_list.pack(fill="both", expand=True, padx=4, pady=4)
        self.side_list.bind("<<ListboxSelect>>", self._on_side_select)
        self.side_list.bind("<Button-3>", self._on_side_menu)

        # ============ 右主区 ============
        main = tk.Frame(body, bg=CREAM)
        main.pack(side="left", fill="both", expand=True)

        # 对话画布（气泡 + 壁纸）
        chat_host = tk.Frame(main, bg=self.wall)
        chat_host.pack(fill="both", expand=True, padx=12, pady=(8, 0))
        self.chat_host = chat_host
        self.chat_canvas = tk.Canvas(chat_host, bg=self.wall, highlightthickness=0, bd=0)
        vsb = tk.Scrollbar(chat_host, orient="vertical", command=self.chat_canvas.yview,
                           relief="flat", width=11)
        self.chat_canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.chat_canvas.pack(side="left", fill="both", expand=True)
        self.chat_canvas.bind("<Configure>", self._on_canvas_resize)
        self.chat_canvas.bind("<MouseWheel>", self._on_wheel)
        self.chat_canvas.bind("<Button-4>", self._on_wheel_linux)
        self.chat_canvas.bind("<Button-5>", self._on_wheel_linux)
        self.chat_canvas.bind("<Button-3>", self._on_chat_menu)

        # 内联确认条（黄油胶囊）
        self.approve_bar = tk.Frame(main, bg=PALETTE["approve_bg"],
                                    highlightbackground=INK, highlightcolor=INK,
                                    highlightthickness=2)
        self.bar_text = tk.StringVar(value="")
        tk.Label(self.approve_bar, textvariable=self.bar_text, anchor="w",
                 bg=PALETTE["approve_bg"], fg=PALETTE["approve_fg"],
                 justify="left", font=(fam, 10, "bold")).pack(fill="x", padx=14, pady=(6, 0))
        ab = tk.Frame(self.approve_bar, bg=PALETTE["approve_bg"])
        ab.pack(fill="x", padx=14, pady=(3, 8))
        _cartoon_btn(ab, "✔ 允许写入", bg=MINT_BG, cmd=lambda: self._resolve_ask(True),
                     size=9, padx=10, pady=2).pack(side="left", padx=(0, 6))
        _cartoon_btn(ab, "本会话都允许", bg=TAG_AI_BG, fg="#ffffff",
                     cmd=lambda: self._resolve_ask("auto"), size=9, padx=10, pady=2
                     ).pack(side="left", padx=(0, 6))
        _cartoon_btn(ab, "✘ 拒绝", bg="#ffb3d1", cmd=lambda: self._resolve_ask(False),
                     size=9, padx=10, pady=2).pack(side="left", padx=(0, 6))
        tk.Label(ab, text="空白回车=允许；输入理由后回车=拒绝并说明",
                 bg=PALETTE["approve_bg"], fg=PALETTE["approve_sub"],
                 font=(fam, 8)).pack(side="left", padx=4)
        self.approve_bar.pack(fill="x")
        self.approve_bar.pack_forget()

        # 底部输入区
        bottom = tk.Frame(main, bg=CREAM)
        bottom.pack(fill="x", padx=12, pady=(4, 0))
        self.bottom = bottom

        # 快捷模板行
        scrow = tk.Frame(bottom, bg=CREAM)
        scrow.pack(fill="x", pady=(4, 6))
        for label, tpl in (("📁 /folder", "/folder"), ("🧩 /skills", "/skills"),
                           ("🛠 /tools", "/tools"), ("🧹 /clear", "/clear"),
                           ("❓ /help", "/help")):
            _cartoon_btn(scrow, label, bg=GRAPE_BG, size=8, padx=8, pady=1,
                         cmd=lambda t=tpl: self._preset(t)).pack(side="left", padx=(0, 6))

        inputrow = tk.Frame(bottom, bg=CREAM)
        inputrow.pack(fill="x")

        # 输入框：葡萄粗描边白底
        box = tk.Frame(inputrow, bg=INK)
        box.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self.input = tk.Text(box, height=3, wrap="word", font=(fam, 11),
                             bg=CARD, fg=INK, insertbackground=INK,
                             relief="flat", borderwidth=0, padx=12, pady=8)
        self.input.pack(fill="both", expand=True, padx=2, pady=2)
        self.input.bind("<Return>", self._on_key)
        self.input.bind("<KP_Enter>", self._on_key)
        self.input.bind("<Button-3>", self._on_input_menu)

        self._emoji_list = ["😊", "🎨", "🍭", "🫧", "⭐", "🚀", "🐻", "😎"]
        self._emoji_i = 0
        _cartoon_btn(inputrow, "🙂", bg=SKY_BG, size=11, padx=9, pady=9,
                     cmd=self._insert_emoji).pack(side="left", padx=(0, 8))

        self.send_btn = _cartoon_btn(inputrow, "🚀 发送", bg=SEND_TOP,
                                     cmd=self._do_send, size=12, padx=16, pady=8)
        self.send_btn.pack(side="right")

        self.status = tk.Label(main, text="就绪", anchor="w", bg=CREAM, fg=MINT_OK,
                               font=(fam, 9, "bold"))
        self.status.pack(fill="x", padx=18, pady=(4, 4))

        self._refresh_sidebar_state()

    # ------------------------------------------------- 快捷/杂项钩子
    def _preset(self, text: str) -> None:
        """把快捷模板填入输入框并聚焦（同 demo .gx-sc：点一下塞进命令）。"""
        self.input.delete("1.0", "end")
        self.input.insert("1.0", text + " ")
        self.input.focus_set()

    def _insert_emoji(self) -> None:
        e = self._emoji_list[self._emoji_i % len(self._emoji_list)]
        self._emoji_i += 1
        self.input.insert("end", e)
        self.input.focus_set()

    def _toggle_collab_chip(self) -> None:
        """左栏「多模型协作」chip：直接切换 主答+审查 协作开关。"""
        if self._busy:
            self._append("（正在处理，稍后再切换协作）", "error")
            return
        base, _api, _model = providers.current()
        if not base:
            self._append("尚未接入厂商：请先点顶栏「⚙ 设置」填好密钥再开协作。", "error")
            return
        new_on = not self.config.collab_enabled
        providers.set_collab(enabled=new_on, critic_model=self.config.collab_critic_model or "")
        self._recreate_agent(HarnessConfig.from_env(), vendor_label=None, announce=True)
        self._refresh_sidebar_state()

    def _refresh_sidebar_state(self) -> None:
        on = bool(self.config.collab_enabled)
        if getattr(self, "_collab_sub", None) is not None:
            self._collab_sub.config(text="主答 + 审查改进 · 已开启" if on
                                    else "主答 + 审查改进 · 已关闭")
        if getattr(self, "_collab_tile", None) is not None:
            self._collab_tile.config(bg=MINT_BG if on else PINK_BG)
        if getattr(self, "_skills_sub", None) is not None:
            self._skills_sub.config(text=f"已启用 {len(self.skills)} 个本地技能")
        if getattr(self, "_tools_var", None) is not None:
            bits = ["✍ 写入确认"]
            if on:
                bits.append("🧠 审查协作")
            if self.workspace is not None:
                bits.append("📁 文件夹工具")
            self._tools_var.set(" · ".join(bits))

    # ------------------------------------------------------------- 会话管理
    def _new_session(self, title: str = "新对话", announce: bool = True) -> dict:
        s = {"title": title, "records": [], "agent": None, "collab": None, "version": -1}
        self.sessions.append(s)
        self._refresh_side_list()
        self._switch_to(s)
        if announce:
            self._append("—— 新对话 ——", "meta")
        return s

    def _refresh_side_list(self) -> None:
        self.side_list.delete(0, "end")
        for s in self.sessions:
            self.side_list.insert("end", s["title"])
        if self.cur in self.sessions:
            self.side_list.selection_clear(0, "end")
            self.side_list.selection_set(self.sessions.index(self.cur))
            self.side_list.activate(self.sessions.index(self.cur))

    def _switch_to(self, s: dict) -> None:
        self.cur = s
        self._refresh_side_list()
        self._render_records()
        self._bind_active()
        self.status.config(text="就绪" if not self._busy else "思考中…")

    def _on_side_select(self, _e) -> None:
        sel = self.side_list.curselection()
        if sel and 0 <= sel[0] < len(self.sessions):
            self._switch_to(self.sessions[sel[0]])

    def _on_side_menu(self, e) -> str:
        idx = self.side_list.nearest(e.y)
        entries = []
        if 0 <= idx < len(self.sessions):
            entries.append(("删除此对话", lambda: self._delete_session(self.sessions[idx])))
        entries.append(("新对话", self._command_new))
        return _menu_at(self.side_list, e, entries)

    def _delete_session(self, s: dict) -> None:
        if len(self.sessions) <= 1:
            self._append("至少保留一个对话。", "meta")
            return
        self.sessions.remove(s)
        if self.cur is s:
            self._switch_to(self.sessions[-1])
        else:
            self._refresh_side_list()

    def _command_new(self) -> None:
        if self._busy:
            self._append("（正在处理，稍后再开新对话）", "error")
            return
        self._new_session()

    # ---------------------------------------------------- Agent 绑定(会话级)
    def _ensure_skills(self, target) -> None:
        """把技能（工具 + 清单 + 已预载项）接到 Agent 或协作主答；可重复调用。"""
        if not self.skills:
            return
        activate_skills(
            target,
            skills_dir=self.skill_dir,
            get_workspace=lambda: self.workspace,
            open_html=lambda p: self.events.put(("open_html", p)),
            preload=tuple(self._loaded_skills),
        )

    def _build_agent(self) -> Agent:
        a = Agent(self.config, on_tool_start=self._on_tool_start,
                  on_tool_end=self._on_tool_end, confirm_handler=self._confirm_write,
                  ask_handler=self._ask_user)
        if self.workspace is not None:
            a.add_tools(build_workspace_tools(self.workspace))
        a.add_tools([build_ask_tool()])
        self._ensure_skills(a)
        return a

    def _bind_active(self) -> None:
        """确保当前会话有与本配置版本一致的 agent；同步到 self._agent（兼容旧引用）。"""
        s = self.cur
        if s is None:
            return
        if s.get("agent") is None or s.get("version") != self._cfg_version:
            s["agent"] = self._build_agent()
            s["version"] = self._cfg_version
        self._agent = s["agent"]

    def _active_collab(self) -> Collaboration:
        s = self.cur
        if s.get("collab") is None:
            s["collab"] = Collaboration(
                self.config, critic_model=guess_critic_model(self.config),
                on_event=lambda txt: self._append(txt, "meta"),
                on_tool_start=self._on_tool_start, on_tool_end=self._on_tool_end,
                confirm_handler=self._confirm_write, ask_handler=self._ask_user)
            extra = [build_ask_tool()]
            if self.workspace is not None:
                extra += build_workspace_tools(self.workspace)
            s["collab"].add_tools(extra)
            self._ensure_skills(s["collab"])
        return s["collab"]

    # ------------------------------------------------------- 壁纸 / 配色
    @staticmethod
    def _hex_luma(hexc: str) -> float:
        hexc = hexc.lstrip("#")
        r, g, b = (int(hexc[i:i + 2], 16) for i in (0, 2, 4))
        return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0

    def _chat_colors(self) -> dict:
        """按当前壁纸深浅返回气泡/文字配色（气泡卡通系）。"""
        if self._wall_dark:
            return {
                "user_bg": "#4a3f6b", "user_fg": "#f2ecff",
                "ans_bg": "#3c3a5c", "ans_fg": "#efeaff",
                "code_bg": "#2e2c47", "code_fg": "#e4defa",
                "head": "#c6a6ff", "tool": "#ffd98a",
                "meta": "#b9b1e0", "ok": "#7fe3b4",
                "error": "#ff9ab8", "plain": "#efeaff",
                "tool_bg": "#554a1f",
            }
        return {
            "user_bg": USER_BUB, "user_fg": "#17314e",
            "ans_bg": CARD, "ans_fg": INK,
            "code_bg": GRAPE_BG, "code_fg": INK,
            "head": "#6f5ce8", "tool": "#7a6420",
            "meta": "#8a7bb8", "ok": MINT_OK,
            "error": "#e2506e", "plain": INK,
            "tool_bg": "#fff3c9",
        }

    # ------------------------------------------------------- 字体 / 文本换行
    def _font(self, spec) -> "tkfont.Font":
        key = str(spec)
        f = self._fonts.get(key)
        if f is None:
            if spec[0] == "ui":
                f = tkfont.Font(family=self.FONT, size=spec[1], weight=spec[2] if len(spec) > 2 else "normal")
            else:  # mono
                f = tkfont.Font(family="Consolas", size=spec[1])
            self._fonts[key] = f
        return f

    @staticmethod
    def _wrap_lines(text: str, font: "tkfont.Font", max_px: int) -> list[str]:
        """按像素宽度手工折行（处理中英混排、保留换行）。"""
        out: list[str] = []
        for raw in text.split("\n"):
            if not raw:
                out.append("")
                continue
            line = ""
            for ch in raw:
                trial = line + ch
                if font.measure(trial) > max_px and line:
                    out.append(line)
                    line = ch
                else:
                    line = trial
            out.append(line)
        return out

    # ------------------------------------------------------------- 壁纸应用
    def _cancel_wall_anim(self) -> None:
        if self._wall_job:
            try:
                self.root.after_cancel(self._wall_job)
            except Exception:
                pass
            self._wall_job = None

    def _clear_wall_image(self) -> None:
        self._cancel_wall_anim()
        self._stop_video()
        self._wall_image_path = None
        self._wall_photo = None
        self._wall_frames = []
        self._wall_index = -1
        self.chat_canvas.delete("wall")

    # ------------------------------------------------------- 视频动态壁纸
    def _stop_video(self, join: bool = True) -> None:
        self._video_stop = True
        if self._video_thread and join:
            try:
                self._video_thread.join(timeout=1.0)
            except Exception:
                pass
        self._video_thread = None
        self._video_path = None
        try:
            while not self._video_q.empty():
                self._video_q.get_nowait()
        except Exception:
            pass

    def _video_worker(self, cap, w: int, h: int, fps: float) -> None:
        self._video_stop = False
        import time as _time
        interval = 1.0 / max(fps or 25.0, 1.0)
        while not self._video_stop:
            ok, frame = cap.read()
            if not ok:
                try:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # 循环播放
                except Exception:
                    break
                continue
            fh, fw = frame.shape[:2]
            scale = max(w / fw, h / fh)
            nw, nh = max(1, int(fw * scale)), max(1, int(fh * scale))
            resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_AREA)
            x0, y0 = (nw - w) // 2, (nh - h) // 2
            cropped = resized[y0:y0 + h, x0:x0 + w]
            try:
                while True:
                    try:
                        self._video_q.get_nowait()
                    except Exception:
                        break
                self._video_q.put_nowait(cropped)
            except Exception:
                pass
            _time.sleep(interval * 0.8)

    def _video_pump(self) -> None:
        if not self._video_stop and self._video_path is not None:
            try:
                frame = self._video_q.get_nowait()
            except Exception:
                frame = None
            if frame is not None:
                try:
                    img = Image.fromarray(frame[:, :, ::-1])  # BGR->RGB
                    photo = ImageTk.PhotoImage(img)
                    self._wall_photo = photo
                    if not self.chat_canvas.find_withtag("wall"):
                        self.chat_canvas.create_image(0, 0, anchor="nw",
                                                      image=photo, tags=("wall",))
                    else:
                        self.chat_canvas.itemconfigure("wall", image=photo)
                    self.chat_canvas.tag_lower("wall")
                except Exception:
                    pass
        if self._video_path is not None and not self._video_stop:
            self.root.after(33, self._video_pump)

    def _set_wallpaper_video(self, path: str, persist: bool = True) -> str | None:
        """把 mp4/avi 等视频设为动态壁纸（后台解码 → 主线程逐帧绘制）。"""
        import os as _os
        path = str(path)
        if not _os.path.isfile(path):
            return "文件不存在"
        if not _CV2:
            return "需要 opencv（先执行 pip install opencv-python）"
        self._clear_wall_image()
        try:
            cap = cv2.VideoCapture(path)
            if not cap.isOpened():
                return "无法打开该视频（编码可能不受支持）"
            w = max(self.chat_canvas.winfo_width(), 640)
            h = max(self.chat_canvas.winfo_height(), 480)
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            ok, first = cap.read()
            if ok:
                small = cv2.resize(first, (32, 32))
                self._wall_dark = float(small.mean() / 255.0) < 0.55
        except Exception as e:
            return f"视频初始化失败：{e}"
        self._video_path = path
        self._video_q = queue.Queue(maxsize=3)
        self._video_stop = False
        self._video_thread = threading.Thread(
            target=self._video_worker, args=(cap, w, h, fps), daemon=True)
        self._video_thread.start()
        self.root.after(33, self._video_pump)
        if persist:
            providers.set_wallpaper_video(path)
        if self.cur is not None:
            self._redraw_all()
            self._scroll_bottom()
        return None

    def _pick_wallpaper_video(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root, title="选择视频作为动态壁纸",
            filetypes=[("视频", "*.mp4 *.avi *.mkv *.webm *.mov"), ("所有文件", "*.*")])
        if not path:
            return
        err = self._set_wallpaper_video(path, persist=True)
        if err:
            messagebox.showerror("壁纸设置失败", err)

    def _clear_wallpaper_video(self) -> None:
        providers.set_wallpaper_video(None)
        self._stop_video()
        self.chat_canvas.delete("wall")
        self.chat_host.configure(bg=self.wall)
        self.chat_canvas.configure(bg=self.wall)
        if self.cur is not None:
            self._redraw_all()
            self._scroll_bottom()

    def _on_close(self) -> None:
        self._video_stop = True
        try:
            self.root.destroy()
        except Exception:
            pass

    def _apply_wallpaper(self, hexc: str) -> None:
        """切回纯色壁纸（清除图片），并即时重绘。"""
        hexc = (hexc or "").strip()
        if not hexc.startswith("#") or len(hexc) != 7:
            return
        self._clear_wall_image()
        self.wall = hexc
        self._wall_dark = self._hex_luma(hexc) < 0.5
        self.chat_host.configure(bg=hexc)
        self.chat_canvas.configure(bg=hexc)
        if self.cur is not None:
            self._redraw_all()
            self._scroll_bottom()

    def _set_wallpaper_image(self, path: str, persist: bool = True) -> str | None:
        """加载图片/GIF 为聊天区壁纸。返回错误信息或 None。"""
        import os as _os
        path = str(path)
        if not _os.path.isfile(path):
            return "文件不存在"
        if not _PIL:
            return "需要 Pillow 支持（可先 pip install pillow）"
        try:
            src = Image.open(path)
            src.load()
        except Exception as e:
            return f"无法打开图片：{e}"

        w = max(self.chat_canvas.winfo_width(), 640)
        h = max(self.chat_canvas.winfo_height(), 480)
        frames: list = []
        self._clear_wall_image()   # 先清掉旧图/动画
        try:
            n = getattr(src, "n_frames", 1)
        except Exception:
            n = 1

        # 封面（覆盖整块区域）
        def cover(im) -> "Image.Image":
            im = im.convert("RGB")
            iw, ih = im.size
            scale = max(w / iw, h / ih)
            im = im.resize((max(1, int(iw * scale)), max(1, int(ih * scale))),
                           Image.LANCZOS)
            iw, ih = im.size
            x0, y0 = (iw - w) // 2, (ih - h) // 2
            return im.crop((x0, y0, x0 + w, y0 + h))

        first = cover(src)
        mean = sum(first.resize((1, 1)).getpixel((0, 0))) / 3 / 255.0
        self._wall_dark = mean < 0.55
        frames: list = []
        for i in range(max(1, n)):
            try:
                src.seek(i)
                frame = cover(src)
            except Exception:
                break
            frames.append(ImageTk.PhotoImage(frame))
        if not frames:
            return "未能解码图片"
        self._wall_frames = frames
        self._wall_image_path = path
        self._wall_photo = self._wall_frames[0]
        self._wall_index = 0
        self.chat_canvas.create_image(0, 0, anchor="nw", image=self._wall_photo, tags=("wall",))
        self.chat_canvas.tag_lower("wall")
        if len(self._wall_frames) > 1:
            self._animate_wall()
        if persist:
            providers.set_wallpaper_image(path)
        if self.cur is not None:
            self._redraw_all()
            self._scroll_bottom()
        return None

    def _pick_wallpaper_image(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root, title="选择一张图片作为聊天区壁纸（GIF 会自动动）",
            filetypes=[("图片 / GIF", "*.png *.gif *.jpg *.jpeg *.bmp *.webp"),
                       ("所有文件", "*.*")])
        if not path:
            return
        err = self._set_wallpaper_image(path, persist=True)
        if err:
            messagebox.showerror("壁纸设置失败", err)

    def _clear_wallpaper_image(self) -> None:
        providers.set_wallpaper_image(None)
        self._clear_wall_image()
        self.chat_host.configure(bg=self.wall)
        self.chat_canvas.configure(bg=self.wall)
        if self.cur is not None:
            self._redraw_all()
            self._scroll_bottom()

    def _restore_saved_wall(self, path: str) -> None:
        err = self._set_wallpaper_image(path, persist=False)
        if err:
            self._append("⚠ 恢复上次壁纸图片失败：%s" % err, "error")

    def _restore_saved_wall_pref(self) -> None:
        video = providers.wallpaper_video()
        if video:
            err = self._set_wallpaper_video(video, persist=False)
            if err:
                self._append("⚠ 恢复动态视频壁纸失败：%s" % err, "error")
            return
        img = providers.wallpaper_image()
        if img:
            self._restore_saved_wall(img)

    def _animate_wall(self) -> None:
        if not self._wall_frames or len(self._wall_frames) <= 1:
            return
        self._wall_index = (self._wall_index + 1) % len(self._wall_frames)
        self._wall_photo = self._wall_frames[self._wall_index]
        try:
            self.chat_canvas.itemconfigure("wall", image=self._wall_photo)
        except Exception:
            return
        self._wall_job = self.root.after(80, self._animate_wall)

    # ------------------------------------------------------------- 气泡渲染
    def _on_canvas_resize(self, _e) -> None:
        pass  # 气泡用固定折行宽度；如需随窗重排可后续扩展

    def _on_wheel(self, e) -> str:
        self.chat_canvas.yview_scroll(int(-e.delta / 120), "units")
        return "break"

    def _on_wheel_linux(self, e) -> str:
        self.chat_canvas.yview_scroll(-1 if e.num == 4 else 1, "units")
        return "break"

    def _scroll_bottom(self) -> None:
        self.chat_canvas.yview_moveto(1.0)

    def _clear_rows(self) -> None:
        self.chat_canvas.delete("msg")

    def _records(self) -> list[dict]:
        return self.cur["records"]

    def _redraw_all(self) -> None:
        self._clear_rows()
        if self.cur is None:
            return
        cw = max(self.chat_canvas.winfo_width() - 50, 360)
        M, AV, AG, W = 18, 40, 10, 3
        y = 16
        gap = 6
        c = self._chat_colors()
        av_right = cw - AV // 2                 # 用户头像圆心 x（右侧）
        x_right1 = cw - (AV + AG)               # 用户气泡右缘

        for rec in self.cur["records"]:
            kind = rec.get("kind") or "txt"
            text = rec.get("text", "")
            lines = []
            if kind == "user":
                font = self._font(("ui", 12))
                maxw = min(520, int((cw - (M + AV + AG)) * 0.70))
                lines = self._wrap_lines(text, font, maxw - 32)
                lw = max((font.measure(ln) for ln in lines), default=0) + 32
                bw = min(max(lw, 48), maxw + 32)
                bh = len(lines) * font.metrics("linespace") + 24
                x0 = max(M, x_right1 - bw)
                acy = y + (bh - AV // 2 if bh >= AV else bh // 2)
                self._avatar(av_right, acy, "😎", AV_USER_BG)
                self._bubble(x0, y, x_right1, y + bh, USER_BUB)
                self._text(x0 + 16, y + 12, lines, font, c["user_fg"], anchor="nw")
            elif kind in ("answer", "code"):
                font = self._font(("mono", 11) if kind == "code" else ("ui", 12))
                maxw = min(680 if kind == "answer" else 700, cw - (M + AV + AG) - 30)
                lines = self._wrap_lines(text, font, maxw - 32)
                lw = max((font.measure(ln) for ln in lines), default=0) + 32
                bw = min(max(lw, 52), maxw + 32)
                bh = len(lines) * font.metrics("linespace") + 26
                x0 = M + AV + AG
                acy = y + (bh - AV // 2 if bh >= AV else bh // 2)
                self._avatar(M + AV // 2, acy, "🐻", AV_AI_BG)
                bg = c["code_bg"] if kind == "code" else c["ans_bg"]
                fg = c["code_fg"] if kind == "code" else c["ans_fg"]
                self._bubble(x0, y, x0 + bw, y + bh, bg)
                self._text(x0 + 16, y + 13, lines, font, fg, anchor="nw")
            elif kind == "tool":
                font = self._font(("ui", 10))
                lines = self._wrap_lines(text, font, min(700, cw - 90) - 40)
                lw = max((font.measure(ln) for ln in lines), default=0) + 40
                bw = min(max(lw, 66), min(700, cw - 90) + 40)
                bh = len(lines) * font.metrics("linespace") + 18
                self._bubble(M, y, M + bw, y + bh, c.get("tool_bg", "#fff3c9"),
                             r=14, shadow=False)
                self._text(M + 20, y + 9, lines, font, c["tool"], anchor="nw")
            else:
                font = self._font(("ui", 10 if kind != "meta" else 9))
                if kind == "head":
                    font = self._font(("ui", 10, "bold"))
                fg = {"head": c["head"], "tool": c["tool"], "meta": c["meta"],
                      "ok": c["ok"], "error": c["error"]}.get(kind, c["plain"])
                lines = self._wrap_lines(text, font, cw - 70)
                self._text(M, y, lines, font, fg, anchor="nw")
                bh = len(lines) * font.metrics("linespace")
            y += bh + gap + 5
        self.chat_canvas.configure(scrollregion=(0, 0, cw + 50, y + 10))

    def _rrect(self, x0, y0, x1, y1, fill, r=14, outline="", width=1) -> None:
        """圆角矩形（多边形逼近），供气泡 / 投影 / 描边使用。"""
        x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
        pts = (x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
               x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0)
        self.chat_canvas.create_polygon(*pts, smooth=True, tags=("msg",),
                                        fill=fill, outline=outline, width=width)

    def _bubble(self, x0, y0, x1, y1, fill, r=14, outline=INK, width=3,
                shadow=True) -> None:
        """卡通气泡：葡萄描边 + 向下厚投影（先画投影再画主体）。"""
        if shadow:
            self._rrect(x0 + 2, y0 + 4, x1 + 2, y1 + 4, INK, r, outline="", width=0)
        self._rrect(x0, y0, x1, y1, fill, r, outline=outline, width=width)

    def _avatar(self, cx, cy, text: str, bg: str) -> None:
        """带头像腮红的小圆脸（白底 + 葡萄描边 + 粉腮红）。"""
        r = 20
        self.chat_canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                                     fill=bg, outline=INK, width=3, tags=("msg",))
        self.chat_canvas.create_text(cx, cy - 1, text=text,
                                     font=self._font(("ui", 19)), fill=INK,
                                     tags=("msg",))
        br = r * 0.30
        for dx in (-r * 0.46, r * 0.46):
            ex0 = cx + dx - br
            ey0 = cy + r * 0.42
            self.chat_canvas.create_oval(ex0, ey0, ex0 + br * 2, ey0 + br,
                                         fill=BLUSH, outline="", tags=("msg",))

    def _text(self, x, y, lines, font, fg, anchor="nw") -> None:
        self.chat_canvas.create_text(x, y, text="\n".join(lines), anchor=anchor,
                                     fill=fg, font=font, tags=("msg",))

    def _render_records(self) -> None:
        self._redraw_all()
        self._scroll_bottom()

    # --------------------------------------------------------- 对外渲染 API
    def _append(self, text: str, tag: str | None = None, endl: bool = True) -> None:
        """把一条消息加入当前会话（气泡式）。tag 决定呈现形态。"""
        if not text:
            return
        kind = tag or "txt"
        if kind == "asst":      # 旧调用兼容 → 头部小标题
            kind = "head"
        elif kind == "txt":
            kind = "answer"
        rec = {"kind": kind, "text": text}
        recs = self._records()
        recs.append(rec)
        if len(recs) > self.MAX_RECORDS:
            for _ in range(len(recs) - self.MAX_RECORDS):
                recs.pop(0)
        # 会话标题跟随第一句
        if self.cur is not None and self.cur.get("title") == "新对话" and kind == "user":
            title = text.splitlines()[0].strip()
            self.cur["title"] = (title[:18] + "…") if len(title) > 18 else title
        self._redraw_all()
        self._scroll_bottom()
        self._refresh_title()

    def _refresh_title(self) -> None:
        if self.cur in self.sessions:
            idx = self.sessions.index(self.cur)
            self.side_list.delete(idx)
            self.side_list.insert(idx, self.cur["title"])
            self.side_list.selection_clear(0, "end")
            self.side_list.selection_set(idx)

    def _clear_transcript(self) -> None:
        self.cur["records"].clear()
        self._render_records()

    # ---------------------------------------------------------- 右键 / 复制
    def _on_chat_menu(self, e) -> str:
        return _menu_at(self.chat_canvas, e, [
            ("复制整个会话", self._copy_all),
            ("清空本对话", lambda: self._command("clear")),
        ])

    def _copy_all(self) -> None:
        parts = []
        for rec in self._records():
            parts.append(rec["text"])
        text = "\n".join(parts)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status.config(text="已复制整个会话。")

    def _on_input_menu(self, event) -> str:
        txt = self.input
        return _menu_at(txt, event, [
            ("剪切", lambda: _text_cut(txt)),
            ("复制", lambda: _copy_selection(txt)),
            ("粘贴", lambda: _paste_text(txt)),
            ("全选", lambda: _select_all_text(txt)),
        ])

    # ------------------------------------------------------------ 配置/标题
    def _recreate_agent(self, config: HarnessConfig, vendor_label: str | None, announce: bool) -> None:
        """应用新配置：提升配置版本，重建当前会话的 agent。"""
        self.config = config
        self._cfg_version += 1
        if self.cur is not None:
            self.cur["agent"] = None
            self.cur["collab"] = None
            self.cur["version"] = -1
            self._bind_active()
        matched = providers.match_provider(config.base_url)
        vendor = vendor_label or (matched.name if matched else "自定义")
        critic = guess_critic_model(config)
        mode = f" · 主+审({critic})" if config.collab_enabled else ""
        self.model_text.set(f"{vendor} · {config.model}{mode}")
        self.root.title(f"{vendor} · {config.model}{mode} —— harness")
        if announce:
            self._clear_transcript()
            self._append(f"已应用新配置：{vendor} / {config.model}"
                         + ("（协作开）" if config.collab_enabled else ""), "ok")
            self._show_problems()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.send_btn.config(state="disabled" if busy else "normal",
                             text="思考中…" if busy else "发送")
        self.status.config(text="思考中（模型可能正在调用工具）…" if busy else "就绪")

    # ----------------------------------------------------------- event pump
    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                self._handle_event(kind, payload)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _handle_event(self, kind: str, payload) -> None:
        if kind == "tool_start":
            name, args = payload
            try:
                shown = json.dumps(args, ensure_ascii=False)
            except (TypeError, ValueError):
                shown = str(args)
            if len(shown) > 200:
                shown = shown[:200] + "…"
            self._append(f"  ⚙ 调用 {name}({shown})", "tool")
        elif kind == "tool_end":
            _name, result = payload
            tag, head = ("ok", "结果") if result.ok else ("error", "出错")
            self._append(f"  → {head}：{result.display}", tag)
        elif kind == "final":
            self._append("模型 ›", "head")
            self._append(payload, "answer")
        elif kind == "ask":
            tool_name, path, content, answer, event = payload
            self._start_ask(tool_name, path, content, answer, event)
        elif kind == "askq":
            question, options, answer, event = payload
            self._start_ask_q(question, options, answer, event)
        elif kind == "error":
            self._append("⚠ " + payload, "error")
        elif kind == "open_html":
            path = payload
            try:
                webbrowser.open(Path(str(path)).as_uri())
            except Exception as e:
                self._append(f"⚠ 打开预览失败：{type(e).__name__}: {e}", "error")
            else:
                self._append(f"已在系统默认浏览器打开预览：{path}", "ok")
        elif kind == "done":
            self._set_busy(False)

    # --------------------------------------------------------- tool callbacks
    def _on_tool_start(self, name: str, args: dict) -> None:
        self.events.put(("tool_start", (name, args)))

    def _on_tool_end(self, name: str, result) -> None:
        self.events.put(("tool_end", (name, result)))

    # ------------------------------------------------ 写入确认（内联气泡）
    def _confirm_write(self, name: str, args: dict) -> "bool | str":
        if self._auto_allow_writes:
            return True
        if not isinstance(args, dict):
            args = {}
        path = str(args.get("path", ""))
        content = str(args.get("content", ""))
        answer = {"ok": False, "note": ""}
        event = threading.Event()
        self.events.put(("ask", (name, path, content, answer, event)))
        event.wait()
        if answer["ok"]:
            return True
        note = str(answer.get("note", "")).strip()
        return note if note else False

    def _start_ask(self, name: str, path: str, content: str, answer: dict, event) -> None:
        self._append(f"✍ {name} —— 目标：{path}", "head")
        cap = 6000
        shown = content or ""
        suffix = ""
        if len(shown) > cap:
            shown = shown[:cap]
            suffix = f"\n…（内容较长，预览前 {cap} 字符；同意后写入完整内容）"
        self._append(shown + suffix, "code")
        self._pending_ask = (answer, event)
        self.bar_text.set("是否允许这次写入？")
        self.approve_bar.pack(fill="x", before=self.bottom)
        self.status.config(text="⏸ 等待你决定是否写入…（空白回车=允许 / 输入理由回车=拒绝并说明）")

    def _resolve_ask(self, decision) -> None:
        if self._pending_ask is None:
            return
        answer, event = self._pending_ask
        self._pending_ask = None
        self.approve_bar.pack_forget()
        if decision == "auto":
            self._auto_allow_writes = True
            answer.update(ok=True, note="")
            line = "→ 已允许写入，并记住：本会话后续写入不再询问。"
        elif isinstance(decision, bool):
            answer.update(ok=decision, note="")
            line = "→ 已允许写入，继续。" if decision else "→ 已拒绝写入（不落盘）。"
        else:
            answer.update(ok=False, note=str(decision).strip())
            line = f"→ 已拒绝写入；你的意见「{decision}」将回传给模型。"
        self._append(line, "meta")
        self.status.config(text="已收到你的决定，继续处理…")
        event.set()

    def _resolve_ask_from_input(self) -> None:
        if self._pending_ask is None:
            return
        text = self.input.get("1.0", "end").strip()
        self.input.delete("1.0", "end")
        self._resolve_ask(True if not text else text)

    # ------------------------------------------------ 模型中途提问(ask_user)
    def _ask_user(self, name: str, args: dict) -> str:
        if not isinstance(args, dict):
            args = {}
        question = str(args.get("question", "")).strip() or "（模型没有写出问题）"
        options = str(args.get("options", "")).strip()
        answer = {"text": ""}
        event = threading.Event()
        self.events.put(("askq", (question, options, answer, event)))
        event.wait()
        return answer["text"]

    def _start_ask_q(self, question: str, options: str, answer: dict, event) -> None:
        self._append("💬 模型提问：", "head")
        self._append(question, "answer")
        if options:
            self._append("（可选：" + options.replace("/", " ｜ ") + "，也可自由输入）", "meta")
        self._pending_q = (answer, event)
        self.status.config(text="⏸ 模型在等你回答…输入后回车")

    def _resolve_q(self) -> None:
        if self._pending_q is None:
            return
        answer, event = self._pending_q
        self._pending_q = None
        text = self.input.get("1.0", "end").strip()
        self.input.delete("1.0", "end")
        answer["text"] = text
        self.status.config(text="已收到你的回答，继续处理…")
        event.set()

    # ------------------------------------------------------------- commands
    def _show_skills_in_chat(self) -> None:
        if not self.skills:
            self._append("尚未安装技能：把技能目录（内含 SKILL.md）放到项目根 skills/ 下即可。", "meta")
            return
        self._append(f"可用技能 {len(self.skills)} 个（模型需要时会自动 use_skill 拉说明）：", "head")
        for s in self.skills:
            self._append(f"• {s.name} —— {s.description}", "tool")
            if s.library:
                self._append(f"    素材库：{s.library}", "meta")
        self._append("想立刻预载某个技能，输入 /skill <名>（如 /skill galaxy-ui）。", "meta")

    def _load_skill(self, name: str) -> None:
        if self._busy:
            self._append("（正在处理，稍后再预载技能）", "error")
            return
        name = (name or "").strip()
        if not name:
            self._append("用法：/skill <技能名>，如 /skill galaxy-ui", "meta")
            return
        try:
            skill = load_skill(name, self.skill_dir)
        except SkillNotFoundError as e:
            self._append("⚠ " + str(e), "error")
            return
        if name not in self._loaded_skills:
            self._loaded_skills.append(name)
        self._ensure_skills(self._agent)
        if self.cur and self.cur.get("collab") is not None:
            self._ensure_skills(self.cur["collab"])
        self._append(f"✔ 已预载技能「{skill.name}」——接下来的任务它会带路（可 /clear 后更稳）。", "ok")

    def _command(self, cmd: str) -> None:
        if cmd == "tools":
            for line in self._agent.tool_list():
                self._append("  • " + line, "meta")
        elif cmd == "skills":
            self._show_skills_in_chat()
        elif cmd == "skill":
            self._append("用法：/skill <技能名>，如 /skill galaxy-ui", "meta")
        elif cmd == "config":
            self._append("当前 " + providers.summary(), "meta")
            for line in self.config.summary().splitlines():
                self._append("  " + line, "meta")
        elif cmd == "settings":
            self._open_settings()
        elif cmd == "folder":
            self._choose_folder()
        elif cmd == "clear":
            if self._busy:
                self._append("（正在思考中，稍后再清空）", "error")
            else:
                if self.cur is not None:
                    self.cur["agent"] = None
                    self.cur["collab"] = None
                    self.cur["version"] = -1
                    self._bind_active()
                    self._clear_transcript()
                self._append("—— 已清空对话历史 ——", "meta")
        elif cmd == "help":
            self._append("右键对话区可复制/清空；顶栏「⚙ 设置 / 📁 文件夹 / 🧩 技能」或左栏快捷命令可用。", "meta")

    def _open_settings(self) -> None:
        if self._busy:
            self._append("（正在思考中，稍后再改设置）", "error")
            return
        self._settings_dialog = _SettingsDialog(
            self.root, on_apply=self._on_provider_applied, current_config=self.config,
            on_wallpaper=self._apply_wallpaper, current_wall=self.wall,
            on_pick_image=self._pick_wallpaper_image,
            on_clear_image=self._clear_wallpaper_image,
            on_pick_video=self._pick_wallpaper_video,
            on_clear_video=self._clear_wallpaper_video)

    def _on_provider_applied(self, vendor_label: str) -> None:
        new_config = HarnessConfig.from_env()
        self._recreate_agent(new_config, vendor_label=vendor_label, announce=True)
        self.status.config(text=f"已切换厂商：{vendor_label}")
        self._refresh_sidebar_state()

    # --------------------------------------------------------- 工作目录
    def _choose_folder(self) -> None:
        if self._busy:
            self._append("（正在处理，稍后再选文件夹）", "error")
            return
        initial = self.workspace.root_str if self.workspace else None
        path = filedialog.askdirectory(parent=self.root, title="选择 agent 的工作目录", initialdir=initial)
        if not path:
            return
        self._apply_folder(path)

    def _apply_folder(self, path) -> None:
        from pathlib import Path

        try:
            if self.workspace is None:
                self.workspace = FolderWorkspace(path)
            else:
                self.workspace.set_root(path)
            tools = build_workspace_tools(self.workspace)
            self._bind_active()
            self._agent.add_tools(tools)
            if self.cur and self.cur.get("collab") is not None:
                self.cur["collab"].add_tools(tools)
        except Exception as e:
            self._append(f"⚠ 无法使用该文件夹：{type(e).__name__}: {e}", "error")
            return
        self._sync_folder_label()
        self._append(f"✔ 工作目录：{self.workspace.root_str}", "ok")
        self._append("模型现在可 list_files / read_file / write_file 读写该目录（写入需你确认）。", "meta")

    def _sync_folder_label(self) -> None:
        if self.workspace:
            self.folder_var.set(f"工作目录\n{self.workspace.root_str}")
            if getattr(self, "_folder_sub", None) is not None:
                self._folder_sub.config(
                    text=(self.workspace.root_str if len(self.workspace.root_str) <= 22
                          else "…" + self.workspace.root_str[-22:]))
            if getattr(self, "_folder_tile", None) is not None:
                self._folder_tile.config(bg=MINT_BG)
        else:
            self.folder_var.set("工作目录：未选择（点「📁 切换目录」后，模型可读写其中的文件）")
            if getattr(self, "_folder_sub", None) is not None:
                self._folder_sub.config(text="chdir 到新工作区")
            if getattr(self, "_folder_tile", None) is not None:
                self._folder_tile.config(bg=SUN_BG)

    def _show_problems(self) -> None:
        for p in self.config.problems():
            self._append("⚠ " + p + " 点「设置」可快速接入。", "error")

    def _show_startup(self) -> None:
        self._append("欢迎使用 harness 气泡卡通窗口 🫧 左侧快捷命令一键切换目录 / 查看技能 / 开协作 / 清空；",
                     "meta")
        self._append("「📁 切换目录」选定后，模型可读写其中文件（写入需你确认）。", "meta")
        if self.skills:
            names = "、".join(s.name for s in self.skills)
            self._append(
                f"已自动启用 {len(self.skills)} 个本地技能（{names}）：点左栏「🧩 查看技能」、"
                "或输入 /skill <名> 预载；做网页 UI 时模型会自己调用 galaxy 素材库。",
                "meta",
            )
        self._show_problems()

    # -------------------------------------------------------------- actions
    def _on_key(self, event) -> str | None:
        if event.state & 0x0001 or event.state & 0x0004:  # Shift / Ctrl -> 换行
            return None
        if self._pending_ask is not None:
            self._resolve_ask_from_input()
        elif self._pending_q is not None:
            self._resolve_q()
        else:
            self._do_send()
        return "break"

    def _do_send(self) -> None:
        if self._pending_ask is not None:
            self._resolve_ask_from_input()
            return
        if self._pending_q is not None:
            self._resolve_q()
            return
        if self._busy:
            return
        raw = self.input.get("1.0", "end").strip()
        self.input.delete("1.0", "end")
        if not raw:
            return
        if raw.startswith("/"):
            cmd_raw = raw[1:].strip()
            cmd, _, cmd_arg = cmd_raw.partition(" ")
            cmd = cmd.lower()
            if cmd in ("exit", "quit"):
                self.root.destroy()
                return
            if cmd in ("new", "clear", "settings", "tools", "config", "help", "folder", "skills", "skill"):
                if cmd == "skill" and cmd_arg.strip():
                    self._load_skill(cmd_arg.strip())
                else:
                    self._command(cmd)
                return
            self._append(f"未知命令 {cmd}", "error")
            return

        self._bind_active()
        self._append(raw, "user")
        self._set_busy(True)
        threading.Thread(target=self._worker, args=(raw,), daemon=True).start()

    def _worker(self, user_input: str) -> None:
        try:
            if self.config.collab_enabled:
                answer = self._active_collab().run(user_input)
            else:
                answer = self._agent.run(user_input)
            if answer:
                self.events.put(("final", answer))
            else:
                self.events.put(("final", "（模型未给出文本回答）"))
        except IterationLimitError as e:
            self.events.put(("error", str(e)))
        except Exception as e:
            hint = ""
            if "401" in str(e) or "Authentication" in type(e).__name__:
                hint = "（检查点「设置」里的密钥 / base_url / model）"
            self.events.put(("error", f"{type(e).__name__}: {e} {hint}"))
        finally:
            self.events.put(("done", None))


def launch(config: "HarnessConfig | None" = None) -> None:
    if tk is None:
        raise RuntimeError(
            "tkinter 不可用（本环境无图形支持）：" + (str(_TK_ERROR) or "") +
            "\n请用支持桌面的 Python 解释器运行，或改用 python -m harness.cli。"
        )
    # 每次启动窗口都清掉 .env 里已保存的密钥：本会话必须重新填写后才可用
    providers.clear_api_key()
    cfg = config or HarnessConfig.from_env()
    root = tk.Tk()
    _ChatWindow(root, cfg)
    root.mainloop()


def main() -> None:
    launch()


if __name__ == "__main__":
    main()
