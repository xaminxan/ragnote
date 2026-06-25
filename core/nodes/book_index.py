"""全局索引节点"""
import json
import re
from langchain_core.messages import HumanMessage

from ..state import BookState
from ..prompts.book_index import SYS_BOOK_INDEX, SYS_BOOK_INDEX_CONCISE


def _get_llm(state: BookState = None):
    """获取LLM实例"""
    from langchain_openai import ChatOpenAI
    from ..config import get_llm_config

    model_override = state.get("llm_model") if state else None
    base_url_override = state.get("llm_base_url") if state else None
    api_key_override = state.get("llm_api_key") if state else None

    cfg = get_llm_config(model_override, base_url_override, api_key_override)
    return ChatOpenAI(
        model=cfg.model,
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        temperature=cfg.temperature,
        timeout=cfg.timeout,
    )


def _extract_json(text: str):
    """从LLM输出中提取JSON"""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    matches = list(re.finditer(r'\{[\s\S]*?\}', text))
    for m in reversed(matches):
        try:
            parsed = json.loads(m.group())
            if isinstance(parsed, dict) and ("chapter_map" in parsed or "argument_flow" in parsed):
                return parsed
        except json.JSONDecodeError:
            continue
    return None


def generate_book_index(state: BookState) -> dict:
    """生成全书索引：章节地图、概念依赖、论证脉络、跨章节关联"""
    ocr_data = state["ocr_data"]
    framework = state["framework"]
    summary_level = state["framework"]["summary_level"]
    book_title = state["book_title"]

    print("\n▶ 生成全书索引（全文评估）...")

    # 分批读取OCR文本
    batch_size = 15000
    all_text = []
    for pg in sorted(ocr_data.keys()):
        text = ocr_data[pg]
        if text.strip():
            all_text.append(f"---第{pg+1}页---\n{text}")

    full_text = "\n".join(all_text)

    # 分批处理
    batches = []
    for i in range(0, len(full_text), batch_size):
        batches.append(full_text[i:i + batch_size])

    llm = _get_llm(state)
    prompt = SYS_BOOK_INDEX if summary_level != "concise" else SYS_BOOK_INDEX_CONCISE

    batch_results = []
    for idx, batch in enumerate(batches):
        print(f"   索引批次 {idx+1}/{len(batches)}...", end=" ")
        messages = [HumanMessage(content=f"书籍：《{book_title}》\n\n{prompt}\n\nOCR文本：\n{batch}")]
        try:
            response = llm.invoke(messages)
            result = _extract_json(response.content)
            if result:
                batch_results.append(result)
                print(f"OK")
            else:
                print(f"解析失败")
        except Exception as e:
            error_msg = str(e)
            if "security" in error_msg.lower() or "rejected" in error_msg.lower():
                print(f"安全过滤跳过")
            else:
                print(f"错误: {error_msg[:50]}")

    # 合并批次结果
    if not batch_results:
        print("   ⚠️ 索引生成失败，使用空索引")
        return {"book_index": {
            "chapter_map": {},
            "concept_dependencies": [],
            "argument_flow": "",
            "cross_chapter_links": [],
            "key_frameworks": [],
        }}

    # 合并多个批次的索引
    merged = {
        "chapter_map": {},
        "concept_dependencies": [],
        "argument_flow": [],
        "cross_chapter_links": [],
        "key_frameworks": [],
    }

    for batch_result in batch_results:
        merged["chapter_map"].update(batch_result.get("chapter_map", {}))
        merged["concept_dependencies"].extend(batch_result.get("concept_dependencies", []))
        flow = batch_result.get("argument_flow", "")
        if flow:
            merged["argument_flow"].append(flow)
        merged["cross_chapter_links"].extend(batch_result.get("cross_chapter_links", []))
        merged["key_frameworks"].extend(batch_result.get("key_frameworks", []))

    # 去重概念依赖
    seen_deps = set()
    unique_deps = []
    for dep in merged["concept_dependencies"]:
        key = dep.get("concept", "")
        if key and key not in seen_deps:
            seen_deps.add(key)
            unique_deps.append(dep)
    merged["concept_dependencies"] = unique_deps

    # 去重框架
    seen_fw = set()
    unique_fw = []
    for fw in merged["key_frameworks"]:
        name = fw.get("name", "")
        if name and name not in seen_fw:
            seen_fw.add(name)
            unique_fw.append(fw)
    merged["key_frameworks"] = unique_fw

    # 合并论证脉络
    merged["argument_flow"] = "\n".join(merged["argument_flow"])

    # 去重跨章节关联
    seen_links = set()
    unique_links = []
    for link in merged["cross_chapter_links"]:
        key = (link.get("from", ""), link.get("to", ""))
        if key not in seen_links:
            seen_links.add(key)
            unique_links.append(link)
    merged["cross_chapter_links"] = unique_links

    stats = (
        f"章节{len(merged['chapter_map'])}个, "
        f"概念依赖{len(merged['concept_dependencies'])}条, "
        f"框架{len(merged['key_frameworks'])}个"
    )
    print(f"   ✅ 全书索引完成: {stats}")

    return {"book_index": merged}
