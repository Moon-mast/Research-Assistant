# Research-Assistant · 科研小助手

基于 **Agent 工具循环 harness** 的 **AI 科研助理**（Python 实现）：接入任意 **OpenAI 兼容接口**大模型，
通过内置的 `research-assistant` 技能，把论文 PDF 管理、解析、问答、翻译、下载推荐、综述、写作辅助装进一个本地桌面助手。
底层是一个可扩展的最小 agent 工具循环——模型自主决定调用工具、结果回填后继续推理，直到给出最终答案（Claude Code / Agent 类应用的核心闭环）。

## 科研小助手的能力（skills/research-assistant）

| 能力 | 说明 |
|---|---|
| 论文库管理与去重 | 批量导入 PDF；按**内容**去重（文件名不同但内容相同，如 arXiv v1/v7），让用户选择并建议保留新版本 |
| 细粒度解析 | 标题/作者/摘要/多级标题/表格/图表/公式(LaTeX)/参考文献/附录；扫描版走 OCR，超长书籍分块建索引 |
| 单篇问答（RAG） | 检索相关块作答，附**原文引用 + 页码**可溯源防幻觉；数值问题优先精确匹配表格/实验设置段 |
| 多篇对比 | 跨论文检索汇总成对比表（架构/参数量/LN 位置/上下文长度…），逐项给双方原文依据 |
| 阅读与翻译 | 摘要/划词/整篇英→中（代码与公式保留英文），Markdown 重排 + 中英对照阅读 |
| 下载与推荐 | 按名搜索下载入库；解析 References 批量下载（可筛重要性）；arXiv OAI 定期刷新 + 每日/每周推荐（附推荐理由、有 GitHub 代码优先） |
| 引用图谱与综述 | 多篇互引网络 → 图论挖掘"基石论文"与"边缘衍生"，输出 Mermaid/DOT 引用图、领域综述、未来方向 |
| 写作助手 | idea→论文框架（References 必须真实无幻觉）；TikZ/Graphviz/Mermaid/Matplotlib 架构图；实验 CSV→图表 + 学术 Figure Caption；中译英 |

## Harness 本体能力

- 切换厂商零改代码：`base_url` / `api_key` / `model` 全部由环境变量 / `.env` 驱动。
- 工具即函数：`@tool` 装饰一个带类型注解 + docstring 的函数，pydantic 自动生成 schema 并校验入参。
- 错误自愈：工具抛异常 / 参数不对 / 超时，都以清晰文本回传模型，让它自己纠错。
- 内置无副作用示例工具：时间/日期/安全计算器（`ast` 白名单求值）。
- 本地技能（Skills）：检测到 `skills/` 即自动启用，把素材库/说明书封装成模型可按需拉取的能力包。
  开箱自带 `galaxy-ui`——套用本地 **Galaxy（Uiverse.io）3800+ 组件库**做网页 UI，生成单文件 HTML 并在浏览器预览。

## 目录结构

