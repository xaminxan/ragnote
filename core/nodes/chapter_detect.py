"""章节检测与分段逻辑"""
import re
from typing import Dict, List, Tuple


# 章节识别模式
CHAPTER_PATTERNS = [
    # 卷一、卷二、卷三、卷四 等
    (r'卷[一二三四五六七八九十]{1,2}', '卷'),
    # 第一章、第二章 等
    (r'第[一二三四五六七八九十百千]{1,3}章', '章'),
    # 第一节、第二节 等
    (r'第[一二三四五六七八九十百千]{1,3}节', '节'),
]

# 紫微斗数全书的特征标题（用于更精确的章节识别）
BOOK_SPECIFIC_TITLES = [
    '太微赋', '形性赋', '星垣论', '斗数准绳', '斗数发微论',
    '重补斗数', '增补太微赋', '诸星问答论', '女命骨髓赋',
    '定富贵贫贱十等论', '安身命例', '安南北斗诸星诀',
    '安大限诀', '谈星要论', '论人命入格', '论格星数高下',
    '论男女命同异', '论小儿命', '论大限十年祸福',
    '论二限太岁吉凶', '论流年太岁', '论阴骘延寿',
    '论羊陀迭并', '论七杀重逢', '古今富贵贫贱天寿命图',
    '批命', '论诸星同垣',
]


def detect_chapters(ocr_data: Dict[int, str]) -> List[dict]:
    """
    从OCR数据中检测章节边界。
    
    返回格式:
    [
        {"name": "卷一", "start_page": 0, "end_page": 30, "subsections": [...]},
        {"name": "卷二", "start_page": 31, "end_page": 60, "subsections": [...]},
        ...
    ]
    """
    chapters = []
    
    # 第一遍：检测主要章节
    for page_num in sorted(ocr_data.keys()):
        text = ocr_data.get(page_num, "")
        if not text.strip():
            continue
        
        # 检测目录页特征：包含多个章节引用+页码数字
        # 目录页通常有 "卷一...37" "卷二...51" 这样的格式
        page_refs = len(re.findall(r'卷[一二三四五六七八九十].*?\d+', text))
        if page_refs >= 2:
            # 可能是目录页，跳过
            continue
        
        # 检测主要章节（卷X）
        for pattern, level in CHAPTER_PATTERNS:
            matches = list(re.finditer(pattern, text))
            if not matches:
                continue
            
            # 取第一个匹配作为章节标题
            match = matches[0]
            chapter_name = match.group()
            
            # 避免重复添加
            if any(ch['name'] == chapter_name for ch in chapters):
                continue
            
            # 要求与上一个章节至少间隔3页
            if chapters:
                last_page = chapters[-1]['start_page']
                if page_num - last_page < 3:
                    continue
            
            chapters.append({
                "name": chapter_name,
                "start_page": page_num,
                "end_page": None,  # 稍后填充
                "subsections": [],
                "level": level,
            })
    
    # 填充 end_page（在检测子章节之前）
    for i in range(len(chapters)):
        if i + 1 < len(chapters):
            end = chapters[i + 1]["start_page"] - 1
            # 确保 end >= start
            chapters[i]["end_page"] = max(end, chapters[i]["start_page"])
        else:
            # 最后一个章节，延伸到最后一页
            chapters[i]["end_page"] = max(ocr_data.keys()) if ocr_data else chapters[i]["start_page"]
    
    # 第二遍：检测子章节（现在 end_page 已填充）
    for page_num in sorted(ocr_data.keys()):
        text = ocr_data.get(page_num, "")
        if not text.strip():
            continue
        
        for title in BOOK_SPECIFIC_TITLES:
            if title in text:
                # 找到所属的大章节
                parent = _find_parent_chapter(chapters, page_num)
                if parent is not None:
                    subsection = {
                        "name": title,
                        "page": page_num,
                    }
                    if subsection not in chapters[parent]["subsections"]:
                        chapters[parent]["subsections"].append(subsection)
    
    return chapters


def _find_parent_chapter(chapters: List[dict], page_num: int) -> int:
    """找到指定页码所属的章节索引"""
    for i, ch in enumerate(chapters):
        start = ch["start_page"]
        end = ch["end_page"] if ch["end_page"] is not None else float('inf')
        if start <= page_num <= end:
            return i
    # 如果没有找到，返回最后一个章节
    return len(chapters) - 1 if chapters else None


def create_chapter_aware_chunks(
    ocr_data: Dict[int, str],
    max_chunk_size: int = 30,
    min_chunk_size: int = 10,
) -> List[dict]:
    """
    基于检测到的章节创建智能分段。
    
    规则:
    1. 章节边界优先：不跨越章节边界
    2. 大章节拆分：超过 max_chunk_size 页的章节拆分为多个 chunk
    3. 小章节合并：小于 min_chunk_size 页的章节与相邻章节合并
    
    返回格式:
    [
        {
            "start": 0,
            "end": 30,
            "label": "第1段（第1-30页）",
            "chapter": "卷一",
            "subchapters": ["太微赋", "形性赋", ...],
        },
        ...
    ]
    """
    chapters = detect_chapters(ocr_data)
    
    if not chapters:
        # 没有检测到章节，回退到简单的分页模式
        return _create_simple_chunks(ocr_data, max_chunk_size)
    
    print(f"   检测到 {len(chapters)} 个主要章节")
    for ch in chapters:
        subsection_count = len(ch.get("subsections", []))
        print(f"      {ch['name']}: 第{ch['start_page']+1}-{ch['end_page']+1}页"
              f"({ch['end_page'] - ch['start_page'] + 1}页)"
              f"{f', {subsection_count}个子章节' if subsection_count > 0 else ''}")
    
    chunks = []
    
    for chapter in chapters:
        chapter_pages = chapter["end_page"] - chapter["start_page"] + 1
        
        if chapter_pages <= max_chunk_size:
            # 章节不大，整个作为一个 chunk
            chunks.append({
                "start": chapter["start_page"],
                "end": chapter["end_page"],
                "label": f"第{len(chunks)+1}段（{chapter['name']}，第{chapter['start_page']+1}-{chapter['end_page']+1}页）",
                "chapter": chapter["name"],
                "subchapters": [s["name"] for s in chapter.get("subsections", [])],
            })
        else:
            # 大章节需要拆分，优先按子章节拆分
            subsections = chapter.get("subsections", [])
            
            if subsections:
                chunks.extend(_split_by_subsections(chapter, subsections, max_chunk_size, len(chunks)))
            else:
                # 没有子章节，按固定页数拆分
                chunks.extend(_split_by_pages(chapter, max_chunk_size, min_chunk_size, len(chunks)))
    
    return chunks


