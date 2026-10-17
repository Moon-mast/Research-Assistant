---
name: galaxy-ui
description: 从本地 Galaxy(Uiverse.io) 3800+ 组件库里挑组件并改造，产出漂亮的单文件 HTML 页面（按钮/卡片/表单/输入框/开关/加载动画等），并在浏览器预览。
when_to_use: 用户想要好看的网页 UI、按钮、卡片、表单、输入框、复选框、开关、加载动画、提示等，或提到 galaxy / uiverse / “做个好看的界面/组件”时。
library: ../../galaxy-main
version: 1
---

# galaxy-ui —— 用本地 Galaxy 组件库做网页 UI

`galaxy-main` 是本机自带的一套 Uiverse.io 组件库（共 3802 个自包含 HTML 组件，MIT 协议）。
本技能教你在要“漂亮的 UI/页面”时：**先翻素材库挑合口味的组件 → 读原文件确认可用 → 改造合并 →
落成一个自包含的单文件 `.html` → 交给宿主在浏览器打开预览**。

## 一、素材库怎么翻（只读，不影响工作目录）

素材库根目录为 `galaxy-main`，用下面两个只读工具浏览（路径都相对库根）：
- `library_list_galaxy_ui(".")`：列目录 → 看到 11 个分类（Buttons/Cards/Checkboxes/Forms/
  Inputs/Notifications/Patterns/Radio-buttons/Toggle-switches/Tooltips/loaders）；
  再 `library_list_galaxy_ui("Buttons")` 即可列全该类 1200+ 个文件。
- `library_read_galaxy_ui("<类别/文件名.html>")`：读某个组件源码，如 `Buttons/xxx.html`。
- `references/CATALOG.md`（已随本说明附上）：每类的**数量 + 纯CSS/Tailwind 比例 + 抽样示例**，
  用来在动手读原文件前快速缩小候选范围。目录里另附 `ADAPT.md`（搬运/合并细则，必要时再细读）。

组件文件规律：
- 命名 `<作者>_<形容词>-<动物>-<序号>.html`，作者常是 Uiverse 用户名。
- 每个文件是**自包含**的一小段 HTML（+ 内联 `<style>`/少量脚本），顶部常有
  `<!-- From Uiverse.io by 作者 - Tags: ... -->` 注释（作者与风格关键词）。
- 风格分三类（CATALOG 里已标注）：
  - `css`：自带 `<style>` 的纯 CSS —— 最易整体搬进页面；
  - `tailwind`：整文件都是 Tailwind 工具类（`class="bg-... flex ..."`）—— 离线就得改写成纯 CSS，
    允许联网时也可引一次 CDN；
  - `other`：要打开确认（外链/内联工具类等）。
- 组件多是“碎片”，可能自带 `h-full`/`min-h-screen`/flex 居中这类**展示用外层**，搬进页面要去掉。

## 二、什么时候用它

用户要“漂亮/好看的/炫酷的 UI、按钮、卡片、登录表单、注册页、仪表盘、加载动画、页面骨架”等，
且你手上有这个技能时 —— 别凭记忆硬写样式，先翻库找贴近的风格再改，又快又有质感。

## 三、标准工作流

1. **澄清关键需求（确有必要才 ask_user，一个问题带选项即可）**：要什么（如“登录表单卡片”）？
   偏好风格/配色？产出是一张演示页还是多张？是否允许联网（影响 Tailwind CDN / 外链字体）？
   写入目录若无工作目录先请用户选（CLI `/folder`、GUI 点“文件夹”）。
2. **看 CATALOG 圈定候选**：按分类先看比例与示例，锁定 3~6 个候选文件名。
3. **读原文件**：`library_read_galaxy_ui` 逐个打开候选，确认风格/结构/是否纯 CSS/是否好改；
   选 1~2 个最贴合的作底子（可跨分类组合，如 Cards 的卡片 + Inputs 的输入框 + Buttons 的按钮）。
4. **合成单文件**：产出**一个自包含的 `.html`**——`<style>` 汇总合并、去掉展示用外层与
   无关注释、替换/内联外部资源；若选了 Tailwind 组件：离线→把用到的工具类改写成纯 CSS；
   联网→页面里引一次 `https://cdn.tailwindcss.com`（只作原型可接受，交付前仍建议纯 CSS）。
   所有 `<style>` 放进 `<head>`，必要时给选择器加作用域前缀避免串味（见 ADAPT.md）。
5. **落盘并预览**：用 `write_file` 把完整 HTML 写入工作目录（文件名如 `login-card.html`），
   随后调用 `preview_html` 让宿主用系统默认浏览器打开；用户反馈后迭代（改组件/配色/布局）。
6. **收尾**：成品可在浏览器直接打开、无外链也能基本可用、关键交互能演示。可在 `<head>` 注释里
   写一句来源：`<!-- 组件来自 Uiverse.io by <作者>（MIT），经 galaxy-ui 技能改编 -->`。

## 四、质量与合规（重要）

- **语义化/可访问**：按钮用 `<button>`、输入用 `<input>`/`<label>`、图标按钮带 `aria-label`，
  保留 `:hover/:focus` 与 `focus-visible`，文字对比度够。
- **别照抄**：组件通常要贴合用户给的主题/文案/配色/尺寸；只把“样式骨架 + 交互思路”带过去。
- **自包含优先**：默认不要外链字体、图标库、CDN；图标若无内联 SVG 又必须显示，可内联或用系统字符。
  确需联网资源时先征得用户同意。
- **尊重许可**：Uiverse 组件为 MIT，鼓励（非强制）保留作者署名注释。
- **一个页面一套命名空间**：合并多组件时按 ADAPT.md 的做法给每个组件 CSS 加前缀/作用域，防互相覆盖。

## 五、素材库更新

若 `galaxy-main` 内容有变、想刷新本目录文件，运行
`scripts/build_galaxy_catalog.py`（会重写 `references/CATALOG.md`），然后重建会话。
