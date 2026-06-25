"""合并输出节点"""
import os
import re
from ..state import BookState


def save_notes(state: BookState) -> dict:
    """保存分段笔记到临时目录"""
    output_dir = state["output_dir"]
    notes = state["chunk_notes"]

    # 临时目录存放分段文件
    temp_dir = os.path.join(output_dir, "_chunks")
    os.makedirs(temp_dir, exist_ok=True)

    saved_files = []
    for idx in sorted(notes.keys()):
        content = notes[idx].strip()
        filename = f"{idx+1:02d}_chunk.md"
        filepath = os.path.join(temp_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        saved_files.append(filepath)

    return {"_saved_files": saved_files}


def merge_all_notes(state: BookState) -> dict:
    """合并所有笔记为按书名命名的最终文档"""
    from .chapter_detect import organize_output_by_chapter

    knowledge = state["book_knowledge"]
    notes = state["chunk_notes"]
    output_dir = state["output_dir"]
    book_title = state["book_title"]
    book_index = state.get("book_index", {})
    chunks = state.get("chunks", [])

    # 清理output目录中可能存在的旧分段文件
    cleanup_old_segments(output_dir)

    # 按章节组织笔记
    if chunks and len(chunks) == len(notes):
        print("   按章节组织输出...")
        merged_parts = [organize_output_by_chapter(notes, chunks)]
    else:
        # 回退到简单拼接模式
        print("   简单拼接模式")
        merged_parts = []
        for idx in sorted(notes.keys()):
            merged_parts.append(notes[idx])

    # 附录：核心概念索引
    if knowledge["key_concepts"]:
        appendix = "\n\n---\n\n## 附录：核心概念索引\n\n"
        for name, info in sorted(
            knowledge["key_concepts"].items(),
            key=lambda x: {"core": 0, "important": 1, "minor": 2}.get(x[1]["importance"], 2),
        ):
            appendix += f"- **{name}**: {info['definition'][:100]}（首次出现：第{info['first_seen_chunk']+1}段）\n"
        merged_parts.append(appendix)

    # 附录：时间线
    if knowledge["timeline"]:
        timeline_section = "\n\n## 附录：时间线\n\n"
        for event in knowledge["timeline"]:
            timeline_section += f"- {event['period']}: {event['event']}\n"
        merged_parts.append(timeline_section)

    final_output = "\n\n".join(merged_parts)

    # 按书名保存最终文件
    safe_title = re.sub(r'[\\/*?:"<>|]', '', book_title).strip() or "未命名"
    final_path = os.path.join(output_dir, f"{safe_title}.md")
    os.makedirs(output_dir, exist_ok=True)

    with open(final_path, "w", encoding="utf-8") as f:
        f.write(final_output)

    # 保存全书索引为独立文件
    if book_index:
        index_path = os.path.join(output_dir, f"{safe_title}_索引.md")
        index_content = _format_book_index(book_index, book_title)
        with open(index_path, "w", encoding="utf-8") as f:
            f.write(index_content)
        print(f"   📑 全书索引：{index_path}")

    # 删除临时分段目录
    temp_dir = os.path.join(output_dir, "_chunks")
    if os.path.exists(temp_dir):
        for f in os.listdir(temp_dir):
            try:
                os.remove(os.path.join(temp_dir, f))
            except Exception:
                pass
        try:
            os.rmdir(temp_dir)
        except Exception:
            pass

    # 质量统计
    total_chars = len(final_output)
    note_count = len(notes)
    concept_count = len(knowledge["key_concepts"])
    figure_count = len(knowledge["key_figures"])
    timeline_count = len(knowledge["timeline"])

    # 计算原始文本覆盖率
    ocr_data = state.get("ocr_data", {})
    original_chars = sum(len(t) for t in ocr_data.values())
    coverage = (total_chars / original_chars * 100) if original_chars > 0 else 0

    # 输出格式校验
    warnings = _validate_output(final_output)

    print(f"\n   📄 最终笔记：{final_path}")
    print(f"\n   {'='*40}")
    print(f"   📊 质量统计")
    print(f"   {'='*40}")
    print(f"   笔记字数：{total_chars:,} 字")
    print(f"   段落数量：{note_count} 段")
    print(f"   核心概念：{concept_count} 个")
    print(f"   关键人物：{figure_count} 个")
    print(f"   时间线事件：{timeline_count} 个")
    print(f"   原始文本：{original_chars:,} 字")
    print(f"   内容覆盖率：{coverage:.1f}%")
    if warnings:
        print(f"   ⚠️ 格式问题：{len(warnings)} 个")
        for w in warnings[:3]:
            print(f"      - {w}")
    else:
        print(f"   ✅ 格式校验通过")
    print(f"   {'='*40}")

    return {"final_output": final_output, "phase": "done"}


def _format_book_index(book_index: dict, book_title: str) -> str:
    """格式化全书索引为Markdown"""
    parts = [f"# 《{book_title}》全书索引\n"]

    # 章节地图
    chapter_map = book_index.get("chapter_map", {})
    if chapter_map:
        parts.append("## 章节地图\n")
        for ch, desc in chapter_map.items():
            parts.append(f"- **{ch}**: {desc}")
        parts.append("")

    # 概念依赖链
    concept_deps = book_index.get("concept_dependencies", [])
    if concept_deps:
        parts.append("## 概念依赖链\n")
        for dep in concept_deps:
            concept = dep.get("concept", "")
            depends_on = dep.get("depends_on", [])
            extends = dep.get("extends", [])
            line = f"- **{concept}**"
            if depends_on:
                line += f" ← 依赖: {', '.join(depends_on)}"
            if extends:
                line += f" → 扩展: {', '.join(extends)}"
            parts.append(line)
        parts.append("")

    # 作者论证脉络
    argument_flow = book_index.get("argument_flow", "")
    if argument_flow:
        parts.append("## 作者论证脉络\n")
        parts.append(argument_flow)
        parts.append("")

    # 跨章节关联
    cross_links = book_index.get("cross_chapter_links", [])
    if cross_links:
        parts.append("## 跨章节关联\n")
        for link in cross_links:
            parts.append(f"- **{link.get('from', '')}** ↔ **{link.get('to', '')}**: {link.get('relation', '')}")
        parts.append("")

    # 核心框架
    frameworks = book_index.get("key_frameworks", [])
    if frameworks:
        parts.append("## 核心框架\n")
        for fw in frameworks:
            name = fw.get("name", "")
            purpose = fw.get("purpose", "")
            chapters = fw.get("chapters", [])
            parts.append(f"- **{name}**: {purpose}（章节: {', '.join(chapters)}）")
        parts.append("")

    return "\n".join(parts)


def cleanup_old_segments(output_dir):
    """清理output目录中可能存在的旧分段文件（仅清理已知的旧命名模式）"""
    if not os.path.exists(output_dir):
        return

    # 只清理明确属于旧系统输出模式的文件，避免误删用户文件
    OLD_PATTERNS = [
        re.compile(r'^00_完整笔记\.md$'),
        re.compile(r'^.*（第\d+-\d+页）.*\.md$'),
        re.compile(r'^.*结构化笔记.*\.md$'),
        re.compile(r'^\d{2}_chunk\.md$'),
    ]

    cleaned = 0
    for f in os.listdir(output_dir):
        filepath = os.path.join(output_dir, f)
        if not os.path.isfile(filepath):
            continue

        should_clean = any(p.match(f) for p in OLD_PATTERNS)

        if should_clean:
            try:
                os.remove(filepath)
                print(f"   🗑️  清理旧文件：{f}")
                cleaned += 1
            except Exception as e:
                print(f"   ⚠️  清理失败：{f} - {e}")

    if cleaned > 0:
        print(f"   ✅ 清理了 {cleaned} 个旧文件")


def _validate_output(text: str) -> list:
    """校验最终Markdown输出的基本结构，返回警告列表"""
    warnings = []

    # 检查是否有标题
    if not re.search(r'^#\s+.+', text, re.MULTILINE):
        warnings.append("缺少一级标题 (# 标题)")

    # 检查是否有二级标题
    if not re.search(r'^##\s+.+', text, re.MULTILINE):
        warnings.append("缺少二级标题 (## 标题)")

    # 检查是否有过多连续空行
    if re.search(r'\n{4,}', text):
        warnings.append("存在过多连续空行")

    # 检查是否有未闭合的代码块
    code_block_count = text.count('```')
    if code_block_count % 2 != 0:
        warnings.append("存在未闭合的代码块")

    # 检查是否有乱码特征
    garbled_pattern = re.compile(r'[\ufffd]{3,}|[锟斤拷]{2,}|[烫烫]{2,}')
    if garbled_pattern.search(text):
        warnings.append("可能包含乱码")

    return warnings