def _split_by_subsections(
    chapter: dict,
    subsections: List[dict],
    max_chunk_size: int,
    existing_chunks: int,
) -> List[dict]:
    """按子章节拆分大章节"""
    chunks = []
    current_start = chapter["start_page"]
    current_subsections = []
    
    for i, sub in enumerate(subsections):
        sub_page = sub["page"]
        current_subsections.append(sub["name"])
        
        # 检查是否需要拆分
        pages_so_far = sub_page - current_start + 1
        is_last = (i == len(subsections) - 1)
        
        if pages_so_far >= max_chunk_size or is_last:
            end_page = chapter["end_page"] if is_last else sub_page
            # 确保 end_page >= start_page
            if end_page < current_start:
                end_page = current_start
            
            chunks.append({
                "start": current_start,
                "end": end_page,
                "label": f"第{existing_chunks + len(chunks) + 1}段（{chapter['name']}，第{current_start+1}-{end_page+1}页）",
                "chapter": chapter["name"],
                "subchapters": list(current_subsections),
            })
            current_start = end_page + 1
            current_subsections = []
    
    return chunks


def _split_by_pages(
    chapter: dict,
    max_chunk_size: int,
    min_chunk_size: int,
    existing_chunks: int,
) -> List[dict]:
    """按固定页数拆分大章节"""
    chunks = []
    start = chapter["start_page"]
    end = chapter["end_page"]
    
    while start <= end:
        chunk_end = min(start + max_chunk_size - 1, end)
        
        # 如果剩余部分太小，合并到当前 chunk
        remaining = end - chunk_end
        if 0 < remaining < min_chunk_size:
            chunk_end = end
        
        chunks.append({
            "start": start,
            "end": chunk_end,
            "label": f"第{existing_chunks + len(chunks) + 1}段（{chapter['name']}，第{start+1}-{chunk_end+1}页）",
            "chapter": chapter["name"],
            "subchapters": [],
        })
        
        start = chunk_end + 1
    
    return chunks


def _create_simple_chunks(ocr_data: Dict[int, str], max_chunk_size: int) -> List[dict]:
    """简单的分页模式（回退方案）"""
    if not ocr_data:
        return []
    
    total = max(ocr_data.keys()) + 1
    chunks = []
    
    for i in range(0, total, max_chunk_size):
        end = min(i + max_chunk_size, total)
        chunks.append({
            "start": i,
            "end": end - 1,
            "label": f"第{len(chunks)+1}段（第{i+1}-{end}页）",
            "chapter": "",
            "subchapters": [],
        })
    
    return chunks


def organize_output_by_chapter(chunk_notes: Dict[int, str], chunks: List[dict]) -> str:
    """
    按章节组织最终输出。
    
    返回格式化的 Markdown 文本，按章节分组并添加章节标题。
    """
    # 按章节分组
    chapter_groups = {}
    for idx in sorted(chunk_notes.keys()):
        if idx < len(chunks):
            chunk = chunks[idx]
            chapter = chunk.get("chapter", "") or f"未分类段落{idx+1}"
            
            if chapter not in chapter_groups:
                chapter_groups[chapter] = {
                    "notes": [],
                    "subchapters": set(),
                }
            
            chapter_groups[chapter]["notes"].append(chunk_notes[idx])
            chapter_groups[chapter]["subchapters"].update(chunk.get("subchapters", []))
    
    # 生成输出
    output_parts = []
    
    for chapter_name, group in chapter_groups.items():
        # 添加章节标题
        output_parts.append(f"\n\n# {chapter_name}\n")
        
        # 添加子章节索引（如果有）
        if group["subchapters"]:
            subchapter_list = "、".join(sorted(group["subchapters"]))
            output_parts.append(f"**本章包含：{subchapter_list}**\n")
        
        # 添加各段笔记
        for note in group["notes"]:
            # 清理笔记中的通用标题
            cleaned_note = _clean_note_title(note)
            output_parts.append(cleaned_note)
    
    return "\n".join(output_parts)


def _clean_note_title(note: str) -> str:
    """清理笔记中的通用标题"""
    # 将 "# 笔记标题" 替换为更合适的标题
    # 或者直接移除通用标题，保留内容
    lines = note.split("\n")
    cleaned_lines = []
    
    for line in lines:
        # 跳过通用的笔记标题行
        if line.strip() in ["# 笔记标题", "# 笔记标题：", "# 笔记标题："]:
            continue
        # 跳过纯验证笔记（只包含"笔记与全书上下文无冲突"等）
        if "笔记与全书上下文无冲突" in line and len(line) < 100:
            continue
        cleaned_lines.append(line)
    
    return "\n".join(cleaned_lines)
