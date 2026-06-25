import os
import re
import sys
import json
import time
import tempfile
import subprocess
import fitz
from openai import OpenAI

sys.stdout.reconfigure(encoding='utf-8')

# ================= 配置 =================
LLM_API_KEY = "simon123"
LLM_BASE_URL = "http://10.10.10.200:8317/v1"
LLM_MODEL_NAME = "deepseek-v4-flash"

PAGES_PER_CHUNK = 30
OCR_BATCH = 20
OCR_TIMEOUT = 600

# ================= LLM =================
_client = None
def llm(system, user, temp=0.3, timeout=180):
    global _client
    if _client is None:
        _client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
    r = _client.chat.completions.create(
        model=LLM_MODEL_NAME, temperature=temp, timeout=timeout,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return r.choices[0].message.content

# ================= 文字型PDF检测与提取 =================
def safe_open_pdf(pdf_path):
    """安全打开PDF，处理损坏/加密情况"""
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        print(f"❌ 无法打开PDF: {e}")
        sys.exit(1)
    if doc.is_encrypted:
        try:
            doc.authenticate("")
        except Exception:
            print("❌ PDF已加密，无法处理")
            sys.exit(1)
    return doc

def extract_all_text(doc):
    """逐页提取文本，返回 {页码: 文本}"""
    result = {}
    for pg in range(len(doc)):
        try:
            result[pg] = doc[pg].get_text().strip()
        except Exception:
            result[pg] = ""
    return result

def find_image_pages(doc):
    """检测哪些页面包含嵌入图片或矢量图形"""
    image_pages = []
    for pg in range(len(doc)):
        has_images = len(doc[pg].get_images()) > 0
        has_drawings = False
        try:
            drawings = doc[pg].get_drawings()
            has_drawings = len(drawings) > 20
        except Exception:
            pass
        if has_images or has_drawings:
            image_pages.append(pg)
    return image_pages

def is_blank_page(doc, pg):
    """判断是否空白页"""
    try:
        text = doc[pg].get_text().strip()
        images = doc[pg].get_images()
        drawings = doc[pg].get_drawings()
        return len(text) < 10 and len(images) == 0 and len(drawings) < 5
    except Exception:
        return True

# ================= OCR（一次性全部做完） =================
def ocr_all(img_paths):
    runner = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ocr_runner.py")
    result = {}
    total = len(img_paths)
    for i in range(0, total, OCR_BATCH):
        batch = img_paths[i:i+OCR_BATCH]
        bnum = i // OCR_BATCH + 1
        bn = (total + OCR_BATCH - 1) // OCR_BATCH
        print(f"    OCR 批次 {bnum}/{bn}", end="")
        try:
            r = subprocess.run(
                [sys.executable, runner, json.dumps(batch, ensure_ascii=False)],
                capture_output=True, timeout=OCR_TIMEOUT,
            )
            out = r.stdout.decode('utf-8', errors='replace')
            ok = False
            if r.returncode == 0 and out.strip():
                for line in reversed(out.strip().split('\n')):
                    try:
                        result.update(json.loads(line))
                        ok = True
                        break
                    except:
                        pass
            if not ok:
                for p in batch:
                    result[p] = ""
            ne = sum(1 for v in result.values() if v.strip())
            print(f" → OK（{ne}页有内容）")
        except Exception as e:
            print(f" → 失败: {str(e)[:40]}")
            for p in batch:
                result[p] = ""
    return result

# ================= 多轮LLM =================
SYS_DRAFT = """你是一位知识整理专家，精通各领域学术内容。将OCR文字整理为结构化笔记。
规则：
1. 修正OCR错字乱码，繁体转简体
2. 输出不含OCR原文片段
3. 保留所有重要内容，不要过度浓缩
4. 根据书籍内容类型调整笔记重点（如科技类保留数据公式，历史类保留年代人物，文学类保留引用赏析）
5. 【重要】必须结合前后文理解，避免断章取义
6. 追踪人物、事件、时间线，确保与前文连贯
7. 【禁止】不要出现"对应第X章"、"对应第X节"、"（见第X页）"等映射说明
8. 【禁止】不要标注段落来源（如"本节对应原书第1-2章"）

输出格式（直接输出Markdown，不加任何解释）：

# 笔记标题

## 📖 章节概述
详细概括核心内容，保留关键数据和例子，2-4段话。

## 🔑 关键概念
重要概念和定义，每个概念用1-2句话详细解释，保留所有关键术语。

## 📝 详细要点
按逻辑顺序整理所有知识点，每个要点都要展开说明，保留具体数据、例子和论证过程。这是最重要的部分，要详细。

## 💡 核心论点
作者的主要观点和详细论证，包括论据和推理过程。

## 📎 引用文献
文中提到的文献、出处。"""

SYS_REVIEW = """你是一位严格的学术审稿人。请独立审阅笔记，用你的专业知识判断：
1. 概念、人名、年代是否准确
2. 逻辑是否连贯
3. 文字是否通顺
4. 内容是否完整，有无重要信息遗漏
5. 【重要】是否存在断章取义？当前笔记是否与前后文保持逻辑一致性？

如有问题直接修正，保持原有的 Markdown 结构不变，输出完整笔记。
【禁止】不要添加"对应第X章"、"对应第X节"、"（见第X页）"等映射说明。
【禁止】不要过度删减内容，保留所有重要细节。"""

SYS_POLISH = """你进行最终润色。输出干净、通顺、格式规范的笔记。

【重要规则】
- 直接输出笔记内容，不要输出任何解释、评价、审阅意见
- 不要加"好的"、"以下为"、"经审阅"之类的开头
- 第一行必须是"# 笔记标题"
- 全文无乱码无OCR残留
- 保留所有重要内容，不要过度浓缩或删减
- 【禁止】不要出现"对应第X章"、"对应第X节"、"（见第X页）"等映射说明
- 【禁止】不要标注段落来源
- 【禁止】不要过度删减，保留所有重要细节
- 保留 ## 📖 章节概述 / ## 🔑 关键概念 / ## 📝 详细要点 / ## 💡 核心论点 / ## 📎 引用文献 五个部分"""

def refine_chapter(book_title, label, ocr_text, prev_cx, next_cx="", framework_context=""):
    t0 = time.time()

    # 轮1：初稿
    context_parts = []
    if framework_context.strip():
        context_parts.append(f"【全局框架】\n{framework_context[:3000]}\n")
    if prev_cx.strip():
        context_parts.append(f"【前文要点】\n{prev_cx[:1500]}\n")
    if next_cx.strip():
        context_parts.append(f"【后文预告】\n{next_cx[:1000]}\n")
    context_hint = "\n".join(context_parts)
    
    print("      [1] 初稿...", end=" ")
    d = llm(SYS_DRAFT,
        f"书籍：《{book_title}》\n分段：{label}\n\n{context_hint}\n\nOCR文字：\n{ocr_text[:35000]}\n\n请结合全书上下文生成详细的结构化笔记，保留所有重要内容，确保理解完整不遗漏。",
        temp=0.5)
    if len(d) < 100:
        d = llm(SYS_DRAFT,
            f"书籍：《{book_title}》\n分段：{label}\n\n{context_hint}\n\nOCR文字：\n{ocr_text[:35000]}\n\n请结合全书上下文生成详细的结构化笔记，保留所有重要内容，确保理解完整不遗漏。",
            temp=0.7)
    print(f"({len(d)}字)")

    # 轮2：自纠（1次，用知识判断不依赖原文）
    print("      [2] 知识审阅...", end=" ")
    d = llm(SYS_REVIEW,
        f"请用你的专业知识审阅并修正以下笔记：\n\n{d}",
        temp=0.25)
    print(f"({len(d)}字)")

    # 轮3：上下文融合（如有前文或后文）
    if prev_cx.strip() or next_cx.strip():
        print("      [3] 上下文验证...", end=" ")
        context_info = ""
        if prev_cx.strip():
            context_info += f"前文已确认的内容：\n{prev_cx[:1500]}\n\n"
        if next_cx.strip():
            context_info += f"后文将涉及的内容：\n{next_cx[:1000]}\n\n"
        d = llm(SYS_REVIEW,
            f"{context_info}请验证当前笔记与前后文的一致性，确保没有断章取义，并修正任何不一致之处：\n\n{d}",
            temp=0.2)
        print(f"({len(d)}字)")

    # 轮4：去残留
    print("      [4] 终稿去残留...", end=" ")
    final = llm(SYS_POLISH,
        f"请确保以下笔记无OCR残留无乱码，然后直接输出：\n\n{d}",
        temp=0.15)
    print(f"({len(final)}字 √ {time.time()-t0:.0f}秒)")
    return final

def extract_summary(note):
    m = re.search(r'## .*章节概述\s*\n(.+?)(?:\n##|\Z)', note, re.DOTALL)
    return m.group(1).strip()[:500] if m else note[:300]

def save_note_temp(content, temp_dir, seg_idx):
    """保存分段笔记到临时目录"""
    content = content.strip()
    os.makedirs(temp_dir, exist_ok=True)
    path = os.path.join(temp_dir, f"{seg_idx:02d}_chunk.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def merge_and_cleanup(book_title, output_dir, temp_dir):
    """合并所有分段笔记并清理临时文件"""
    safe = re.sub(r'[\\/*?:"<>|]', '', book_title).strip()[:80] or "未命名"
    final_path = os.path.join(output_dir, f"{safe}.md")
    os.makedirs(output_dir, exist_ok=True)

    # 清理output目录中可能存在的旧分段文件
    cleanup_old_segments(output_dir)

    parts = []
    if os.path.exists(temp_dir):
        for f in sorted(os.listdir(temp_dir)):
            if f.endswith("_chunk.md"):
                with open(os.path.join(temp_dir, f), "r", encoding="utf-8") as fh:
                    parts.append(fh.read().strip())
                os.remove(os.path.join(temp_dir, f))
        os.rmdir(temp_dir)

    if parts:
        final_output = "\n\n".join(parts)
        with open(final_path, "w", encoding="utf-8") as f:
            f.write(final_output)
        print(f"\n   📄 最终笔记：{final_path}")
        return final_path
    return None


def cleanup_old_segments(output_dir):
    """清理output目录中可能存在的旧分段文件"""
    if not os.path.exists(output_dir):
        return
    
    cleaned = 0
    for f in os.listdir(output_dir):
        filepath = os.path.join(output_dir, f)
        if not os.path.isfile(filepath):
            continue
        
        # 清理分段文件：01_xxx.md, 02_xxx.md, 等
        # 清理完整笔记文件：00_完整笔记.md
        # 清理旧命名模式的文件
        should_clean = (
            re.match(r'^\d{2}_[\w\-\s（）《》】【]+\.md$', f) or
            re.match(r'^00_完整笔记\.md$', f) or
            re.match(r'^.*（第\d+-\d+页）.*\.md$', f) or
            re.match(r'^.*结构化笔记.*\.md$', f)
        )
        
        if should_clean:
            try:
                os.remove(filepath)
                print(f"   🗑️  清理旧文件：{f}")
                cleaned += 1
            except Exception as e:
                print(f"   ⚠️  清理失败：{f} - {e}")
    
    if cleaned > 0:
        print(f"   ✅ 清理了 {cleaned} 个旧文件")

# ================= 框架构建 =================
SYS_FRAMEWORK_DETAILED = """你是一位书籍分析专家。请分析以下OCR文本，生成详细的书籍框架。

要求：
1. 提取书籍结构大纲（章节目录）
2. 识别核心概念及定义（至少10个）
3. 总结主要论点和主题
4. 列出关键人物/事件
5. 保持输出详细，控制在3000字以内

输出格式（JSON）：
{
    "structure": "书籍结构大纲（Markdown格式）",
    "key_concepts": {"概念名": "详细定义"},
    "main_themes": ["主题1详细说明", "主题2详细说明"],
    "key_figures": {"人物名": "详细简介"}
}"""

SYS_FRAMEWORK_MEDIUM = """你是一位书籍分析专家。请分析以下OCR文本，生成书籍框架。

要求：
1. 提取主要章节结构
2. 识别5-8个核心概念（每个概念详细定义）
3. 总结主要论点（详细说明）
4. 列出关键人物（详细简介）
5. 控制在2500字以内

输出格式（JSON）：
{
    "structure": "章节结构",
    "key_concepts": {"概念": "详细定义"},
    "main_themes": ["详细论点说明"],
    "key_figures": {"人物": "详细简介"}
}"""

SYS_FRAMEWORK_CONCISE = """你是一位书籍分析专家。请快速提取以下OCR文本的框架。

要求：
1. 简要结构（3-5个要点）
2. 5-8个核心概念（每个概念简要定义）
3. 2-3个主要论点
4. 控制在2000字以内

输出格式（JSON）：
{
    "structure": "简要结构",
    "key_concepts": {"概念": "简要定义"},
    "main_themes": ["论点说明"],
    "key_figures": {}
}"""

def estimate_length_and_get_prompt(total_chars):
    """估算字数并返回对应提示词"""
    if total_chars < 30000:
        print(f"   📊 短篇（{total_chars}字）→ 详细框架")
        return SYS_FRAMEWORK_DETAILED
    elif total_chars < 100000:
        print(f"   📊 中篇（{total_chars}字）→ 中等框架")
        return SYS_FRAMEWORK_MEDIUM
    else:
        print(f"   📊 长篇（{total_chars}字）→ 精简框架")
        return SYS_FRAMEWORK_CONCISE

def build_framework(ocr_data, book_title):
    """构建全局框架"""
    print(f"\n▶ 构建全局框架...")
    
    # 分批读取OCR文本
    batch_size = 15000
    all_text = []
    for pg in sorted(ocr_data.keys()):
        text = ocr_data[pg]
        if text.strip():
            all_text.append(f"---第{pg+1}页---\n{text}")
    
    full_text = "\n".join(all_text)
    total_chars = len(full_text)
    
    # 获取对应提示词
    prompt = estimate_length_and_get_prompt(total_chars)
    
    # 分批处理
    batches = []
    for i in range(0, len(full_text), batch_size):
        batches.append(full_text[i:i+batch_size])
    
    # 生成各批摘要
    batch_summaries = []
    for idx, batch in enumerate(batches):
        print(f"   处理批次 {idx+1}/{len(batches)}...", end=" ")
        try:
            response = llm(prompt, f"书籍：《{book_title}》\n\nOCR文本：\n{batch}", temp=0.3)
            batch_summaries.append(response)
            print(f"({len(response)}字)")
        except Exception as e:
            error_msg = str(e)
            if "security" in error_msg.lower() or "rejected" in error_msg.lower() or "18" in error_msg:
                print(f"⚠️ 安全过滤跳过")
                batch_summaries.append("")
            else:
                print(f"⚠️ 错误: {error_msg[:50]}")
                batch_summaries.append("")
    
    # 合并所有摘要，生成最终框架
    print("   合并框架...", end=" ")
    # 过滤掉空的摘要
    valid_summaries = [s for s in batch_summaries if s.strip()]
    if not valid_summaries:
        print("⚠️ 所有批次都被过滤，使用默认框架")
        return {
            "structure": "",
            "key_concepts": {},
            "main_themes": [],
            "key_figures": {},
        }
    
    combined = "\n\n".join(valid_summaries)
    merge_prompt = f"""请合并以下书籍各部分的分析结果，生成一个统一的书籍框架。

书籍：《{book_title}》

各部分分析结果：
{combined[:8000]}

{prompt}"""
    
    try:
        response = llm("", merge_prompt, temp=0.3)
    except Exception as e:
        error_msg = str(e)
        if "security" in error_msg.lower() or "rejected" in error_msg.lower() or "18" in error_msg:
            print(f"⚠️ 安全过滤，使用合并摘要")
            return {
                "structure": combined[:2000],
                "key_concepts": {},
                "main_themes": [],
                "key_figures": {},
            }
        else:
            raise
    
    # 解析JSON
    try:
        framework = json.loads(response)
    except json.JSONDecodeError:
        json_match = re.search(r'\{[\s\S]*\}', response)
        if json_match:
            framework = json.loads(json_match.group())
        else:
            framework = {
                "structure": "",
                "key_concepts": {},
                "main_themes": [],
                "key_figures": {},
            }
    
    print(f"✅ 框架构建完成")
    return framework

def format_framework_context(framework):
    """格式化框架为上下文字符串"""
    context_parts = []
    
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
    
    return "\n\n".join(context_parts)

# ================= 主程序 =================
def main(pdf_path=None, output_dir=None):
    """主函数，可从run.py调用"""
    print("=" * 56)
    print("PDF → 结构化笔记（先OCR后LLM分多轮处理）")
    print("=" * 56)

    if pdf_path is None:
        pdf_path = sys.argv[1].strip() if len(sys.argv) >= 2 else input("📄 PDF: ").strip().strip('"')
    if output_dir is None:
        output_dir = sys.argv[2].strip() if len(sys.argv) >= 3 else input("📁 输出: ").strip().strip('"')

    if not os.path.exists(pdf_path):
        print(f"❌ 不存在"); sys.exit(1)
    os.makedirs(output_dir, exist_ok=True)

    book_title = re.sub(r'^\d+[\[\(]', '', os.path.splitext(os.path.basename(pdf_path))[0])
    book_title = re.sub(r'[\]\)]', '', book_title)
    print(f"\n📖 {book_title}")

    doc = safe_open_pdf(pdf_path)
    total = len(doc)
    print(f"   总页数：{total} 页")

    # ================= 混合提取：文字+图片OCR =================
    print("\n▶ 阶段1/4：提取文本 + 检测图片页...")
    ocr_data = extract_all_text(doc)
    text_pages = [pg for pg, t in ocr_data.items() if t]
    image_pages = find_image_pages(doc)
    blank_pages = [pg for pg in range(total) if is_blank_page(doc, pg)]
    print(f"   📄 有文字：{len(text_pages)} 页，🖼️ 含图片/图形：{len(image_pages)} 页，⬜ 空白：{len(blank_pages)} 页")

    # 含图片的页：渲染+OCR，补充图片中的文字（跳过空白页）
    all_imgs = {}
    need_ocr = [pg for pg in image_pages if pg not in blank_pages and len(ocr_data.get(pg, "")) < 200]
    if need_ocr:
        print(f"\n▶ 阶段1/4：渲染 {len(need_ocr)} 个含图片的页面...")
        for pg in need_ocr:
            try:
                pix = doc[pg].get_pixmap(dpi=150)
                p = os.path.join(tempfile.gettempdir(), f"_pdf_{pg}.png")
                pix.save(p)
                all_imgs[pg] = p
            except Exception as e:
                print(f"   ⚠️ 第{pg+1}页渲染失败: {e}")
        print(f"   ✅ 渲染完成")

        if all_imgs:
            print("\n▶ 阶段2/4：OCR 补充图片中的文字...")
            ocr_result = ocr_all(list(all_imgs.values()))
            for pg, path in all_imgs.items():
                ocr_img_text = ocr_result.get(path, "")
                existing = ocr_data.get(pg, "")
                if len(ocr_img_text) > len(existing):
                    ocr_data[pg] = ocr_img_text
            print(f"   ✅ OCR 补充完成")
    else:
        print("\n▶ 阶段1/4：文字充足或无图片页，跳过OCR")

    # ================= VLM图表分析 =================
    vlm_results = {}
    if all_imgs:
        try:
            from sop_extract import detect_image_pages, analyze_pages_with_vlm, get_vlm_context
            print("\n▶ 阶段2.5/4：VLM图表分析...")
            vlm_image_pages = detect_image_pages(ocr_data, threshold=50)
            if vlm_image_pages:
                print(f"   📊 检测到 {len(vlm_image_pages)} 个可能的图表页面")
                vlm_results = analyze_pages_with_vlm(vlm_image_pages, all_imgs, book_title)
                vlm_context = get_vlm_context(vlm_results)
                if vlm_context:
                    print(f"   ✅ VLM分析完成，生成 {len(vlm_context)} 字描述")
            else:
                print("   📄 未检测到图表页面")
        except ImportError:
            print("\n▶ 阶段2.5/4：VLM模块未导入，跳过图表分析")
        except Exception as e:
            print(f"\n▶ 阶段2.5/4：VLM分析失败 - {e}")

        # 清理临时图片
        for p in all_imgs.values():
            try: os.remove(p)
            except: pass
    else:
        print("\n▶ 阶段2.5/4：无图片页，跳过VLM图表分析")
    doc.close()

    # ================= 构建全局框架 =================
    print("\n▶ 阶段3/4：构建全局框架...")
    framework = build_framework(ocr_data, book_title)
    framework_context = format_framework_context(framework)
    
    # 合并框架和VLM上下文
    try:
        vlm_context = get_vlm_context(vlm_results)
    except NameError:
        vlm_context = ""
    if vlm_context:
        full_context = f"{framework_context}\n\n{vlm_context}"
    else:
        full_context = framework_context
    
    print(f"   框架上下文：{len(full_context)}字")

    # ================= 第三阶段：LLM多轮处理 =================
    print("\n▶ 阶段4/4：LLM 多轮深入思考（逐段处理）...\n")

    chunks = []
    for i in range(0, total, PAGES_PER_CHUNK):
        end = min(i + PAGES_PER_CHUNK, total)
        chunks.append({
            "start": i, "end": end,
            "label": f"第{i//PAGES_PER_CHUNK+1}段（第{i+1}-{end}页）"
        })
    print(f"   共 {len(chunks)} 段，每段 {PAGES_PER_CHUNK} 页\n")

    # 临时目录存放分段文件
    temp_dir = os.path.join(output_dir, "_chunks")

    success = 0
    fail = 0
    prev_ctx = ""
    all_notes = []  # 存储所有笔记用于后续生成后文预览

    for idx, ch in enumerate(chunks, 1):
        label = ch["label"]
        print(f"[{idx}/{len(chunks)}] {label}")

        texts = []
        for pg in range(ch["start"], ch["end"]):
            t = ocr_data.get(pg, "")
            if t.strip():
                texts.append(f"---第{pg+1}页---\n{t}")

        full = "\n".join(texts)
        print(f"   OCR文字：{len(full)}字")

        if len(full) < 80:
            print("   ⏭ 内容过短，跳过\n")
            fail += 1
            continue

        try:
            # 生成后文预览（如果有下一段）
            next_ctx = ""
            if idx < len(chunks):
                next_texts = []
                next_ch = chunks[idx]  # 当前段的索引是idx-1，所以chunks[idx]是下一段
                for pg in range(next_ch["start"], min(next_ch["start"] + 10, next_ch["end"])):
                    t = ocr_data.get(pg, "")
                    if t.strip():
                        next_texts.append(t[:500])
                if next_texts:
                    next_ctx = "\n".join(next_texts)[:1500]
            
            # 使用框架上下文 + 前文摘要
            note = refine_chapter(book_title, label, full, prev_ctx, next_ctx, framework_context)
            save_note_temp(note, temp_dir, idx)
            all_notes.append(note)
            prev_ctx = extract_summary(note)
            success += 1
        except Exception as e:
            print(f"   ❌ {e}")
            fail += 1
        print()

    # 合并所有笔记并清理临时文件
    final_path = merge_and_cleanup(book_title, output_dir, temp_dir)

    print(f"{'='*56}")
    print(f"📊 完成：{success} 段成功，{fail} 段跳过")
    if final_path:
        print(f"   输出：{final_path}")
    else:
        print(f"   输出：{output_dir}")
    print(f"{'='*56}")

    return final_path


if __name__ == "__main__":
    main()
