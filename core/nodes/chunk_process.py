"""分段处理节点"""
import json
import re
import time
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from ..state import BookState
from ..prompts.draft import SYS_DRAFT
from ..prompts.review import SYS_REVIEW, SYS_CONTEXT_CHECK
from ..prompts.knowledge import SYS_KNOWLEDGE_EXTRACT
from ..utils import safe_llm_call as _safe_llm_call

_llm = None


def _extract_json(text: str, expect_type: type = dict):
    """从 LLM 输出中提取 JSON，支持多个 JSON 块时优先选择匹配类型的。
    返回 (parsed, remainder)。remainder 是最后一个 JSON 块之后的文本。"""
    # 先清理 markdown 代码块包裹
    cleaned = text.strip()
    code_block = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?\s*```', cleaned)
    if code_block:
        cleaned = code_block.group(1).strip()

    # 尝试直接解析整个清理后的文本
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, expect_type):
            return parsed, ""
    except json.JSONDecodeError:
        pass

    # 用贪婪匹配找最外层的 { } 或 [ ]，支持嵌套
    matches = []
    for opener, closer in [('{', '}'), ('[', ']')]:
        depth = 0
        start = None
        for i, ch in enumerate(cleaned):
            if ch == opener:
                if depth == 0:
                    start = i
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0 and start is not None:
                    matches.append((start, i + 1))
                    start = None

    for start, end in reversed(matches):
        candidate = cleaned[start:end]
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, expect_type):
                return parsed, cleaned[end:]
        except json.JSONDecodeError:
            continue
    return None, text


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


def prepare_chunk_context(state: BookState) -> dict:
    """准备分段上下文（使用全局框架+全书索引）"""
    idx = state["current_chunk_idx"]
    chunks = state["chunks"]

    if idx >= len(chunks):
        return {"phase": "global_review"}

    chunk = chunks[idx]
    framework = state["framework"]
    knowledge = state["book_knowledge"]
    book_index = state.get("book_index", {})
    vlm_results = state.get("vlm_results", {})

    context_parts = []

    # 全局框架（优先）
    if framework.get("structure"):
        context_parts.append(f"【书籍结构】\n{framework['structure'][:1500]}")
    if framework.get("key_concepts"):
        concepts = "\n".join(
            f"- {name}: {defn[:100]}"
            for name, defn in list(framework["key_concepts"].items())[:15]
        )
        context_parts.append(f"【核心概念】\n{concepts}")
    if framework.get("main_themes"):
        themes = "\n".join(f"- {t}" for t in framework["main_themes"][:5])
        context_parts.append(f"【主要论点】\n{themes}")
    if framework.get("key_figures"):
        figures = "\n".join(
            f"- {name}: {desc[:80]}"
            for name, desc in list(framework["key_figures"].items())[:10]
        )
        context_parts.append(f"【关键人物】\n{figures}")

    # 全书索引：章节地图
    chapter_map = book_index.get("chapter_map", {})
    if chapter_map:
        chapters_text = "\n".join(
            f"- {ch}: {desc[:100]}"
            for ch, desc in list(chapter_map.items())[:15]
        )
        context_parts.append(f"【章节地图】\n{chapters_text}")

    # 全书索引：概念依赖链
    concept_deps = book_index.get("concept_dependencies", [])
    if concept_deps:
        deps_text = "\n".join(
            f"- {d.get('concept', '')}: 依赖{d.get('depends_on', [])}，扩展{d.get('extends', [])}"
            for d in concept_deps[:10]
        )
        context_parts.append(f"【概念依赖链】\n{deps_text}")

    # 全书索引：论证脉络
    argument_flow = book_index.get("argument_flow", "")
    if argument_flow:
        context_parts.append(f"【作者论证脉络】\n{argument_flow[:800]}")

    # 全书索引：跨章节关联
    cross_links = book_index.get("cross_chapter_links", [])
    if cross_links:
        links_text = "\n".join(
            f"- {l.get('from', '')} ↔ {l.get('to', '')}: {l.get('relation', '')}"
            for l in cross_links[:8]
        )
        context_parts.append(f"【跨章节关联】\n{links_text}")

    # 全书索引：核心框架
    frameworks = book_index.get("key_frameworks", [])
    if frameworks:
        fw_text = "\n".join(
            f"- {f.get('name', '')}: {f.get('purpose', '')}（章节: {f.get('chapters', [])}）"
            for f in frameworks[:8]
        )
        context_parts.append(f"【核心框架】\n{fw_text}")

    # VLM图表分析结果（当前段）
    chunk_vlm = {}
    for pg in range(chunk["start"], chunk["end"]):
        if pg in vlm_results:
            chunk_vlm[pg] = vlm_results[pg]
    if chunk_vlm:
        vlm_parts = [f"第{pg+1}页图表：{desc[:300]}" for pg, desc in chunk_vlm.items()]
        context_parts.append(f"【本段图表内容】\n" + "\n".join(vlm_parts))

    # 已处理段落摘要（包含前一段和后一段的上下文）
    summaries = knowledge["chunk_summaries"]
    if summaries:
        summary_parts = []
        # 前一段摘要（当前段之前最近的）
        prev_chunks = [k for k in summaries.keys() if k < idx]
        if prev_chunks:
            prev_k = max(prev_chunks)
            summary_parts.append(f"【前一段摘要】\n第{prev_k+1}段: {summaries[prev_k][:300]}")

        # 后一段摘要（当前段之后最近的，如果已处理）
        next_chunks = [k for k in summaries.keys() if k > idx]
        if next_chunks:
            next_k = min(next_chunks)
            summary_parts.append(f"【后一段摘要】\n第{next_k+1}段: {summaries[next_k][:300]}")

        if summary_parts:
            context_parts.append("\n\n".join(summary_parts))

    context = "\n\n".join(context_parts)

    # 获取当前段OCR文本
    from ..config import get_config
    cfg = get_config()

    texts = []
    for pg in range(chunk["start"], chunk["end"]):
        t = state["ocr_data"].get(pg, "")
        if t.strip():
            texts.append(f"---第{pg+1}页---\n{t}")
    full_text = "\n".join(texts)[:cfg.processing.max_ocr_text_length]
    
    # 调试信息
    print(f"      [调试] 段{idx+1}: OCR={len(full_text)}字, 上下文={len(context)}字")

    return {
        "current_chunk_text": full_text,
        "current_chunk_context": context,
        "current_chunk_label": chunk["label"],
    }


def generate_draft_note(state: BookState) -> dict:
    """轮1：生成初稿"""
    # 如果OCR文本为空（空段），跳过生成
    if not state.get("current_chunk_text", "").strip():
        print("      [1] 跳过（无OCR文本）")
        return {"current_draft": ""}

    llm = get_llm(state.get("llm_model"), state.get("llm_base_url"), state.get("llm_api_key"))

    messages = [
        SystemMessage(content=SYS_DRAFT),
        HumanMessage(content=f"""
书籍：《{state['book_title']}》
分段：{state['current_chunk_label']}

全书上下文：
{state['current_chunk_context']}

OCR文字：
{state['current_chunk_text']}

请结合全书上下文生成详细的结构化笔记，确保理解完整不遗漏。"""),
    ]

    t0 = time.time()
    print("      [1] 初稿...", end=" ")

    ocr_len = len(state['current_chunk_text'])
    ctx_len = len(state['current_chunk_context'])
    print(f"(OCR={ocr_len}字, 上下文={ctx_len}字)", end=" ")

    draft = _safe_llm_call(llm, messages, fallback=state['current_chunk_text'][:2000], label="初稿")
    print(f"→ {len(draft)}字")

    if len(draft) < 100:
        print("      [1] 重试...", end=" ")
        from ..config import get_llm_config
        cfg = get_llm_config()
        llm_retry = ChatOpenAI(
            model=cfg.model,
            base_url=cfg.base_url,
            api_key=cfg.api_key,
            temperature=0.7,
            timeout=cfg.timeout,
        )
        retry_result = _safe_llm_call(llm_retry, messages, fallback=draft, label="初稿重试")
        if len(retry_result) > len(draft):
            draft = retry_result
        print(f"→ {len(draft)}字")

    return {"current_draft": draft}


def review_knowledge(state: BookState) -> dict:
    """轮2：知识审阅"""
    from ..config import get_config
    cfg = get_config()

    # 如果草稿为空，跳过
    if not state.get("current_draft", "").strip():
        print("      [2] 跳过（无内容）")
        return {}

    if cfg.processing.fast_mode:
        print("      [2] 知识审阅... 跳过（快速模式）")
        return {}

    llm = get_llm(state.get("llm_model"), state.get("llm_base_url"), state.get("llm_api_key"))

    print("      [2] 知识审阅...", end=" ")
    messages = [
        SystemMessage(content=SYS_REVIEW),
        HumanMessage(content=f"请用你的专业知识审阅并修正以下笔记：\n\n{state['current_draft']}"),
    ]
    reviewed = _safe_llm_call(llm, messages, fallback=state['current_draft'], label="知识审阅")
    print(f"({len(reviewed)}字)")

    return {"current_draft": reviewed}


def validate_context(state: BookState) -> dict:
    """轮3：上下文一致性验证"""
    from ..config import get_config
    cfg = get_config()

    # 如果草稿为空，跳过
    if not state.get("current_draft", "").strip():
        print("      [3] 跳过（无内容）")
        return {}

    if cfg.processing.fast_mode:
        print("      [3] 上下文验证... 跳过（快速模式）")
        return {}

    context = state.get("current_chunk_context", "")
    if not context.strip():
        print("      [3] 无上下文，跳过")
        return {}

    llm = get_llm(state.get("llm_model"), state.get("llm_base_url"), state.get("llm_api_key"))

    print("      [3] 上下文验证...", end=" ")
    messages = [
        SystemMessage(content=SYS_CONTEXT_CHECK),
        HumanMessage(content=f"全书上下文：\n{context[:cfg.processing.context_window]}\n\n请验证当前笔记与全书上下文的一致性，确保没有断章取义：\n\n{state['current_draft']}"),
    ]
    validated = _safe_llm_call(llm, messages, fallback=state['current_draft'], label="上下文验证")
    print(f"({len(validated)}字)")

    return {"current_draft": validated}


def polish_note(state: BookState) -> dict:
    """轮4：终稿润色"""
    from ..config import get_config
    cfg = get_config()

    # 如果草稿为空，跳过
    if not state.get("current_draft", "").strip():
        print("      [4] 跳过（无内容）")
        return {}

    if cfg.processing.fast_mode:
        print("      [4] 终稿去残留... 跳过（快速模式）")
        return {}

    llm = get_llm(state.get("llm_model"), state.get("llm_base_url"), state.get("llm_api_key"))

    print("      [4] 终稿去残留...", end=" ")
    messages = [
        SystemMessage(content="你进行最终润色。输出干净、通顺、格式规范的笔记。\n\n【重要规则】\n- 直接输出笔记内容，不要输出任何解释、评价、审阅意见\n- 不要加\"好的\"、\"以下为\"、\"经审阅\"之类的开头\n- 第一行必须是\"# 笔记标题\"\n- 全文无乱码无OCR残留\n- 【核心要求】保留所有重要内容，不要过度浓缩或删减，维持原文的信息密度\n- 【禁止】删除所有括号及括号内的内容，包括\"（第X页）\"、\"（原文及评注）\"、\"（见第X页）\"、\"（第X-Y页）\"等任何形式的页码标注和注释\n- 【禁止】不要出现\"对应第X章\"、\"对应第X节\"等映射说明\n- 【禁止】不要标注段落来源\n- 【禁止】不要过度删减，保留所有重要细节和论据"),
        HumanMessage(content=f"请确保以下笔记无OCR残留无乱码，删除所有括号及括号内的页码标注和注释，保留全部重要内容，然后直接输出：\n\n{state['current_draft']}"),
    ]
    final = _safe_llm_call(llm, messages, fallback=state['current_draft'], label="终稿润色")
    print(f"({len(final)}字)")

    # 后处理：删除残留的括号注释
    import re as _re
    # 删除中文括号及其内容：（xxx）
    final = _re.sub(r'（[^）]{0,50}）', '', final)
    # 删除英文括号及其内容：(xxx) 但保留代码块中的括号
    final = _re.sub(r'\([^)]{0,50}\)', '', final)
    # 清理多余空行
    final = _re.sub(r'\n{3,}', '\n\n', final)

    return {"current_draft": final}


def update_book_knowledge(state: BookState) -> dict:
    """更新全书知识图谱"""
    knowledge = state["book_knowledge"]
    idx = state["current_chunk_idx"]
    note = state["current_draft"]

    # 如果笔记为空，跳过知识提取
    if not note.strip():
        chunk_notes = dict(state["chunk_notes"])
        chunk_notes[idx] = ""
        processed = list(state["processed_chunks"])
        processed.append(idx)
        from ..checkpoint import save_checkpoint
        save_checkpoint(state["output_dir"], state["pdf_path"], {
            "processed_chunks": processed,
            "chunk_notes": chunk_notes,
            "book_knowledge": knowledge,
            "current_chunk_idx": idx + 1,
            "chunks": state.get("chunks", []),
        })
        return {
            "book_knowledge": knowledge,
            "chunk_notes": chunk_notes,
            "processed_chunks": processed,
            "current_chunk_idx": idx + 1,
        }

    llm = get_llm(state.get("llm_model"), state.get("llm_base_url"), state.get("llm_api_key"))

    # 提取知识图谱信息
    existing_concepts = list(knowledge["key_concepts"].keys())[:30]
    extract_prompt = SYS_KNOWLEDGE_EXTRACT.format(
        existing_concepts=existing_concepts,
        note_content=note[:8000],
    )

    try:
        messages = [HumanMessage(content=extract_prompt)]
        response_text = _safe_llm_call(llm, messages, fallback="{}", label="知识提取")
        updates, _ = _extract_json(response_text, dict)
        if updates is None:
            raise json.JSONDecodeError("无法从LLM输出提取JSON", response_text, 0)

        for c in updates.get("new_concepts", []):
            name = c["name"]
            if name not in knowledge["key_concepts"]:
                knowledge["key_concepts"][name] = {
                    "name": name,
                    "definition": c.get("definition", ""),
                    "first_seen_chunk": idx,
                    "last_seen_chunk": idx,
                    "related_concepts": [],
                    "importance": c.get("importance", "minor"),
                }
            else:
                knowledge["key_concepts"][name]["last_seen_chunk"] = idx

        knowledge["key_figures"].update(updates.get("new_figures", {}))
        knowledge["chunk_summaries"][idx] = updates.get("chunk_summary", "")

        # 内存控制：只保留最近N段的摘要
        from ..config import get_config
        max_summaries = get_config().processing.max_chunk_summaries
        if len(knowledge["chunk_summaries"]) > max_summaries:
            sorted_keys = sorted(knowledge["chunk_summaries"].keys())
            for old_key in sorted_keys[:-max_summaries]:
                del knowledge["chunk_summaries"][old_key]

        knowledge["timeline"].extend(
            [{**e, "chunk_idx": idx} for e in updates.get("timeline_events", [])]
        )
        knowledge["concept_relations"].extend(updates.get("new_relations", []))
    except Exception as e:
        print(f"      ⚠️ 知识提取失败: {str(e)[:50]}")

    # 保存笔记
    chunk_notes = dict(state["chunk_notes"])
    chunk_notes[idx] = note

    processed = list(state["processed_chunks"])
    processed.append(idx)

    # 保存checkpoint
    from ..checkpoint import save_checkpoint
    save_checkpoint(state["output_dir"], state["pdf_path"], {
        "processed_chunks": processed,
        "chunk_notes": chunk_notes,
        "book_knowledge": knowledge,
        "current_chunk_idx": idx + 1,
        "chunks": state.get("chunks", []),
    })

    return {
        "book_knowledge": knowledge,
        "chunk_notes": chunk_notes,
        "processed_chunks": processed,
        "current_chunk_idx": idx + 1,
    }
