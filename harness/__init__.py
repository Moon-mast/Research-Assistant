"""harness —— 一个接入任意 OpenAI 兼容大模型的最小 agent 工具循环。

快速上手（配好 .env 后）：
    from harness import Agent
    agent = Agent()
    print(agent.run("现在几点了？"))
"""
from .agent import Agent, IterationLimitError
from .builtin_tools import get_builtins
from .config import HarnessConfig
from . import providers
from .providers import (
    PROVIDERS,
    Provider,
    apply_credentials,
    clear_api_key,
    critic_default_for,
    main_model_for,
    match_provider,
    set_collab,
    set_wallpaper,
    set_wallpaper_image,
    set_wallpaper_video,
    wallpaper,
    wallpaper_image,
    wallpaper_video,
)
from .skills import (
    Skill,
    SkillError,
    SkillNotFoundError,
    activate_skills,
    catalog_block,
    default_skills_dir,
    discover,
    load_skill,
    make_skill_tools,
    skill_instructions,
)
from .team import Collaboration
from .tools import REGISTRY, Tool, ToolResult, build_ask_tool, make_tool, tool
from .workspace import FolderWorkspace, build_folder_tools

__all__ = [
    "Agent",
    "IterationLimitError",
    "Collaboration",
    "HarnessConfig",
    "tool",
    "make_tool",
    "build_ask_tool",
    "Tool",
    "ToolResult",
    "REGISTRY",
    "get_builtins",
    "PROVIDERS",
    "Provider",
    "apply_credentials",
    "clear_api_key",
    "match_provider",
    "main_model_for",
    "critic_default_for",
    "set_collab",
    "set_wallpaper",
    "set_wallpaper_image",
    "set_wallpaper_video",
    "wallpaper",
    "wallpaper_image",
    "wallpaper_video",
    "providers",
    "FolderWorkspace",
    "build_folder_tools",
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

__version__ = "0.12.0"
