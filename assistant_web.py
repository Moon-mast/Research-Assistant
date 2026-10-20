"""科研小助手 · HTML 内核桌面助手（pywebview + Edge WebView2）。

窗口直接渲染 assets/assistant_ui.html（galaxy-ui 技能改编的整窗皮肤），
Python 侧复用 harness 引擎，提供真正"能干活的助手"能力：
  - 编码 / 干活：内置工具 + 选定文件夹后 list_files / read_file / write_file（写入需确认）；
  - 不同模型：厂商预设 + 自由填 base_url/model（写 .env 或在内存生效）；
  - skill：检测 skills/ 自动启用（list_skills / use_skill / library_*）；
  - 上下文压缩：Agent 内建（多轮 + 单轮中途自动压缩，见 CONTEXT_*）；
  - 多模型协作（可选）：主答 + 审查改进。

运行：.venv\\Scripts\\pythonw assistant_web.py   （桌面快捷方式「科研小助手」即指向它）
"""
from __future__ import annotations

import itertools
import json
import os
import queue
import sys
import threading
from collections import deque
from pathlib import Path

try:
    import webview
except Exception as e:  # pragma: no cover
    webview = None  # type: ignore[assignment,misc]
    _WEBVIEW_ERR = e
else:
    _WEBVIEW_ERR = None

_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
UI_HTML = _ROOT / "assets" / "assistant_ui.html"
ICO = _ROOT / "assets" / "assistant.ico"


def _snippet(text, limit=180):
    t = " ".join(str(text or "").split())
    return t if len(t) <= limit else t[:limit] + "…"


