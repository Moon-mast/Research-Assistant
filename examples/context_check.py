"""离线自检：上下文压缩（省 token）的纯逻辑与 Agent 集成。不联网、不需要 API key。

覆盖：
  1. token 估算 / 模型窗口自动预算
  2. 轮次切分 + keep_recent（纯函数）
  3. Agent 折叠正确性：原文进存档、保留最近轮、会话回顾、角色序合法、token 下降
  4. 超预算但无更早可折叠轮 → 不动
  5. 悬空工具尾愈合
  6. recall_history 取回 / 空档提示
  7. 工具结果结构化瘦身
  8. reset() 清档并摘除 recall_history
  9. HarnessConfig.from_env() 解析 CONTEXT_*
 10. Collaboration 主答注册 recall、审查(critic)不受影响

运行：.venv\\Scripts\\python examples\\context_check.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 项目根目录

from harness import Collaboration, get_builtins  # noqa: E402
from harness import context as ctx  # noqa: E402
from harness.agent import Agent  # noqa: E402
from harness.config import HarnessConfig  # noqa: E402
from harness.tools import _format_result  # noqa: E402


# ----------------------------------------------------------------------
# 构造消息的小助手
# ----------------------------------------------------------------------
def S(text="系统人设"):  # noqa: N802
    return {"role": "system", "content": text}


def U(text):  # noqa: N802
    return {"role": "user", "content": text}


def A(text):  # noqa: N802
    return {"role": "assistant", "content": text}


def TC(cid, name="calculator", args='{"expression": "1+1"}', text="", content=None):  # noqa: N802
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [{"id": cid, "type": "function", "function": {"name": name, "arguments": args}}],
    }


def T(cid, text):  # noqa: N802
    return {"role": "tool", "tool_call_id": cid, "content": text}


def call(cid, args='{"expression": "1+1"}'):
    return {"id": cid, "type": "function",
            "function": {"name": "calculator", "arguments": args}}


def _roles_valid(messages: list) -> bool:
    """校验 chat 工具协议：每个 tool 消息必须紧跟着它所引用的 assistant.tool_calls，
    且同一轮的工具结果个数与工具调用个数一致，中间不能被 user 打断。"""
    n = len(messages)
    i = 0
    while i < n:
        m = messages[i]
        if m["role"] == "tool":
            return False
        if m["role"] == "assistant" and m.get("tool_calls"):
            ids = {tc["id"] for tc in m["tool_calls"]}
            j = i + 1
            count = 0
            while j < n and messages[j]["role"] == "tool":
                if messages[j]["tool_call_id"] not in ids:
                    return False
                count += 1
                j += 1
            if count != len(ids):
                return False
            i = j
            continue
        i += 1
    return True


def _all_text(messages) -> str:
    parts = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            parts.append(c)
        if m.get("tool_calls"):
            for tc in m["tool_calls"]:
                parts.append(json.dumps(tc.get("function"), ensure_ascii=False))
    return "\n".join(parts)


def _cfg(**kw):
    base = dict(api_key="sk-dummy")
    base.update(kw)
    return HarnessConfig(**base)


def check_estimators() -> None:
    print("== 1. 估算器 / 预算 ==")
    assert ctx.estimate_text_tokens("a" * 400) >= 90, "ASCII 粗估 ≈ len/4"
    assert ctx.estimate_text_tokens("a" * 400) <= 200
    assert ctx.estimate_text_tokens("汉" * 400) > ctx.estimate_text_tokens("a" * 400), "CJK 应比 ASCII 更重"
    assert ctx.estimate_text_tokens("x" * 10) <= ctx.estimate_text_tokens("x" * 100), "单调递增"
    assert ctx.estimate_message_tokens({"role": "user", "content": "hi"}) >= 5, "含结构开销"
    calls = {"role": "assistant", "content": None,
             "tool_calls": [{"id": "c", "type": "function",
                             "function": {"name": "calculator", "arguments": '{"expression":"1+1"}'}}]}
    assert ctx.estimate_message_tokens(calls) > ctx.estimate_text_tokens("calculator"), "arguments 计入"
    assert ctx.estimate_tool_schema_tokens(get_builtins()) > 0, "工具 schema 计入"

    b8 = ctx.default_budget_tokens("moonshot-v1-8k")
    assert 0 < b8 < 8000, f"8k 模型预算应 < 8000，实际 {b8}"
    b128 = ctx.default_budget_tokens("moonshot-v1-128k")
    assert b128 > 30000, f"128k 模型预算应较大，实际 {b128}"
    b_unknown = ctx.default_budget_tokens("some-unknown-model")
    assert 10000 <= b_unknown <= 30000, f"未知模型预算应落保守区间，实际 {b_unknown}"
    print("  ✔ 估算器/预算基本合理")


def check_pure_split() -> None:
    print("\n== 2. 轮次切分 + keep_recent（纯函数）==")
    msgs = [S(), U("q1"), A("a1"), U("q2"), A("a2"), U("q3"), A("a3")]
    fold = ctx.select_fold_turns(msgs, keep_recent=1)
    assert len(fold) == 2, f"应折叠 2 个旧轮，实际 {len(fold)}"
    assert fold[0].start == 1 and fold[1].start == 3, "折叠起点应指向旧轮的 user"
    assert fold[0].end == 3 and fold[1].end == 5, "折叠终点应指向下一个 user"
    assert all(t.completed for t in fold)
    fold2 = ctx.select_fold_turns(msgs, keep_recent=2)
    assert len(fold2) == 1 and fold2[0].start == 1, "keep_recent=2 只折叠最旧的 1 轮"
    gist = ctx.turn_to_gist(fold[0])
    assert "q1" in gist and "a1" in gist, f"gist 应含用户问题与结论：{gist}"
    print("  ✔ 切分/保留/折叠与 gist 正确")


def check_agent_compact() -> None:
    print("\n== 3. Agent 折叠正确性 ==")
    agent = Agent(_cfg(context_compress=True, context_budget_tokens=10))
    big = "B" * 1500  # 模拟一轮重型工具输出：折叠才有明显收益
    m1 = "MARKER_FIRST_AAA"
    m2 = "MARKER_SECOND_BBB"
    msgs = [
        S(),
        U("任务一：请用计算器算一下，并给出过程"),
        TC("c1", args='{"expression": "1+1"}'),
        T("c1", f"{big} {m1} 结果是 42"),
        A("第一轮结论：是 42。"),
        U("任务二：再算一次"),
        TC("c2", args='{"expression": "2+2"}'),
        T("c2", f"{big} {m2} 结果是 7"),
        A("第二轮结论：是 7。"),
    ]
    assert _roles_valid(msgs), "预制消息本身应合法"
    agent.messages = list(msgs)
    before = ctx.estimate_messages_tokens(msgs)
    agent._maybe_compact_before_turn()
    out = agent.messages
    st = agent.context_stats

    assert st.get("triggered") is True, "超预算且折叠有省 → 应触发"
    assert st["saved"] > 0, f"折叠后应省 token：{st}"
    assert len(agent._archive) == 1, "第一轮原文应进存档"
    archive1 = json.dumps(agent._archive, ensure_ascii=False)
    assert m1 in archive1 and "是 42" in archive1, "存档应保留首轮完整原文"
    assert m1 not in _all_text(out), "折叠后首轮工具原文不应留在 live 消息"
    assert m2 in _all_text(out), "最近一轮(含其工具输出)应全文保留"
    assert out[0]["content"] == "系统人设", "system 主消息保持在首位"
    assert out[1]["role"] == "system" and ctx.is_digest_message(out[1]), "第二位应为会话回顾"
    assert _roles_valid(out), f"折叠后角色序应合法：{[m['role'] for m in out]}"
    after = ctx.estimate_messages_tokens(out)
    assert after < before - 200, f"折叠应明显下降：{after} vs {before}"
    print(f"  ✔ 折叠正确：省 ≈{st['saved']} token，首轮入档、次轮保留、协议合法")

    # —— 连续第二轮压缩：次轮带大输出，变旧后同样被折进回顾 ——
    agent.messages = out + [U("第三轮问题"), A("第三轮结论。")]
    agent._maybe_compact_before_turn()
    assert agent.context_stats.get("triggered") is True, agent.context_stats
    assert m2 not in _all_text(agent.messages), "第二轮大输出应移出 live 消息"
    archive2 = json.dumps(agent._archive, ensure_ascii=False)
    assert m2 in archive2, "第二轮原文应进存档"
    assert len(agent._archive) == 2
    assert _roles_valid(agent.messages)
    assert sum(1 for m in agent.messages if ctx.is_digest_message(m)) == 1, "会话回顾应只有一条"
    print("  ✔ 跨轮连续压缩正常（回顾随轮次增长、仍只有一条）")

    # —— 折叠不省 token 时不应用（避免短小轮次越压越大） ——
    agent2 = Agent(_cfg(context_compress=True, context_budget_tokens=10))
    tiny = [S(), U("q1"), A("a1"), U("q2"), A("a2")]
    agent2.messages = list(tiny)
    agent2._maybe_compact_before_turn()
    assert agent2.context_stats.get("triggered") is False, "短轮折叠反而不省 → 应跳过"
    assert agent2.messages == tiny and agent2._archive == [], "消息与存档都不应变"
    print("  ✔ 不省 token 的折叠被跳过（不会越压越大）")


def check_no_fold() -> None:
    print("\n== 4. 超预算但无更早可折叠轮 → 不动 ==")
    agent = Agent(_cfg(context_compress=True, context_budget_tokens=10))
    msgs = [S(), U("唯一一轮问题"), A("唯一一轮结论。")]
    agent.messages = list(msgs)
    agent._maybe_compact_before_turn()
    st = agent.context_stats
    assert st.get("triggered") is False, "只有一轮已完成时不应折叠"
    assert agent.messages == msgs, "消息应原样保留"
    print("  ✔ 无可折叠旧轮时不改动历史")


def check_heal() -> None:
    print("\n== 5. 悬空工具尾愈合 ==")
    agent = Agent(_cfg())
    agent.messages = [S(), U("q1"), A("a1"), U("q2"), TC("c2"), T("c2", "x")]
    agent._heal_dangling_tail()
    roles = [m["role"] for m in agent.messages]
    assert roles == ["system", "user", "assistant", "user"], f"应保留 q2 并清空悬空块：{roles}"
    assert agent.messages[-1]["content"] == "q2"
    print("  ✔ 悬空 tool_calls/tool 被摘除、最近的 user 保留")

    # 变体：悬空块后面还夹着一条被注入的技能 system —— 应保留该 system
    agent2 = Agent(_cfg())
    agent2.messages = [S(), U("q1"), A("a1"), U("q2"), TC("c2"), T("c2", "x"), S("sticky-skill-mid")]
    agent2._heal_dangling_tail()
    roles2 = [m["role"] for m in agent2.messages]
    assert roles2 == ["system", "user", "assistant", "user", "system"], f"技能片段应保留：{roles2}"
    print("  ✔ 悬空块后的技能 system 片段被保留")


def check_recall() -> None:
    print("\n== 6. recall_history ==")
    agent = Agent(_cfg(context_compress=True, context_budget_tokens=10))
    agent._recall_tool = None
    # 空档
    assert "暂无归档" in agent._recall_history("anything")
    # 折叠后注册
    msgs = [
        S(),
        U("请搜索星球 alpha-beta 的半径"),
        TC("c1", name="read_file", args='{"path": "planet.md"}'),
        T("c1", "alpha-beta 半径约为 998877 千米\n" + "x" * 1400),
        A("结论：半径约 998877 千米。"),
        U("再来一个问题"),
        A("好的。"),
    ]
    agent.messages = list(msgs)
    agent._maybe_compact_before_turn()
    names = [t.name for t in agent.tools]
    assert "recall_history" in names, f"折叠后应注册 recall_history：{names}"
    resp = agent._recall_history("998877")
    assert "998877" in resp and "alpha-beta" in resp, f"应能取回原文片段：{resp[:120]}"
    print(f"  ✔ recall 注册并可取回：{resp.splitlines()[0][:60]}…")


def check_mid_turn() -> None:
    print("\n== 6b. 单轮中途自动压缩 ==")
    big = "M" * 1200
    mk0, mk1, mk2 = "MID_MARKER_0", "MID_MARKER_1", "MID_MARKER_2"
    msgs = [
        S(),
        U("一个大任务：逐步读取并处理三个文件"),
        TC("a", args='{"path": "1"}'), T("a", big + " " + mk0),   # 回合0（已完成，其后有 assistant）
        TC("b", args='{"path": "2"}'), T("b", big + " " + mk1),   # 回合1（已完成）
        TC("c", args='{"path": "3"}'), T("c", big + " " + mk2),   # 回合2（进行中：末尾无 assistant 回复）
    ]
    assert _roles_valid(msgs), "预制消息本身应合法"

    agent = Agent(_cfg(context_compress=True, context_compress_mid=True, context_budget_tokens=10))
    agent.messages = list(msgs)
    before = ctx.estimate_messages_tokens(msgs)
    agent._maybe_compact_mid_turn()
    st = agent.context_stats
    out = agent.messages
    assert st.get("triggered") is True and st.get("kind") == "mid-turn", st
    assert st["saved"] > 0, st
    assert mk0 not in _all_text(out), "最早的已完成回合应被折叠出 live 历史"
    assert mk1 in _all_text(out) and mk2 in _all_text(out), "最近已完成回合 + 进行中回合应保留"
    assert _roles_valid(out), f"中途折叠后协议应合法：{[m['role'] for m in out]}"
    archive = json.dumps(agent._archive, ensure_ascii=False)
    assert mk0 in archive, "被折回合原文应进存档"
    assert "recall_history" in [t.name for t in agent.tools], "中途折叠后也应注册 recall"
    print(f"  ✔ 中途折叠正确：省 ≈{st['saved']} token，进行中/最近回合保留、协议合法")

    # 关闭单轮压缩开关 → 不折叠
    agent2 = Agent(_cfg(context_compress=True, context_compress_mid=False, context_budget_tokens=10))
    agent2.messages = list(msgs)
    agent2._maybe_compact_mid_turn()
    assert agent2.messages == msgs and agent2._archive == [], "CONTEXT_COMPRESS_MID=0 时不应中途折叠"
    print("  ✔ CONTEXT_COMPRESS_MID=0 关闭单轮中途压缩")


def check_slim() -> None:
    print("\n== 7. 工具结果结构化瘦身 ==")
    rows = [{"name": f"row_{i}", "value": i * 10, "note": "x" * 20} for i in range(500)]
    out = _format_result(rows)
    assert len(out) <= 4000, f"结构化预览应 ≤ 4000，实际 {len(out)}"
    assert "字段：" in out, "应给出字段名"
    assert "共 500 项" in out, f"应给出总行数：{out[-200:]}"
    assert '"row_0"' in out, "首行数据应逐字保留（不捏造）"
    # 超大字符串/字典仍安全截断：长值给"长度/项数"占位而非逐字全文
    out2 = _format_result({"long": "x" * 9000, "list": [i for i in range(300)]})
    assert len(out2) <= 4000
    assert "字符串共 9000 字符" in out2 and "[list 共 300 项]" in out2, out2[:200]
    print("  ✔ 结构化预览 + 截断兜底正常")


def check_reset() -> None:
    print("\n== 8. reset() 清档并摘除 recall ==")
    agent = Agent(_cfg(context_compress=True, context_budget_tokens=10))
    msgs = [S(), U("q1"), TC("c1"), T("c1", "B" * 900 + " mm_x"), A("a1"), U("q2"), A("a2")]
    agent.messages = list(msgs)
    agent._maybe_compact_before_turn()
    assert "recall_history" in [t.name for t in agent.tools]
    agent.reset()
    assert agent.messages == [] and agent._archive == [] and agent.context_stats == {}
    assert "recall_history" not in [t.name for t in agent.tools]
    print("  ✔ reset 清空消息/存档/统计并移除 recall 工具")


def check_env() -> None:
    print("\n== 9. HarnessConfig.from_env 解析 CONTEXT_* ==")
    saved = {k: os.environ.get(k) for k in (
        "CONTEXT_COMPRESS", "CONTEXT_BUDGET_TOKENS", "CONTEXT_MODE",
        "CONTEXT_RECALL", "CONTEXT_KEEP_RECENT", "CONTEXT_COMPRESS_MID")}
    try:
        os.environ["CONTEXT_COMPRESS"] = "1"
        os.environ["CONTEXT_BUDGET_TOKENS"] = "9000"
        os.environ["CONTEXT_MODE"] = "weird"  # 非法 → 回退 gist
        os.environ["CONTEXT_RECALL"] = "0"
        os.environ["CONTEXT_KEEP_RECENT"] = "3"
        os.environ["CONTEXT_COMPRESS_MID"] = "0"
        cfg = HarnessConfig.from_env()
        assert cfg.context_compress is True
        assert cfg.context_budget_tokens == 9000
        assert cfg.context_mode == "gist", "非法 mode 应回退 gist"
        assert cfg.context_recall is False
        assert cfg.context_keep_recent == 3
        assert cfg.context_compress_mid is False
        # 未设置时默认
        for k in saved:
            os.environ.pop(k, None)
        cfg2 = HarnessConfig.from_env()
        assert cfg2.context_compress is True and cfg2.context_recall is True
        assert cfg2.context_mode == "gist" and cfg2.context_budget_tokens == 0
        assert cfg2.context_keep_recent == 1 and cfg2.context_compress_mid is True
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print("  ✔ CONTEXT_* 环境变量解析与默认值正确")


def check_collab() -> None:
    print("\n== 10. Collaboration：主答注册 recall，审查不受影响 ==")
    colab = Collaboration(_cfg(context_compress=True, context_budget_tokens=10))
    critic_before = [t.name for t in colab.critic.tools]
    colab.main.messages = [
        S(), U("q1"), TC("c1"), T("c1", "B" * 900 + " mmm_collab"), A("a1"), U("q2"), A("a2"),
    ]
    colab.main._maybe_compact_before_turn()
    assert "recall_history" in [t.name for t in colab.main.tools], "主答应注册 recall"
    assert [t.name for t in colab.critic.tools] == critic_before, "审查模型不应被加工具"
    print("  ✔ 主答注册 recall、审查保持无工具")


def main() -> None:
    check_estimators()
    check_pure_split()
    check_agent_compact()
    check_no_fold()
    check_heal()
    check_recall()
    check_mid_turn()
    check_slim()
    check_reset()
    check_env()
    check_collab()
    print("\n✔ 全部上下文压缩离线自检通过。")


if __name__ == "__main__":
    main()
