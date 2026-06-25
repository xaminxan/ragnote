"""全局审查节点"""
import json
import re
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

from ..state import BookState
from ..prompts.knowledge import SYS_GLOBAL_CHECK
from ..utils import safe_llm_call as _safe_llm_call


_llm = None


def get_llm(model_override: str = None, base_url_override: str = None, api_key_override: str = None):
    """获取LLM实例，支持客户端覆盖"""
    from ..config import get_llm_config
    cfg = get_llm_config(model_override, base_url_override, api_key_override)
    return ChatOpenAI(
        model=cfg.model,
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        temperature=cfg.temperature,
        timeout=cfg.timeout,
    )


def _extract_json_array(text: str):
    """从 LLM 输出中提取 JSON 数组。"""
    # 先清理 markdown 代码块包裹
    cleaned = text.strip()
    code_block = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?\s*```', cleaned)
    if code_block:
        cleaned = code_block.group(1).strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    # 用贪婪匹配找最外层的 [ ]
    depth = 0
    start = None
    for i, ch in enumerate(cleaned):
        if ch == '[':
            if depth == 0:
                start = i
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0 and start is not None:
                candidate = cleaned[start:i + 1]
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, list):
                        return parsed
                except json.JSONDecodeError:
                    pass
                start = None
    return []


def global_consistency_check(state: BookState) -> dict:
    """全局一致性检查"""
    knowledge = state["book_knowledge"]
    notes = state["chunk_notes"]

    if not notes:
        return {"consistency_issues": [], "phase": "merge"}

    # 构建全书笔记预览
    all_notes_preview = "\n\n---\n\n".join(
        f"第{k+1}段: {v[:500]}" for k, v in sorted(notes.items())
    )[:8000]

    core_concepts = json.dumps(
        list(knowledge["key_concepts"].keys())[:20],
        ensure_ascii=False,
    )

    print("\n▶ 全局一致性审查...")
    llm = get_llm(state.get("llm_model"), state.get("llm_base_url"), state.get("llm_api_key"))

    check_prompt = SYS_GLOBAL_CHECK.format(
        book_title=state["book_title"],
        core_concepts=core_concepts,
        notes_preview=all_notes_preview,
    )

    messages = [HumanMessage(content=check_prompt)]
    response_text = _safe_llm_call(llm, messages, fallback="[]", label="全局审查")

    issues = _extract_json_array(response_text)

    if issues:
        print(f"   ⚠️ 发现 {len(issues)} 个一致性问题")
        for i, issue in enumerate(issues[:5], 1):
            if isinstance(issue, dict):
                print(f"   {i}. {issue.get('description', str(issue)[:80])}")
            else:
                print(f"   {i}. {str(issue)[:80]}")
    else:
        print("   ✅ 全书一致性检查通过")

    return {
        "consistency_issues": issues,
        "phase": "merge" if not issues else "global_review",
    }


def fill_knowledge_gaps(state: BookState) -> dict:
    """补充缺失内容 - 逐段修正，避免整体替换导致格式混乱"""
    issues = state.get("consistency_issues", [])
    notes = dict(state["chunk_notes"])

    if not issues:
        return {"phase": "merge"}

    print("\n▶ 补充缺失内容...")
    llm = get_llm(state.get("llm_model"), state.get("llm_base_url"), state.get("llm_api_key"))

    # 按受影响段落分组问题
    chunk_issues = {}
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        affected = issue.get("affected_chunks", [])
        suggestion = issue.get("suggestion", issue.get("description", ""))
        for chunk_ref in affected:
            # 提取段号
            m = re.search(r'(\d+)', str(chunk_ref))
            if m:
                chunk_num = int(m.group(1)) - 1
                if chunk_num not in chunk_issues:
                    chunk_issues[chunk_num] = []
                chunk_issues[chunk_num].append(suggestion)

    if not chunk_issues:
        # 无法定位具体段落，跳过本轮
        print("   ⚠️ 无法定位问题段落，跳过修正")
        return {
            "consistency_issues": [],
            "phase": "merge",
            "global_review_round": state.get("global_review_round", 0) + 1,
        }

    # 逐段修正
    for chunk_idx, issue_list in chunk_issues.items():
        if chunk_idx not in notes:
            continue
        current_note = notes[chunk_idx]
        issues_text = "\n".join(f"- {s}" for s in issue_list[:5])

        messages = [
            HumanMessage(content=f"""请修正以下笔记中的不一致问题，保持原有Markdown结构，只修改有问题的部分：

问题：
{issues_text}

当前笔记：
{current_note[:3000]}

直接输出修正后的完整笔记，不要解释。"""),
        ]

        corrected = _safe_llm_call(llm, messages, fallback=current_note, label=f"修正第{chunk_idx+1}段")

        # 验证修正结果：至少保留原内容的60%
        if len(corrected) > len(current_note) * 0.5:
            notes[chunk_idx] = corrected
            print(f"   ✅ 第{chunk_idx+1}段已修正")
        else:
            print(f"   ⚠️ 第{chunk_idx+1}段修正结果过短，保留原内容")

    return {
        "chunk_notes": notes,
        "consistency_issues": [],
        "phase": "merge",
        "global_review_round": state.get("global_review_round", 0) + 1,
    }