```
Research-Assistant/
├── harness/
│   ├── config.py        # 环境变量 + .env -> HarnessConfig
│   ├── providers.py     # 厂商预设目录(各家 base_url/推荐模型) + 写 .env
│   ├── select.py        # CLI 厂商选择向导（python -m harness.select）
│   ├── tools.py         # @tool 装饰器、注册表、签名->schema、安全执行(超时/异常)
│   ├── skills.py        # 本地技能系统：SKILL.md 解析、技能清单注入、use_skill/library_* 工具
│   ├── client.py        # openai 官方 SDK -> 任意兼容 base_url 的封装
│   ├── context.py       # 上下文压缩纯逻辑：token 估算/预算/旧轮折叠/会话回顾/recall 检索（省 token）
│   ├── agent.py         # Agent：tool_calls 循环、历史管理、压缩、迭代守卫、inject_system
│   ├── team.py          # 多模型协作：主答 + 审查改进（Collaboration）
│   ├── workspace.py     # 选定工作目录 -> list_files/read_file/write_file 文件工具(带越界防护)
│   ├── builtin_tools.py # 内置工具：calculator / get_current_time / get_current_date
│   ├── cli.py           # 交互式 REPL（python -m harness.cli，/skills /skill 命令）
│   ├── gui.py           # 桌面窗口版（pythonw -m harness.gui，零第三方依赖，顶栏「技能」）
│   └── __init__.py      # 对外 API：Agent / tool / get_builtins / discover …
├── assistant_web.py    # 科研小助手桌面入口（pywebview + Edge WebView2，渲染 assets/assistant_ui.html）
├── run-assistant.bat   # 双击启动科研小助手桌面版
├── run-gui.bat         # 双击启动 tkinter 窗口版
├── assets/             # assistant_ui.html（整窗界面）+ assistant.ico（图标）
├── examples/
│   ├── quickstart.py    # 最小程序化用法（含自定义工具示例）
│   ├── self_check.py    # 离线自检：工具/schema/执行路径（无需 API key、不联网）
│   ├── skills_check.py  # 离线自检：技能系统 + galaxy-ui（发现/解析/越界防护/激活）
│   └── mock_e2e.py      # 离线端到端：本地假服务跑完整条 agent 循环（无需密钥）
├── skills/              # 本地技能目录（检测到即自动启用）
│   ├── galaxy-ui/       # 技能：用本地 Galaxy 组件库做网页 UI
│   │   ├── SKILL.md     #   说明书：何时用/素材库浏览/标准工作流/质量要求
│   │   └── references/  #   CATALOG.md(分类+风格索引) + ADAPT.md(搬运细则)
│   ├── research-assistant/  # 技能：科研小助手（论文全流程 8 条工作流）
│   │   ├── SKILL.md         #   说明书：入库/解析/问答/翻译/推荐/图谱/写作
│   │   └── references/      #   requirements.md（产品需求底稿）
│   └── _TEMPLATE/       # 新技能模板（目录以下划线开头会被自动跳过）
├── scripts/
│   └── build_galaxy_catalog.py   # 重扫 galaxy-main -> 重写 skills/galaxy-ui 的 CATALOG.md
├── galaxy-main/         # Uiverse.io Galaxy 组件库素材（galaxy-ui 技能只读引用，MIT）
├── .env.example         # 复制为 .env 使用
├── requirements.txt
└── README.md
```

## 快速开始

> Windows 下建议用 `py -3.12`（其它平台用 `python`，下同）。

```bash
cd "D:\my harness"

# 1) 建虚拟环境并安装依赖（仅首次）
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

# 2) 选厂商、只填密钥（cc-switch 式预设，自动写 .env，无需手动编辑）
#    命令行向导：
.venv\Scripts\python -m harness.select
#    或在 GUI 里点顶栏「设置」选厂商填密钥

# 3) 离线验证（无需密钥）
.venv\Scripts\python examples\self_check.py   # 工具/schema/执行路径
.venv\Scripts\python examples\skills_check.py # 技能系统 + galaxy-ui
.venv\Scripts\python examples\mock_e2e.py     # 本地假服务跑完整条 agent 循环

# 4) 启动交互界面
#    科研小助手桌面版（推荐；首次需装 pywebview，或直接双击 run-assistant.bat）：
.venv\Scripts\pip install pywebview pypdf
.venv\Scripts\pythonw assistant_web.py
#    命令行版：
.venv\Scripts\python -m harness.cli
#    tkinter 桌面窗口版（零第三方依赖）：
.venv\Scripts\pythonw -m harness.gui
```

## 厂商一键接入（像 cc-switch 一样，只需填密钥）

内置多家厂商预设，每家的 `base_url` 与推荐模型都已配好，**你只需填 API key**（本地 Ollama 连密钥都不用）：

- **GUI**：打开窗口点顶栏「设置」→ 下拉选厂商 → 填密钥 → 保存并应用。保存即时生效（重建 Agent 并清空上下文），同时写入项目 `.env`，CLI 下次启动用同一份配置。
- **CLI**：运行 `python -m harness.select`，按序号选厂商 → 填密钥 → 完成。

已内置：**OpenAI / DeepSeek / 通义千问 / Kimi / 智谱 GLM / 硅基流动 / 本地 Ollama**，另加「自定义」手填任意 OpenAI 兼容端点。