class Bridge:
    """暴露给页面 JS 的后端（js_api）。所有方法都可被页面异步调用。"""

    def __init__(self):
        self._lock = threading.RLock()
        self.events: deque = deque()
        self.busy = False
        self.config = None            # HarnessConfig
        self.engine = None            # Agent 或 Collaboration
        self.engine_kind = "agent"
        self.ws = None                # FolderWorkspace
        self.ws_tools = False
        self._pending: dict = {}      # uid -> threading.Event
        self._answers: dict = {}      # uid -> (ok, note)
        self._ids = itertools.count(1)

    # ---------------------------------------------------------- 事件通道
    def _emit(self, kind: str, text: str = "", **extra) -> None:
        with self._lock:
            self.events.append({"kind": kind, "text": text, **extra})

    def _set_busy(self, busy: bool) -> None:
        with self._lock:
            self.busy = bool(busy)

    def pull(self) -> dict:
        """JS 轮询：取走待渲染事件 + 当前忙碌态。"""
        with self._lock:
            out = list(self.events)
            self.events.clear()
            busy = self.busy
        return {"busy": busy, "events": out}

    # ---------------------------------------------------------- 启动信息
    def boot(self) -> dict:
        from harness import __version__
        from harness.config import HarnessConfig
        from harness.providers import PROVIDERS
        from harness.skills import discover, default_skills_dir

        cfg = self.config or HarnessConfig.from_env()
        self.config = cfg      # 冷启动（.env 接入、未点过设置）也要能实时拉模型
        prov = [
            {"id": p.id, "name": p.name, "base_url": p.base_url, "models": p.models,
             "light": p.light, "note": p.note}
            for p in PROVIDERS
        ]
        skills = [
            {"name": s.name, "description": s.description,
             "when_to_use": s.when_to_use or ""}
            for s in discover(default_skills_dir())
        ]
        return {
            "version": __version__,
            "base_url": cfg.base_url,
            "model": cfg.model,
            "api_key_set": bool(cfg.api_key),
            "collab": cfg.collab_enabled,
            "critic": cfg.collab_critic_model or "",
            "compress": cfg.context_compress,
            "providers": prov,
            "skills": skills,
            "ws": self.ws.root_str if self.ws else "",
            "has_engine": self.engine is not None,
            "engine_kind": self.engine_kind,
            "backend": "pywebview",
        }

    # ---------------------------------------------------------- 引擎构建
    def _ensure_engine(self, cfg=None) -> None:
        """按当前配置构建 Agent / Collaboration，并挂技能 + 文件夹工具。"""
        from harness.config import HarnessConfig
        from harness.skills import activate_skills, discover, default_skills_dir

        cfg = cfg or self.config or HarnessConfig.from_env()
        self.config = cfg
        if cfg.api_key in ("", "sk-dummy"):
            # 无 key：只允许本地服务（Ollama / 测试用 loopback），补占位 key 以通过 SDK 校验
            local = ("localhost" in (cfg.base_url or "")) or ("127.0.0.1" in (cfg.base_url or ""))
            if not local:
                self.engine = None
                self.engine_kind = "none"
                return
            from dataclasses import replace
            cfg = replace(cfg, api_key="local")

        def on_start(name, args):
            try:
                a = json.dumps(args, ensure_ascii=False) if args else ""
            except Exception:
                a = ""
            self._emit("sys", f"⚙ 正在调用 {name}{(' ' + a[:60]) if a else ''} …")

        def on_end(name, result):
            ok = bool(getattr(result, "ok", False))
            head = _snippet(result.content if ok else f"[错误] {result}")
            self._emit("sys", f"{'✔' if ok else '✘'} {name} → {head}")

        if cfg.collab_enabled:
            from harness.team import Collaboration
            eng = Collaboration(
                cfg,
                critic_model=cfg.collab_critic_model,
                on_tool_start=on_start,
                on_tool_end=on_end,
                confirm_handler=self._confirm_handler,
            )
            self.engine_kind = "collab"
        else:
            from harness.agent import Agent
            eng = Agent(
                cfg,
                on_tool_start=on_start,
                on_tool_end=on_end,
                confirm_handler=self._confirm_handler,
            )
            self.engine_kind = "agent"
        self.engine = eng
        activate_skills(eng, get_workspace=lambda: self.ws)
        self._attach_folder()      # 无条件挂工具（未选目录时为分离态提示）

    # ---------------------------------------------------------- 厂商设置
    def save_config(self, provider_id=None, base_url=None, model=None,
                    api_key=None, critic=None, collab=False, persist=False) -> dict:
        from harness.config import HarnessConfig
        from harness.providers import PROVIDERS, apply_credentials, set_collab

        if self.busy:
            return {"ok": False, "msg": "正在生成中，先等一下再改设置"}

        p = next((x for x in PROVIDERS if x.id == provider_id), None) if provider_id else None
        base = (base_url or (p.base_url if p else "")).strip()
        key = (api_key or "").strip()
        model = (model or "").strip()
        if not model:
            # 模型版本不固化：留空时优先实时拉取该端点的最新模型列表
            try:
                from harness.providers import fetch_remote_models
                live = fetch_remote_models(base, key)
                if live:
                    model = live[0]
                    self._emit("sys", f"模型留空 → 自动选用端点实时列表第一个：{model}")
            except Exception:
                pass
            if not model:
                model = (p.models[0] if p and p.models else "").strip()
        if not base or not model:
            return {"ok": False, "msg": "base_url 不能为空；model 可留空（自动取端点最新），拉取失败时才需手填"}

        # 直接落内存（本地 Ollama 之类的 key 可为空）
        cfg = HarnessConfig(base_url=base, api_key=key, model=model,
                            collab_enabled=bool(collab),
                            collab_critic_model=critic or None)

        if persist and not getattr(sys, "frozen", False):
            try:
                apply_credentials(base, key, model)
                set_collab(bool(collab), critic or "")
            except Exception as e:
                self._emit("sys", f"写 .env 失败（不影响本次使用）：{e}")

        with self._lock:
            self._ensure_engine(cfg)
        self._emit("sys", f"已切换到 {model}" + (" · 协作开" if collab else ""))
        info = self.boot()
        info["ok"] = True
        info["msg"] = "已应用"
        return info

    # ---------------------------------------------------------- 文件夹
    def _attach_folder(self, path=None) -> None:
        """把文件/PDF/脚本工具挂到当前引擎。

        - path 为空时挂一个"未选目录"的分离态工作区：工具从一开始就存在，
          调用会得到"请先选目录"的明确提示，而不是技能里承诺了却找不到工具；
        - FolderWorkspace 绑定方法的工具会跟随 root 动态变化，选目录后无需重挂；
        - 引擎重建（save_config / 切协作）后新引擎没有工具，需要重挂；
        - 用 _tools_on_engine 记录避免同一引擎上重复挂载。
        """
        from harness.workspace import FolderWorkspace, build_workspace_tools

        if path is None:
            if self.ws is None:
                self.ws = FolderWorkspace()      # 分离态：root=None
        else:
            if self.ws is None:
                self.ws = FolderWorkspace(str(path))
            else:
                self.ws.set_root(str(path))
        if self.engine is not None and getattr(self, "_tools_on_engine", None) is not id(self.engine):
            self.engine.add_tools(build_workspace_tools(self.ws))
            self._tools_on_engine = id(self.engine)
            self.ws_tools = True

    def choose_folder(self) -> str:
        """打开系统文件夹选择框；选定后模型获得 list_files/read_file/write_file。"""
        try:
            if webview is None:
                raise RuntimeError("无 webview")
            win = webview.windows[0]
            paths = win.create_file_dialog(webview.FOLDER_DIALOG)
        except Exception as e:
            return f"打开系统选择框失败：{e}（可在输入框输入 /folder <路径>）"
        if not paths:
            return "已取消"
        return self.set_folder(paths[0])

    def set_folder(self, path: str) -> str:
        path = (path or "").strip().strip('"')
        if not path:
            return "路径为空"
        try:
            p = Path(path).expanduser()
            if not p.is_dir():
                return f"不是目录或不存在：{p}"
            self._attach_folder(p)
            self._emit("sys", f"📁 工作区已设为：{p}")
            return f"已把工作区设为 {p}"
        except Exception as e:
            return f"设置工作区失败：{e}"

    # ---------------------------------------------------------- 技能
    def list_dir(self, path: str = "") -> dict:
        """列目录（供页面内嵌的文件夹/文件浏览弹层用）。path 空 = 用户主目录。"""
        from pathlib import Path as _P
        try:
            raw = (path or "").strip()
            p = _P(raw).expanduser() if raw else _P.home()
            p = p.resolve()
            if not p.is_dir():
                return {"ok": False, "msg": f"不是目录：{p}"}
        except Exception as e:
            return {"ok": False, "msg": f"路径无效：{e}"}
        try:
            kids = sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower()))
        except Exception as e:
            return {"ok": False, "msg": f"无法读取目录：{e}"}
        entries = [{"name": c.name, "dir": c.is_dir()} for c in kids[:600]]
        parent = str(p.parent) if str(p.parent) != str(p) else ""
        return {"ok": True, "path": str(p), "parent": parent, "entries": entries}

    def preload_skill(self, name: str) -> str:
        from harness.skills import load_skill, skill_instructions

        if self.engine is None:
            return "还没有引擎（请先在 ⚙ 设置接入模型）"
        try:
            s = load_skill(name)
            self.engine.inject_system("【已预载技能】\n" + skill_instructions(s))
            self._emit("sys", f"🧩 已载入技能说明书：{s.name}")
            return "已载入：" + s.name
        except Exception as e:
            return f"载入技能失败：{e}"

    # ---------------------------------------------------------- 模型实时列表
    def list_models(self, base_url=None, api_key=None) -> dict:
        """实时拉取该端点当前可用的模型列表（设置面板「🔄 最新」用）。

        base_url/api_key 缺省时用当前已接入的配置；返回 {"ok", "models", "msg"}。
        """
        from harness.providers import fetch_remote_models
        base = (base_url or (self.config.base_url if self.config else "") or "").strip()
        if api_key is None:
            api_key = self.config.api_key if self.config else ""
        key = (api_key or "").strip()
        if not base:
            return {"ok": False, "models": [], "msg": "缺少 base_url"}
        try:
            models = fetch_remote_models(base, key)
            return {"ok": True, "models": models,
                    "msg": f"实时获取到 {len(models)} 个模型"}
        except Exception as e:
            return {"ok": False, "models": [], "msg": f"拉取失败：{e}"}

    # ---------------------------------------------------------- 对话
    def clear(self) -> None:
        if self.engine is not None:
            try:
                self.engine.reset()
            except Exception:
                pass
        with self._lock:
            self.events.clear()
        self._emit("clear", "已清空对话（上下文已重置）")

    def chat(self, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            return {"ok": False, "msg": "空消息"}
        if self.busy:
            return {"ok": False, "msg": "正在处理上一条，请稍候"}
        if self.engine is None:
            self._emit("err", "还没有接入模型：请点右上 ⚙ 设置填好厂商与密钥后再聊。")
            return {"ok": False, "msg": "no engine"}
        self._set_busy(True)
        threading.Thread(target=self._work, args=(text,), daemon=True).start()
        return {"ok": True}

    def _work(self, text: str) -> None:
        try:
            ans = self.engine.run(text)
            self._emit("ai", ans or "（模型没有给出文字回答）")
        except Exception as e:  # 网络 / 限流 / 超迭代等
            self._emit("err", f"[错误] {type(e).__name__}: {e}")
        finally:
            self._set_busy(False)

    # ---------------------------------------------------------- 写入确认
    def _confirm_handler(self, name, args) -> "bool | str":
        """write_file 等破坏性操作执行前的用户确认：推事件给页面，阻塞等待答复。"""
        uid = f"c{next(self._ids)}"
        done = threading.Event()
        with self._lock:
            self._pending[uid] = done
        self._emit("confirm", "",
                   id=uid, tool=name, args=json.dumps(args, ensure_ascii=False, default=str))
        answered = done.wait(timeout=600)
        with self._lock:
            self._pending.pop(uid, None)
            ok, note = self._answers.pop(uid, (False, ""))
        if not answered:
            return False  # 超时：视为拒绝
        if ok is True:
            return True
        if isinstance(note, str) and note.strip():
            return note.strip()
        return False

    def respond(self, uid: str, ok: bool, note: str = "") -> str:
        with self._lock:
            done = self._pending.get(uid)
            if done is not None:
                self._answers[uid] = (bool(ok), str(note or ""))
        if done is None:
            return "unknown confirm id"
        done.set()  # 先写答复，再唤醒等待方（重复 respond 会覆盖答复但不再 set 出错）
        self._emit("sys", "✔ 已放行写入" if ok else "✘ 已拒绝写入")
        return "ok"


def _avatar_data_uri() -> str:
    """新版界面自带徽章头像，不再注入图片；保留空实现以兼容调用点。"""
    return ""


def main() -> None:
    if os.environ.get("WAKA_DEBUG"):
        import logging
        logging.basicConfig(level=logging.DEBUG, force=True)
        print("[dbg] assistant_web.main 开始", flush=True)
    if webview is None:
        raise RuntimeError("未安装 pywebview：" + (str(_WEBVIEW_ERR) or ""))

    html = UI_HTML
    if not html.is_file():
        raise RuntimeError(f"缺少界面文件：{html}。请先运行 scripts/build_live_ui.py 生成它。")
    html_text = html.read_text(encoding="utf-8")
    if os.environ.get("WAKA_DEBUG"):
        print("[dbg] html 读取完成，长度", len(html_text), flush=True)

    bridge = Bridge()
    win = webview.create_window(
        "科研小助手 · AI 研究助理",
        html=html_text,
        js_api=bridge,
        width=1120,
        height=780,
        min_size=(760, 540),
        background_color="#fff6ea",
        text_select=True,   # 允许鼠标拖选文字（配合右键菜单复制/粘贴）
    )
    # 让界面里需要头像的地方拿到图
    bridge._avatar_uri = _avatar_data_uri()
    if os.environ.get("WAKA_DEBUG"):
        print("[dbg] create_window 返回", win, flush=True)
        import pathlib as _pl
        try:
            import clr
            print("[dbg] clr import ok", flush=True)
        except Exception as e:
            print("[dbg] clr import FAIL:", repr(e), flush=True)
        try:
            import clr_loader
            print("[dbg] clr_loader ok", flush=True)
        except Exception as e:
            print("[dbg] clr_loader FAIL:", repr(e), flush=True)
        try:
            import webview.platforms.winforms as _wf
            print("[dbg] winforms backend import ok", flush=True)
        except Exception as e:
            print("[dbg] winforms import FAIL:", repr(e), flush=True)
        try:
            _wv = webview.__file__
            print("[dbg] webview @", _wv, flush=True)
            print("[dbg] lib exists:", (_pl.Path(_wv).parent / "lib").exists(), flush=True)
        except Exception as e:
            print("[dbg] webview inspect FAIL:", repr(e), flush=True)

    # 启动后把 data uri 注入 JS 全局（页面脚本里用 window.__AVATAR）
    def _inject_avatar():
        try:
            win.evaluate_js("window.__AVATAR=" + json.dumps(bridge._avatar_uri))
        except Exception:
            pass

    icon = str(ICO) if ICO.is_file() else None
    try:
        webview.start(_inject_avatar, gui="edgechromium", icon=icon, debug=False)
    except Exception as e:
        if os.environ.get("WAKA_DEBUG"):
            import traceback
            traceback.print_exc()
        raise
    if os.environ.get("WAKA_DEBUG"):
        print("[dbg] webview.start 返回（说明窗口被关闭或未创建）", flush=True)


if __name__ == "__main__":
    main()
