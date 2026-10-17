# ADAPT —— 把 Galaxy 单组件改造成整页的细则

用 `library_read_galaxy_ui` 打开组件后，把它搬进最终单文件页面时的实操规则。

## 1. 读懂一个典型组件

Galaxy 组件有三种常见形态，改造前先分清楚：

- **纯 CSS 型**：顶部 `<style>` 定义类，下面跟 HTML 碎片。
  例（Buttons 里很多）：
  ```html
  <style>.btn { ... } .btn:hover { ... }</style>
  <button class="btn">Click me</button>
  ```
  → 直接把 `<style>` 里的规则抄进页面 `<head>` 的 `<style>`，HTML 保持语义结构即可。
- **Tailwind 型**：没有（或只有少量）`<style>`，元素 `class` 是一串工具类
  （`bg-blue-500 hover:bg-blue-600 rounded-lg px-4 ...`）。
  → 离线/交付态：把用到的工具类**逐一翻译成等价的纯 CSS**（约十来个规则时最省事）；
    原型/演示且用户同意联网：页面引一次 `https://cdn.tailwindcss.com` 原样保留工具类。
    不要两种方案混着来。
- **混合/其它**：偶有 `<script>`（极少）或依赖背景图案/图片。取 HTML 骨架 + 你要的样式部分即可，
  脚本逻辑只保留必要交互并检查依赖。

## 2. 去掉“展示用外壳”

组件按 Uiverse 的画布设计的，常自带：`html,body{height:100%}`、`min-h-screen`、flex 居中、
`h-full`、占满屏的外层 `<div>`。搬进真实页面时删除这些约束，让组件自然躺在你的容器里；
外层若要保留（比如“整屏居中演示”），改成页面自己的布局，而不是组件自带。

## 3. 防止多组件串味（命名空间）

合并 2+ 组件进同一页时，它们的类名很容易撞（`.card`、`.btn`、`.input`、`.box` 遍地都是）。
两种可靠做法任选其一：

- **做法 A（推荐，改动小）**：给每个组件的 CSS 加作用域——外层套一个独特 wrapper 类，
  并把该组所有选择器改成后代写法，例如原 `.card{...}` → 假设 wrapper 叫 `.gx-login`,
  改成 `.gx-login .card{...}`（子组件内部同名的类也就此隔离）。
  改选择器时留意 `.card:hover`、`.card::before` 等都要一并加前缀。
- **做法 B**：直接给类改名成唯一前缀（`.gx1-btn`, `.gx2-btn`），HTML 与 CSS 同步改。

同时：尽量不用 ID 选择器；`*` 选择器、`@media` 全局声明尽量收窄到作用域内；避免重复引入字体。

## 4. 资源与外链处理

- 外部字体 `@import url(...)` / `<link href="https://fonts...">`：默认移除，改用系统字体栈
  （`font-family: system-ui, -apple-system, "Segoe UI", Roboto, ...`）；用户明确允许联网再用外链。
- 外部图片/背景：默认换成纯色/渐变或内联 SVG；确需图片问用户要路径。
- `@media (prefers-reduced-motion: reduce)`：若动画组件大量 motion，可顺手加一行减少动画的兜底。

## 5. 可访问性与健壮性（改造即检查）

- 该是 `<button>` 的别用 `<div>`；`<input>` 配 `<label>`（或 `aria-label`/`placeholder` 仅辅助）；
  纯图标按钮加 `aria-label="说明"`。
- 保留 `:hover`/`:focus`/`:focus-visible` 反馈，文字/背景对比度达标，字号别小于 ~12px。
- 把交互逻辑（hover/active/check 切换等）想清楚，能演示的都要能演示。

## 6. 交付检查清单（收尾前过一遍）

- [ ] 单文件 `.html`，双击即开；无外链也能基本可用（联网版明确告知用户即可）。
- [ ] `<style>` 全部在 `<head>`，无 `body{height:100%}` 之类的组件残留。
- [ ] 多组件已各自作用域隔离，改一处样式不影响其它。
- [ ] 语义/可访问：真按钮、真输入、有标签、有聚焦态。
- [ ] 文案/配色/尺寸贴合用户需求，不是生搬。
- [ ] `<head>` 或文件头留了来源注释（MIT 鼓励署名）。