**模型版本不固化，老模型保留**：设置面板的模型下拉实时调用该端点的 `GET /models`（GUI 里点「🔄 实时获取」；CLI 向导在填完 key 后自动拉取），展示**当前最新**的可用模型；**每次启动也会后台自动同步**一次（厂商上新模型无需任何操作即出现在下拉里）。下拉列表 = 实时最新 + 静态预设旧模型 + 当前已用模型 合并去重——换到前沿模型（如 `deepseek-v4-flash-1017`、`chatgpt-6-sol`）的同时，老模型仍然可选。模型框留空保存＝自动选用实时列表的第一个（即该端点当前默认的最新模型）。

面向 **agent / 多智能体协作**的模型已预置：DeepSeek 含 **`deepseek-v4-pro`（主答）+ `deepseek-v4-flash`（轻量/审查）**，其它厂商也各标了一个轻量模型用于协作里的审查角色。

对应实现：`harness/providers.py` 的 `PROVIDERS` 目录 + `apply_credentials()`（保留 .env 里的注释和其它配置项）。想加厂商，往目录里加一条 `Provider(...)` 即可。

## 多模型协作：「主答 + 审查改进」

一个任务让**两个不同模型配合**：主模型（可调工具）完整作答 → 审查模型（轻量、无工具）检验；
不通过就给出具体修改意见并打回主模型重做，最多 N 轮，直到审查通过。

- **开启**（GUI：设置面板勾选「多模型协作」，可另选审查模型；CLI：`/collab on`，`/collab off` 关闭）。
- 两个角色**共用同一厂商/同一密钥**，只是模型不同——审查建议用便宜快速的轻量模型，
  例如 DeepSeek 主答 `deepseek-v4-pro` + 审查 `deepseek-v4-flash`（厂商预设里已自动给出）。
- 状态会写入 `.env`（`COLLAB_ENABLED` / `COLLAB_CRITIC_MODEL`），单模型/协作两种模式随时切换。
- 实现：`harness/team.py` 的 `Collaboration`。主答模型 = 你当前选的模型；审查默认取该厂商的 light 模型（可用 `/collab on` 时或 GUI 里覆盖）。

```python
# 程序化用法：两模型协作完成一条任务
from harness import Collaboration, HarnessConfig
team = Collaboration(HarnessConfig.from_env())            # 审查默认同厂商轻量模型
ans = team.run("写一个快速排序并解释复杂度")                # 内部：主答→审查→(打回修改…)→通过
```

```python
# 库用法：程序里也能直接切厂商（写 .env + 同步环境变量）
from harness import apply_credentials, HarnessConfig, Agent
apply_credentials("https://api.deepseek.com/v1", "sk-xxx", "deepseek-chat")
agent = Agent(HarnessConfig.from_env())
```

## 上下文压缩：多轮会话省 token（参考 headroom）

