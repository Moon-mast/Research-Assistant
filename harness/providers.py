"""厂商预设目录 + 一键写入 .env（GUI 设置面板 / CLI 向导共用）。

用法（GUI/CLI 之外也能直接用）：
    providers.apply_credentials(base_url, api_key, model)  # 写入项目 .env
配置写好后，harness 的 CLI / GUI 下次启动即读取同一份 .env，实现"只填密钥即可切换厂商"。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = Path(os.environ.get("HARNESS_ENV_FILE", PROJECT_ROOT / ".env"))

# 写入 .env 时用到的键
KEY_BASE_URL = "LLM_BASE_URL"
KEY_API_KEY = "LLM_API_KEY"
KEY_MODEL = "LLM_MODEL"
KEY_COLLAB = "COLLAB_ENABLED"
KEY_COLLAB_CRITIC = "COLLAB_CRITIC_MODEL"
KEY_WALLPAPER = "CHAT_WALLPAPER"      # 聊天区壁纸（背景色，十六进制）
KEY_WALLPAPER_IMG = "CHAT_WALLPAPER_IMG"  # 聊天区壁纸图片文件路径（有则优先生效）
KEY_WALL_VIDEO = "CHAT_WALL_VIDEO"        # 聊天区动态壁纸视频路径（优先级最高）


@dataclass
class Provider:
    id: str
    name: str
    base_url: str
    models: list[str]
    needs_key: bool = True
    note: str = ""
    light: str | None = None  # "审查/轻量"角色的默认模型（多模型协作用）


# 每家预设 base_url 与推荐模型；模型均可自行输入任意值（只是下拉建议）
# light = "主答+审查"协作里审查/轻量角色的默认模型
PROVIDERS: list[Provider] = [
    Provider("openai", "OpenAI", "https://api.openai.com/v1",
             ["chatgpt-6-sol", "gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"],
             light="gpt-4o-mini",
             note="前沿模型与旧模型都在下拉里，模型名以「🔄 实时获取」的端点列表为准"),
    Provider("deepseek", "DeepSeek", "https://api.deepseek.com/v1",
             ["deepseek-v4-flash-1017", "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-chat", "deepseek-reasoner"],
             light="deepseek-v4-flash-1017",
             note="V4 系列(flash-1017 为最新快照 / pro 主答 / flash 审查或轻量)工具调用与多智能体协作能力强；deepseek-chat 为通用别名；reasoner 偏推理，工具调用表现可能不稳"),
    Provider("dashscope", "通义千问 Qwen（阿里云）", "https://dashscope.aliyuncs.com/compatible-mode/v1",
             ["qwen-max", "qwen-plus", "qwen-turbo", "qwen2.5-72b-instruct"],
             light="qwen-turbo"),
    Provider("moonshot", "Kimi (Moonshot)", "https://api.moonshot.cn/v1",
             ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k", "kimi-k2-0711-preview"],
             light="moonshot-v1-8k",
             note="模型名以官网为准，可在框内任意输入"),
    Provider("zhipu", "智谱 GLM", "https://open.bigmodel.cn/api/paas/v4",
             ["glm-4-flash", "glm-4-plus", "glm-4-air", "glm-4-long"],
             light="glm-4-flash",
             note="GLM 密钥不带 sk- 前缀"),
    Provider("siliconflow", "硅基流动 SiliconFlow", "https://api.siliconflow.cn/v1",
             ["Qwen/Qwen3-235B-A22B-Instruct-2507", "deepseek-ai/DeepSeek-V3", "THUDM/glm-4-9b-chat"],
             light="THUDM/glm-4-9b-chat",
             note="开源模型聚合平台，模型名需带组织前缀，见官网"),
    Provider("ollama", "本地 Ollama（无需联网）", "http://localhost:11434/v1",
             ["qwen2.5:7b", "llama3.1:8b", "deepseek-r1:7b"], needs_key=False,
             light="qwen2.5:7b",
             note="key 填任意占位（如 ollama）；模型名需与 `ollama list` 输出一致"),
]

# 额外加一个"自定义"入口（在列表末端，name 用不到写任意）
CUSTOM_NAME = "自定义（手动填写 base_url/model）"


def fetch_remote_models(base_url: str, api_key: str = "", timeout: float = 8.0) -> list[str]:
    """实时拉取该端点当前可用的模型列表（OpenAI 兼容 GET /models）。

    模型版本不固化：界面/向导应优先用本函数的实时结果，PROVIDERS 里的静态列表
    只在拉取失败（无 key / 端点不支持 /models / 断网）时作回退建议。
    返回去重后的模型 id 列表（保持端点返回顺序——厂商通常把最新模型排在前）；
    失败抛异常，由调用方决定回退到静态建议。
    """
    try:
        from openai import OpenAI
    except Exception as e:  # pragma: no cover
        raise RuntimeError("未安装 openai 包，无法拉取模型列表") from e
    client = OpenAI(base_url=base_url, api_key=api_key or "empty",
                    timeout=timeout, max_retries=0)
    ids = [str(m.id) for m in client.models.list()]
    return list(dict.fromkeys(ids))


def provider_by_name(name: str) -> Provider | None:
    return next((p for p in PROVIDERS if p.name == name), None)


def provider_names() -> list[str]:
    return [p.name for p in PROVIDERS]


def match_provider(base_url: str) -> Provider | None:
    """按 base_url 反查预设（.env 已配某厂商时，界面能自动选中它）。"""
    url = (base_url or "").rstrip("/")
    for p in PROVIDERS:
        if url == p.base_url.rstrip("/"):
            return p
    return None


def _read_env_lines() -> list[str]:
    if ENV_PATH.is_file():
        return ENV_PATH.read_text(encoding="utf-8").splitlines()
    return []


def main_model_for(base_url: str) -> str | None:
    """某 base_url 对应厂商的默认"主答"模型。"""
    p = match_provider(base_url)
    return p.models[0] if p and p.models else None


def critic_default_for(base_url: str) -> str | None:
    """某 base_url 对应厂商的默认"审查/轻量"模型（多模型协作的副角色）。"""
    p = match_provider(base_url)
    if not p or not p.models:
        return None
    return p.light or p.models[0]


def _write_env_keys(changes: dict[str, str | None]) -> None:
    """更新 .env 中若干键（值为 None 表示删除该行），保留注释及其它项。"""
    lines = _read_env_lines()
    out: list[str] = []
    touched: set[str] = set()
    for ln in lines:
        stripped = ln.strip()
        if "=" in stripped and not stripped.startswith(("#", ";")):
            key = stripped.split("=", 1)[0].strip()
            if key in changes:
                value = changes[key]
                if value is not None:
                    out.append(f"{key}={value}")
                touched.add(key)
                continue
        out.append(ln)
    for key, value in changes.items():
        if key not in touched and value is not None:
            out.append(f"{key}={value}")
    text = "\n".join(out)
    if text.strip():
        text += "\n"
    ENV_PATH.write_text(text, encoding="utf-8")


def set_collab(enabled: bool, critic_model: str) -> None:
    """写入"主答+审查"协作开关与审查模型到 .env 并同步进程内环境变量。"""
    changes = {KEY_COLLAB: "1" if enabled else "0"}
    if enabled and critic_model.strip():
        changes[KEY_COLLAB_CRITIC] = critic_model.strip()
    else:
        changes[KEY_COLLAB_CRITIC] = None  # 关闭时移除审查模型记录
    _write_env_keys(changes)
    os.environ[KEY_COLLAB] = changes[KEY_COLLAB]
    if KEY_COLLAB_CRITIC in changes and changes[KEY_COLLAB_CRITIC] is not None:
        os.environ[KEY_COLLAB_CRITIC] = changes[KEY_COLLAB_CRITIC]
    elif KEY_COLLAB_CRITIC in os.environ:
        os.environ.pop(KEY_COLLAB_CRITIC, None)


def apply_credentials(base_url: str, api_key: str, model: str) -> None:
    """把 (base_url, api_key, model) 写入 .env，保留注释及其它配置项。

    同时同步进程内 os.environ，使同一进程里 HarnessConfig.from_env() 立即读到新值。
    """
    base_url, api_key, model = base_url.strip(), api_key.strip(), model.strip()
    changes = {KEY_BASE_URL: base_url, KEY_MODEL: model}
    if api_key:
        changes[KEY_API_KEY] = api_key  # 本地 Ollama 允许留空/任意占位

    lines = _read_env_lines()
    out: list[str] = []
    written: set[str] = set()
    for ln in lines:
        stripped = ln.strip()
        if "=" in stripped and not stripped.startswith(("#", ";")):
            key = stripped.split("=", 1)[0].strip()
            if key in changes:
                out.append(f"{key}={changes[key]}")
                written.add(key)
                continue
        out.append(ln)
    for key in changes:
        if key not in written:
            out.append(f"{key}={changes[key]}")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")

    os.environ[KEY_BASE_URL] = base_url
    os.environ[KEY_MODEL] = model
    if api_key:
        os.environ[KEY_API_KEY] = api_key
    elif KEY_API_KEY in os.environ:
        os.environ.pop(KEY_API_KEY, None)


def wallpaper() -> str:
    """当前聊天区壁纸颜色（十六进制），未设置则为默认白。"""
    v = (os.environ.get(KEY_WALLPAPER) or "").strip()
    return v if re_fullmatch_hex(v) else "#ffffff"


def re_fullmatch_hex(v: str) -> bool:
    import re
    return bool(re.fullmatch(r"#[0-9a-fA-F]{6}", v))


def set_wallpaper(hex_color: str | None) -> None:
    """写入/清除聊天区壁纸颜色（写 .env 并同步进程内环境变量）。"""
    color = (hex_color or "").strip().lower()
    if color and not re_fullmatch_hex(color):
        raise ValueError(f"颜色需为 #rrggbb 格式，收到：{color!r}")
    changes = {KEY_WALLPAPER: color if color else None}
    _write_env_keys(changes)
    if color:
        os.environ[KEY_WALLPAPER] = color
    else:
        os.environ.pop(KEY_WALLPAPER, None)


def wallpaper_image() -> str:
    """当前壁纸图片路径；未设置则空串。"""
    return (os.environ.get(KEY_WALLPAPER_IMG) or "").strip()


def set_wallpaper_image(path: str | None) -> None:
    """设置/清除壁纸图片路径（写 .env 并同步进程内环境变量）。"""
    p = (path or "").strip()
    changes = {KEY_WALLPAPER_IMG: p if p else None}
    _write_env_keys(changes)
    if p:
        os.environ[KEY_WALLPAPER_IMG] = p
    else:
        os.environ.pop(KEY_WALLPAPER_IMG, None)


def wallpaper_video() -> str:
    """当前动态壁纸视频路径；未设置则空串。"""
    return (os.environ.get(KEY_WALL_VIDEO) or "").strip()


def set_wallpaper_video(path: str | None) -> None:
    """设置/清除动态壁纸视频路径（写 .env 并同步进程内环境变量）。"""
    p = (path or "").strip()
    changes = {KEY_WALL_VIDEO: p if p else None}
    _write_env_keys(changes)
    if p:
        os.environ[KEY_WALL_VIDEO] = p
    else:
        os.environ.pop(KEY_WALL_VIDEO, None)


def clear_api_key() -> None:
    """从 .env 移除已保存的 API key（并清掉进程内环境变量）。

    用于"每次启动窗口后强制重新填写"，避免密钥长期留在磁盘上。
    """
    if not ENV_PATH.is_file():
        os.environ.pop(KEY_API_KEY, None)
        return
    lines = _read_env_lines()
    out = [
        ln for ln in lines
        if not ln.strip() or ln.strip().startswith(("#", ";"))
        or ln.partition("=")[0].strip() != KEY_API_KEY
    ]
    text = "\n".join(out)
    if text.strip():
        text += "\n"
    ENV_PATH.write_text(text, encoding="utf-8")
    os.environ.pop(KEY_API_KEY, None)


def current() -> tuple[str, str, str]:
    """返回当前 .env 里的 (base_url, api_key, model)，未配置则空串。"""
    base = api = model = ""
    for ln in _read_env_lines():
        if "=" in ln and not ln.strip().startswith("#"):
            k, _, v = ln.partition("=")
            k = k.strip()
            if k == KEY_BASE_URL:
                base = v.strip()
            elif k == KEY_API_KEY:
                api = v.strip()
            elif k == KEY_MODEL:
                model = v.strip()
    return base, api, model


def summary() -> str:
    base, api, model = current()
    prov = match_provider(base)
    vendor = prov.name if prov else ("自定义" if base else "未配置")
    key_hint = (api[:4] + "…" + api[-4:]) if api else "未填"
    return f"厂商={vendor}  model={model or '未设'}  key={key_hint}"


# --------------------------------------------------------------------------
# CLI 向导：python -m harness.select
# --------------------------------------------------------------------------
def run_wizard() -> None:
    print("== 选择接入的厂商 ==")
    print(f"{0:>2}. {CUSTOM_NAME}")
    for i, p in enumerate(PROVIDERS, start=1):
        flag = "" if p.needs_key else "（无需真实密钥）"
        print(f"{i:>2}. {p.name}  {flag}")
    try:
        choice = int(input("\n请输入序号: ").strip())
    except (ValueError, EOFError):
        print("输入无效，已取消。")
        return
    if choice < 0 or choice > len(PROVIDERS):
        print("序号超出范围，已取消。")
        return

    if choice == 0:
        base_url = input(f"base_url（{KEY_BASE_URL}）: ").strip()
        key = input(f"api_key（留空则不写入 {KEY_API_KEY}）: ").strip()
        model = ""
        try:
            live = fetch_remote_models(base_url, key)
            if live:
                print(f"实时获取到 {len(live)} 个模型（前 20 个）：")
                print("  " + " / ".join(live[:20]))
                model = input("model（回车=用列表第一个）: ").strip() or live[0]
        except Exception as e:
            print(f"（实时拉取模型列表失败：{e}；请手动填写）")
        if not model:
            model = input(f"model（{KEY_MODEL}）: ").strip()
        if not base_url or not model:
            print("base_url/model 不能为空，已取消。")
            return
    else:
        p = PROVIDERS[choice - 1]
        base_url = p.base_url
        print(f"\n厂商：{p.name}")
        print(f"base_url：{p.base_url}")
        if p.note:
            print(f"提示：{p.note}")
        key = "" if not p.needs_key else input("api_key: ").strip()
        if p.needs_key and not key:
            print("需要 API key，已取消。")
            return
        model = ""
        try:
            live = fetch_remote_models(p.base_url, key)
            if live:
                print(f"实时获取到 {len(live)} 个模型（前 20 个）：")
                print("  " + " / ".join(live[:20]))
                model = input("model（回车=用实时列表第一个，总是该端点当前最新）: ").strip() or live[0]
        except Exception:
            if p.models:
                print("（实时拉取失败，改用静态建议：" + " / ".join(p.models) + "）")
        if not model:
            model = input(f"model（{'默认 ' + p.models[0] if p.models else '必填'}）: ").strip() or (p.models[0] if p.models else "")
        if not model:
            print("model 不能为空，已取消。")
            return

    apply_credentials(base_url=base_url, api_key=key, model=model)
    print(f"\n✔ 已写入 {ENV_PATH}：")
    print(f"   {KEY_BASE_URL} = {base_url}")
    print(f"   {KEY_MODEL}   = {model}")
    if key:
        print(f"   {KEY_API_KEY} = {key[:4]}…{key[-4:]}")
    print("\n现在可直接运行： python -m harness.gui  或  python -m harness.cli")


if __name__ == "__main__":
    run_wizard()