长会话是 token 消耗的大头：每一轮都会把**整段历史（含旧的工具输出）原样重发**，历史越积越重。
本 harness 参考 [headroom](https://github.com/headroomlabs-ai/headroom)（开源"上下文压缩层"）的思路，
内置了一层**零依赖、确定性**的压缩，默认开启：

- **旧轮折叠**：新一轮开始时，若历史估算 token 超过预算，把**更早、已完成**的轮次折成一条很短的
  「会话回顾」小结（`用户问 → 结论`，免费、不额外调用模型）；最近 1 轮全文保留、当前轮绝不动。
- **单轮中途也自动压（`CONTEXT_COMPRESS_MID=1`）**：同一问题里的多步工具调用还没跑完、但这轮历史已
  超预算时，自动折叠**更早、已被模型消费**的工具回合（只保留最近几个回合 + 正在进行的这步），
  避免一轮长任务越跑越贵甚至超窗。
- **CCR 可检索存档**：被折叠轮次 / 回合的**原文**存入会话内存档；模型拿不准早先某次工具调用的精确
  输出 / 路径 / 数字时，可调用 `recall_history(关键词)` 取回原文片段，而不是臆造。
- **只在实际省钱时折叠**：折叠不省 token（例如都是很短的轮次）会被跳过，不会越压越大。
- **前缀稳定 → 命中厂商缓存**：折叠之间已发消息保持不变，请求前缀稳定，能命中 DeepSeek / 通义 /
  Kimi / GLM 等厂商的**自动前缀 KV 缓存**（前缀相同的部分按缓存折扣价计费）。

压缩只发生在轮次边界（新一轮 user 之前）或工具回合间隙，**绝不打断进行中的工具执行**，也绝不破坏
`tool_calls ↔ tool` 配对；上一轮若因超迭代/失败留下悬空工具块，会自动愈合，保证历史始终合法。

相关配置见文末「配置项」表（`CONTEXT_*`，默认开启）。想完全回到旧行为：`CONTEXT_COMPRESS=0`。
程序化用法里 `agent.context_stats` 存有最近一次压缩统计（是否触发 / 省了多少 token），`verbose` 会打印。

> 估算器是启发式的（无外部 tokenizer），预算只是"闸门"而非精确计费；
> `CONTEXT_BUDGET_TOKENS=0` 时按模型窗口自动取值（约 55%，如 moonshot-v1-8k 会自动取更小预算）。

## 选定文件夹：让模型读写你目录里的文件

点一个文件夹，把它变成 agent 的工作区，模型就能在对话里**浏览、读取，并写入**其中的文件
（每次写入前都会先征求你的确认）——比如让它按你的要求在这个目录里生成/改写代码。所有访问都限定在该目录内：

- 顶栏「**选择文件夹…**」打开系统目录选择框；选定后模型获得完整工作区工具包：
  - `list_files(path=".", recursive=False)` —— 列出目录内容；
  - `read_file(path, max_chars=12000)` —— 读取文本文件（UTF-8，超长自动截断）；
  - `pdf_info(path)` / `pdf_extract(path, first_page, last_page)` —— 读取论文 PDF：pypdf 自动解压
    FlateDecode 压缩流、带页码标记分页抽取，`pdf_info` 还能给页数/标题/作者并识别扫描图片页
    （PDF 是二进制压缩格式，不能直接 read_file）；
  - `python_exec(code)` —— 执行一段 Python 并返回 print 输出（**执行前必须经你确认**），
    兜底数据处理/格式转换/调用第三方库等场景；
  - `write_file(path, content)` —— 把文本/代码写入文件：不存在则新建（缺失子目录自动创建），已存在则覆盖。**执行前必须经你确认**。
- **写入前确认（Claude Code 式、不弹窗）**：GUI 会把「目标路径 + 将要写入的代码」**内联展示在聊天区**，输入区上方出现确认条——三个按钮 **✔ 允许写入 / 本会话都允许 / ✘ 拒绝**；或直接像回消息一样操作：**空白回车=允许，输入理由后回车=拒绝并说明**。拒绝时你的理由会回传给模型，让它按意见改。CLI 同理会先打印内容再问 `y/n`。
- **越界防护**：`..` 逃逸、指向目录外的绝对路径、目录外符号链接都会被拒，并以错误文本回传模型。写操作只落在所选目录内、绝不删除文件。
- ⚠ `write_file` 会**覆盖同名文件**：请选一个专门的输出目录，模型只会动它选中的那个文件夹。
- 切换厂商、单模型↔协作模式后，文件夹工具会自动保持。
- CLI 等价命令：`/folder D:\某目录`（协作主模型同样可用）。

```python
# 库用法：把文件夹工具挂到任意 Agent 上
from harness import FolderWorkspace, build_folder_tools, Agent, HarnessConfig
ws = FolderWorkspace(r"D:\my project")
agent = Agent(HarnessConfig.from_env())
agent.add_tools(build_folder_tools(ws))        # 之后模型即可 list_files / read_file / write_file
ws.set_root(r"D:\another")                       # 换个目录，同一批工具自动跟随新根
```

### 命令行版（CLI）
进 CLI 后直接提问，例如：

```
你 > 现在几点了？顺便帮我算一下 sqrt(2026) 保留 4 位小数
```

会看到它自主完成「调工具 → 拿结果 → 回答」的过程（`⚙ 调用 …`），支持 `/tools`、`/config`、`/clear`、`/help`、`/exit`。

### 桌面窗口版（GUI）
基于 Python 自带的 tkinter，**零第三方依赖**。聊天界面会实时回显每一次工具调用与结果；顶栏按钮对应命令，Enter 发送、Shift/Ctrl+Enter 换行。Agent 的阻塞请求跑在后台线程，界面不会卡死。

复制粘贴：对话区（只读）**鼠标拖选 + Ctrl+C** 或 **右键菜单**（复制选中 / 复制全部 / 全选）即可复制回答；下方输入框右键可剪切/复制/粘贴；设置面板的 base_url、api_key 输入框支持右键粘贴（密钥那格还可右键「复制密钥原文」——显示的是 ••••，复制的才是真值）。

安全性：**窗口每次启动都会清空 `.env` 里已保存的 `LLM_API_KEY`**，设置面板的密钥框也每次打开都保持空白——即密钥不长期留在磁盘上，每次会话都要重新粘贴才可用（厂商/模型等其它配置不受影响）。

聊天区壁纸：设置里可换背景——预设/自定义**颜色**（深色自动切浅字）、**从文件夹选图片/GIF**（png/jpg/bmp/webp，GIF 自动播放），或 **选 mp4/avi 等视频当动态壁纸**（OpenCV 后台解码、自动循环铺满、气泡浮在画面上）。均支持任意缩放、写 `.env` 持久化、下次启动自动恢复（视频>图片>颜色）。

## 模型中途向你提问（ask_user）

不只写文件，模型在**任务中途**也能停下来问你问题——需要澄清、补充信息或在几个方案里做选择时，
它会调用 `ask_user(question, options)`：

- **GUI**：问题以内联消息出现在聊天区（`💬 模型提问：…`），带可选项提示；你在**输入框**直接回答后回车，它拿到回答继续。
- **CLI**：打印 `💬 模型提问：…` 后等你输入 `你的回答 >`。
- 单模型、协作主模型都带此能力；`options` 用 `/` 分隔仅供展示，你仍可自由输入任意答案。
- 可在问题前直接要求它先问你，例如：“写个 Python 脚本到工作目录，但**先问我**要脚本风格/技术选型，别擅自决定。”

```python
# 库用法：自定义如何应答模型提问
def my_ask(name, args):
    return input(f"模型提问：{args.get('question')} → ")   # 返回用户回答，继续任务
agent = Agent(HarnessConfig.from_env(), ask_handler=my_ask)
agent.add_tools([build_ask_tool()])
```

## 本地技能（Skills）：把素材库/说明书封装成 agent 能力包

技能 = `skills/<名字>/SKILL.md`（Markdown：`---` 前端 + 正文，可带 `references/*.md`），
本质是一份“教模型怎么做”的说明书，外加可选的一个**只读素材库**。检测到 `skills/` 目录时
CLI / GUI 每个会话都会**自动启用**：给主模型注入一份极简技能清单，并注册一批技能工具——
技能正文**只在模型需要时**通过 `use_skill` 拉取，平时不占上下文。

### 界面里怎么用

- **GUI**：顶栏多了一个「**技能**」按钮，点它把当前技能列进聊天区；要立刻预载某技能，
  输入 `/skill <名>`（如 `/skill galaxy-ui`）。启动时若检测到技能会提示。
- **CLI**：`/skills` 列出全部技能；`/skill <名>` 把某技能说明书预载进当前会话。
- 更常见的是**不需要你动手**：模型发现任务命中某个技能（如“做个好看的登录卡片”命中
  galaxy-ui）时，会自己在工具循环里调用 `use_skill` 拉取说明并照做。

自动注册的技能工具（模型可用，宿主只读）：

| 工具 | 作用 |
|---|---|
| `list_skills` | 列出已安装技能与用途 |
| `use_skill(name)` | 把某技能完整说明书（正文+参考）拉进上下文，随后按它行事 |
| `library_list_<技能>` | 只读列出该技能素材库目录（如 `library_list_galaxy_ui`） |
| `library_read_<技能>` | 只读读取该技能素材库里的文件（不会写库、不碰工作目录） |
| `preview_html(path)` | 用系统默认浏览器打开**工作目录内**的本地 HTML 预览 |

### galaxy-ui：用 3800+ Galaxy 组件做网页 UI（开箱自带）

`skills/galaxy-ui` 把仓库里的 `galaxy-main`（Uiverse.io 组件库，MIT）封装成了一个技能。
要做按钮/卡片/表单/输入框/开关/加载动画等“好看的界面”时，模型会：

1. 先看 `CATALOG.md`（自动生成的分类索引：11 类、纯 CSS / Tailwind 比例、示例文件），
   用 `library_list_galaxy_ui` / `library_read_galaxy_ui` 翻素材、读原文件；
2. 挑贴合风格的组件，按 `references/ADAPT.md` 的规则**改造合并**成**单文件自包含 HTML**；
3. 用 `write_file` 写进你选定的工作目录（写入需你确认），再调 `preview_html` 在浏览器打开预览，
   按你反馈迭代。

> 提醒：galaxy 素材库是**只读**的（`library_*` 只读工具），生成的文件落在**工作目录**。
> 做 UI 前最好先选好一个输出目录（CLI `/folder D:\...` / GUI 点“文件夹”）。

```python
# 库用法：把技能系统挂到任意 Agent（离线无副作用）
from harness import Agent, HarnessConfig, activate_skills, discover
skills = discover()                        # 扫描 skills/
agent = Agent(HarnessConfig(api_key="sk-dummy"), tools=[])
activate_skills(agent)                     # 注册技能工具 + 注入技能清单
# agent 现在带 list_skills / use_skill / library_*（preview_html 需再传 get_workspace/open_html 回调）
```

### 新增一个自己的技能

照 `skills/_TEMPLATE/` 复制一份到 `skills/<你的名字>/SKILL.md` 填好即可（下划线开头的目录会被
自动跳过，用来放模板很安全）。`SKILL.md` 前端支持：

```yaml
---
name: my-skill              # 技能名（目录名或 name）
description: 一句用途简介      # 必填，会进技能清单
when_to_use: 何时该用它        # 可选：命中它的需求信号
library: ../../my-assets     # 可选：只读素材库，相对本 SKILL.md；声明后自动生成
                             #   library_list_my_skill / library_read_my_skill 两个只读工具
version: 1                   # 可选
---
正文：教模型“什么时候用、怎么一步步做、验收标准”，细节可放 references/*.md。
```

保存后下次会话即自动出现。重扫/刷新 galaxy 的 `CATALOG.md`：
`.venv\Scripts\python scripts\build_galaxy_catalog.py`（幂等，可反复跑）。

## 厂商配置对照

编辑 `.env`（模板见 `.env.example`）。`base_url` 需保留各厂商要求的路径前缀（`/v1`、`/v4`、`compatible-mode/v1`），不能统一省略：

| 厂商 | LLM_BASE_URL | LLM_MODEL 示例 | 备注 |
|---|---|---|---|
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` | |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` | `deepseek-reasoner` 亦可 |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` / `qwen-max` | 必须是兼容模式路径 |
| Kimi (Moonshot) | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` | |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` | 注意是 `/v4` |
| 本地 Ollama | `http://localhost:11434/v1` | `qwen2.5:7b` | api_key 填任意占位如 `ollama` |

## 写自己的工具

任何「带类型注解 + docstring」的普通函数都能变成工具：

```python
from harness import tool

@tool   # 也支持 @tool(name="别名", description="覆盖描述")
def fetch_price(symbol: str, market: str = "CN") -> str:
    """查询某只股票的当前价格。

    Args:
        symbol (str): 股票代码，如 "600519" 或 "AAPL"。
        market (str): 市场，CN 或 US，默认 CN。
    """
    return f"{symbol}@{market} 当前价格 168.50"  # 换成真实数据源即可
```

约定：
- **必填**：有类型注解、无默认值的参数 → schema `required`；有默认值 → 可选。
- **描述**：docstring 第一段是工具描述；`Args:` 段每行 `参数名 (类型): 说明` 会挂到对应字段（Google 风格子集）。
- 返回值 `str / dict / list / pydantic 模型 / None` 都会被序列化，超过 4000 字符自动截断；抛异常会被捕获并以 `[错误] …` 文本回传模型。

在库代码里使用：

```python
from harness import Agent, get_builtins

agent = Agent(tools=[*get_builtins(), fetch_price])   # 不传 tools 则默认内置工具
agent.reset()                                          # 清空本轮历史
answer = agent.run("茅台今天多少钱？")                   # 完整跑完工具循环，返回最终文本
```

> 注意：工具在**独立线程带超时执行**（默认 30s），函数体内不要依赖线程局部状态；入参是普通值，返回要可 JSON 序列化。

## 配置项（环境变量）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `LLM_BASE_URL` | `https://api.openai.com/v1` | 任意 OpenAI 兼容端点 |
| `LLM_API_KEY` | 空 | 密钥（Ollama 填任意占位） |
| `LLM_MODEL` | `gpt-4o-mini` | 模型名 |
| `LLM_TEMPERATURE` | 不传 | 采样温度，省略则交给服务端默认 |
| `AGENT_MAX_ITERATIONS` | `40` | 单轮最多迭代步数（防死循环闸门）；论文解析/综述等长任务建议 `60~200` |
| `COLLAB_ENABLED` | `0` | 是否启用「主答+审查」多模型协作 |
| `COLLAB_CRITIC_MODEL` | 该厂商 light | 协作里审查角色的模型（默认取厂商预设的轻量模型） |
| `CONTEXT_COMPRESS` | `1` | 上下文压缩总开关（0=关闭，回到"从不压缩"） |
| `CONTEXT_COMPRESS_MID` | `1` | 单轮任务中途超预算时也自动折叠更早的已消费工具回合 |
| `CONTEXT_BUDGET_TOKENS` | `0`=自动 | 历史估算 token 超此值即折叠旧轮；0=按模型窗口约 55% 自动 |
| `CONTEXT_MODE` | `gist` | 小结生成方式（现实现 gist：免费确定性折叠） |
| `CONTEXT_RECALL` | `1` | 折叠后注册 `recall_history` 取回工具（可检索折叠前原文） |
| `CONTEXT_KEEP_RECENT` | `1` | 保留最近几个"已完成"轮次的全文不折叠 |
| `SYSTEM_PROMPT` | 中文工具型人设 | 系统提示词 |
| `TOOL_TIMEOUT` | `30` | 单个工具执行超时（秒） |
| `LLM_REQUEST_TIMEOUT` | `90` | 单次请求超时（秒） |

## 设计说明与当前边界

- 核心循环走**非流式**请求，跨兼容厂商最稳（流式是明确的后续增强点，见下）。
- 对话历史保存在 `Agent.messages`，连续 `run()` 即多轮对话；`/clear` 或 `agent.reset()` 重开。
- 工具出错（参数校验失败 / 运行时异常 / 超时 / 未知工具名）不会中断循环，而是作为 `tool` 消息回传，模型可据此自我纠正。

已知边界（欢迎在此基础上扩展）：
- 尚未实现：流式输出、并行工具回调展示、`async` 版 `Agent`、多 agent 协作。
- 多轮会话的旧轮压缩已内置（见「上下文压缩」一节），默认开启；要观察/关闭可调 `CONTEXT_*`。
- 个别「兼容」端点的推理类模型（如部分 reasoner）返回结构特殊，可能需在 `client.py` 里微调。

## 科研小助手 · HTML 内核桌面版（assistant_web.py，推荐）

窗口用 Edge WebView2 渲染 `assets/assistant_ui.html`（galaxy-ui 组件库改编的整窗皮肤），
Python 侧复用本 harness，是一个**真能干活**的桌面研究助理：

- 干活：选工作区后获得 `list_files / read_file / write_file`（写入前会弹窗让你确认）；
- 不同模型：右上 ⚙ 设置选厂商（DeepSeek/通义/Kimi/GLM/Ollama…）粘贴 key，或填任意 OpenAI 兼容端点；
- 技能：`research-assistant` + `galaxy-ui` 自动启用，右上 🧩 载入说明书；
- 上下文压缩：Agent 内建（多轮 + 单轮中途自动压缩）；
- 多模型协作：主答 + 审查改进（设置里勾选开启）。

```bash
.venv\Scripts\pip install pywebview pypdf     # 首次（用 Edge WebView2，Win11 自带运行库）
.venv\Scripts\pythonw assistant_web.py        # 或双击 run-assistant.bat
```
纯浏览器打开 `assets/assistant_ui.html` 可预览界面（演示模式，不连模型）。

## 许可证

MIT，详见 [LICENSE](LICENSE)。`galaxy-main/`（Uiverse.io 组件库）及其组件同为 MIT，随仓库携带。
