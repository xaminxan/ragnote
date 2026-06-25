"""预处理节点"""
import os
import sys
import json
import tempfile
import subprocess
import fitz
from langchain_core.messages import HumanMessage
from ..state import BookState
from ..prompts.framework import get_framework_prompt
from .vlm_analysis import detect_image_pages, analyze_pages_with_vlm, get_vlm_context

_PID = os.getpid()


def _get_llm(state: BookState = None):
    """获取LLM实例，优先使用状态中的覆盖配置"""
    from langchain_openai import ChatOpenAI
    from ..config import get_llm_config
    
    # 从状态中获取覆盖配置
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


def init_book_state(state: BookState) -> dict:
    """初始化书籍状态，尝试加载checkpoint"""
    from ..checkpoint import load_checkpoint

    # 尝试加载checkpoint
    checkpoint = load_checkpoint(state["output_dir"], state["pdf_path"])

    if checkpoint:
        # 从checkpoint恢复（补全所有必须字段）
        return {
            "phase": "preprocess",
            "ocr_data": {},
            "vlm_results": {},
            "page_has_images": {},
            "page_direct_text": {},
            "total_pages": 0,
            "total_chars": 0,
            "framework": {
                "structure": "",
                "key_concepts": {},
                "main_themes": [],
                "key_figures": {},
                "summary_level": "medium",
            },
            "book_index": checkpoint.get("book_index", {
                "chapter_map": {},
                "concept_dependencies": [],
                "argument_flow": "",
                "cross_chapter_links": [],
                "key_frameworks": [],
            }),
            "chunks": checkpoint.get("chunks", []),
            "current_chunk_text": "",
            "current_chunk_context": "",
            "current_chunk_label": "",
            "current_draft": "",
            "chunk_notes": checkpoint["chunk_notes"],
            "processed_chunks": checkpoint["processed_chunks"],
            "book_knowledge": checkpoint["book_knowledge"],
            "current_chunk_idx": checkpoint["current_chunk_idx"],
            "errors": [],
            "consistency_issues": [],
            "final_output": "",
            "global_review_round": 0,
            "_all_imgs": {},
        }

    # 正常初始化
    return {
        "phase": "preprocess",
        "ocr_data": {},
        "vlm_results": {},
        "page_has_images": {},
        "page_direct_text": {},
        "total_pages": 0,
        "total_chars": 0,
        "framework": {
            "structure": "",
            "key_concepts": {},
            "main_themes": [],
            "key_figures": {},
            "summary_level": "medium",
        },
        "chunks": [],
        "current_chunk_text": "",
        "current_chunk_context": "",
        "current_chunk_label": "",
        "current_draft": "",
        "chunk_notes": {},
        "processed_chunks": [],
        "book_knowledge": {
            "key_concepts": {},
            "key_figures": {},
            "topic_hierarchy": [],
            "timeline": [],
            "chunk_summaries": {},
            "concept_relations": [],
        },
        "current_chunk_idx": 0,
        "errors": [],
        "consistency_issues": [],
        "final_output": "",
        "global_review_round": 0,
        "_all_imgs": {},
    }


def render_pdf_pages(state: BookState) -> dict:
    """渲染PDF页面为图片，同时检测嵌入式图片和提取直接文本"""
    from ..config import get_config
    cfg = get_config()

    pdf_path = state["pdf_path"]
    doc = fitz.open(pdf_path)
    total = len(doc)

    if total == 0:
        doc.close()
        raise ValueError(f"PDF 文件为空（0页）：{pdf_path}")

    dpi = cfg.ocr.dpi
    render_batch = 50  # 每批渲染页数，控制内存峰值

    # 大PDF提示
    if total > 500:
        print(f"   ⚠️ 大PDF（{total}页），分批渲染中...")

    all_imgs = {}
    page_has_images = {}
    page_direct_text = {}

    for start in range(0, total, render_batch):
        end = min(start + render_batch, total)
        for pg in range(start, end):
            page = doc[pg]

            # 渲染为图片（用于OCR和VLM）
            pix = page.get_pixmap(dpi=dpi)
            p = os.path.join(tempfile.gettempdir(), f"_pdf_{_PID}_{pg}.png")
            pix.save(p)
            all_imgs[pg] = p

            # 检测页面是否包含嵌入式图片
            images = page.get_images(full=True)
            page_has_images[pg] = len(images) > 0

            # 尝试直接提取文本（纯文本PDF的关键优化）
            try:
                direct_text = page.get_text("text").strip()
                page_direct_text[pg] = direct_text
            except Exception:
                page_direct_text[pg] = ""

            if (pg + 1) % 100 == 0:
                print(f"   已处理 {pg+1}/{total}")

    doc.close()

    img_pages = sum(1 for v in page_has_images.values() if v)
    text_pages = sum(1 for v in page_direct_text.values() if len(v.strip()) > 50)
    print(f"   ✅ {total} 页处理完成（{text_pages}页有直接文本，{img_pages}页含嵌入图片）")

    return {
        "total_pages": total,
        "_all_imgs": all_imgs,
        "page_has_images": page_has_images,
        "page_direct_text": page_direct_text,
    }


def run_ocr_all(state: BookState) -> dict:
    """运行OCR识别（带缓存）"""
    from ..config import get_config
    from ..checkpoint import load_ocr_cache, save_ocr_cache
    cfg = get_config()

    output_dir = state["output_dir"]
    pdf_path = state["pdf_path"]
    all_imgs = state["_all_imgs"]

    # 检查是否有足够直接文本（纯文字PDF可跳过OCR）
    page_direct_text = state.get("page_direct_text", {})
    direct_pages = sum(1 for v in page_direct_text.values() if len(v.strip()) > 50)
    total_pages = len(page_direct_text)
    
    if total_pages > 0 and direct_pages > total_pages * 0.5:
        # 超过50%页面有直接文本，跳过OCR
        print(f"   ✅ 检测到纯文字PDF（{direct_pages}/{total_pages}页有直接文本），跳过OCR")
        page_to_text = {}
        for pg in sorted(page_direct_text.keys()):
            page_to_text[pg] = page_direct_text.get(pg, "")
        return {
            "ocr_data": page_to_text,
            "phase": "preprocess",
        }

    # 尝试加载OCR缓存
    cached = load_ocr_cache(output_dir, pdf_path)
    if cached is not None:
        # 验证缓存是否完整（页数匹配）
        if len(cached) == len(all_imgs):
            # 转换为页码key
            page_to_text = {}
            for pg, path in all_imgs.items():
                page_to_text[pg] = cached.get(pg, cached.get(path, ""))
            non_empty = sum(1 for v in page_to_text.values() if v.strip())
            print(f"   ✅ OCR 使用缓存：{non_empty}/{len(all_imgs)} 页有内容")
            return {
                "ocr_data": page_to_text,
                "phase": "preprocess",
            }
        else:
            print(f"   ⚠️ OCR缓存不完整（{len(cached)}页 vs {len(all_imgs)}页），重新OCR")

    # 正常OCR流程
    batch_size = cfg.ocr.batch_size
    ocr_timeout = cfg.ocr.timeout

    ocr_runner = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "ocr_runner.py",
    )
    img_paths = [all_imgs[pg] for pg in sorted(all_imgs.keys())]

    ocr_data = {}
    total = len(img_paths)
    for i in range(0, total, batch_size):
        batch = img_paths[i : i + batch_size]
        bnum = i // batch_size + 1
        bn = (total + batch_size - 1) // batch_size
        print(f"    OCR 批次 {bnum}/{bn}", end="")
        try:
            r = subprocess.run(
                [sys.executable, ocr_runner, json.dumps(batch, ensure_ascii=False)],
                capture_output=True,
                timeout=ocr_timeout,
            )
            out = r.stdout.decode("utf-8", errors="replace")
            ok = False
            if r.returncode == 0 and out.strip():
                for line in reversed(out.strip().split("\n")):
                    try:
                        result = json.loads(line)
                        ocr_data.update(result)
                        ok = True
                        break
                    except Exception:
                        pass
            if not ok:
                for p in batch:
                    ocr_data[p] = ""
            ne = sum(1 for v in ocr_data.values() if v.strip())
            print(f" → OK（{ne}页有内容）")
        except Exception as e:
            print(f" → 失败: {str(e)[:40]}")
            for p in batch:
                ocr_data[p] = ""

    # 转换键为页码
    page_to_text = {}
    for pg, path in all_imgs.items():
        page_to_text[pg] = ocr_data.get(path, "")

    non_empty = sum(1 for v in page_to_text.values() if v.strip())
    print(f"   ✅ OCR 完成：{non_empty}/{len(all_imgs)} 页有内容")

    # 保存OCR缓存
    save_ocr_cache(output_dir, pdf_path, page_to_text)

    return {
        "ocr_data": page_to_text,
        "phase": "preprocess",
    }


def merge_text_sources(state: BookState) -> dict:
    """合并直接文本提取和OCR结果，取更优者"""
    page_direct_text = state.get("page_direct_text", {})
    ocr_data = state["ocr_data"]

    # 如果OCR已被跳过（纯文字PDF），直接使用直接文本
    direct_pages = sum(1 for v in page_direct_text.values() if len(v.strip()) > 50)
    if direct_pages > 0 and not any(v.strip() for v in ocr_data.values()):
        print(f"   ✅ 使用直接提取文本（{direct_pages}页）")
        return {
            "ocr_data": page_direct_text,
            "phase": "preprocess",
        }

    merged = {}
    stats = {"direct": 0, "ocr": 0, "empty": 0}

    for pg in ocr_data:
        direct = page_direct_text.get(pg, "").strip()
        ocr = ocr_data.get(pg, "").strip()

        # 策略：根据内容质量选择最佳来源
        if direct and ocr:
            # 两者都有内容：取更长的（通常更完整）
            if len(direct) >= len(ocr) * 0.8:
                # 直接文本足够好，优先使用（避免OCR引入错字）
                merged[pg] = direct
                stats["direct"] += 1
            else:
                # OCR内容更多（可能是扫描页面的文字被直接提取漏掉了）
                merged[pg] = ocr
                stats["ocr"] += 1
        elif direct:
            merged[pg] = direct
            stats["direct"] += 1
        elif ocr:
            merged[pg] = ocr
            stats["ocr"] += 1
        else:
            merged[pg] = ""
            stats["empty"] += 1

    print(f"   ✅ 文本合并完成：{stats['direct']}页直接提取, {stats['ocr']}页OCR, {stats['empty']}页空白")

    return {
        "ocr_data": merged,
        "phase": "preprocess",
    }


def run_vlm_analysis(state: BookState) -> dict:
    """VLM分析图表页面"""
    ocr_data = state["ocr_data"]
    all_imgs = state["_all_imgs"]
    book_title = state["book_title"]
    page_has_images = state.get("page_has_images", {})

    # 检测需要VLM分析的页面（OCR文字少 或 包含嵌入图片）
    image_pages = detect_image_pages(ocr_data, page_has_images, threshold=50)
    
    if not image_pages:
        print("   📄 未检测到图表页面")
        for p in all_imgs.values():
            try:
                os.remove(p)
            except Exception:
                pass
        return {"vlm_results": {}}

    print(f"   📊 检测到 {len(image_pages)} 个可能的图表页面")
    
    # VLM分析
    vlm_results = analyze_pages_with_vlm(image_pages, all_imgs, book_title, ocr_data=ocr_data)
    
    # 清理临时图片
    for p in all_imgs.values():
        try:
            os.remove(p)
        except Exception:
            pass
    
    vlm_context = get_vlm_context(vlm_results)
    if vlm_context:
        print(f"   ✅ VLM分析完成，生成 {len(vlm_context)} 字描述")

    return {"vlm_results": vlm_results}


def estimate_length(state: BookState) -> dict:
    """估算OCR文本总字数，决定框架策略"""
    ocr_data = state["ocr_data"]
    total_chars = sum(len(text) for text in ocr_data.values())

    if total_chars < 100:
        print(f"   ⚠️ OCR 文本极少（{total_chars}字），可能为纯图片 PDF 或扫描质量差")
    
    if total_chars < 30000:
        summary_level = "detailed"
        print(f"   📊 短篇（{total_chars}字）→ 详细框架")
    elif total_chars < 100000:
        summary_level = "medium"
        print(f"   📊 中篇（{total_chars}字）→ 中等框架")
    else:
        summary_level = "concise"
        print(f"   📊 长篇（{total_chars}字）→ 精简框架")
    
    # 保留现有framework字段，只更新summary_level
    framework = dict(state.get("framework", {}))
    framework["summary_level"] = summary_level
    
    return {
        "total_chars": total_chars,
        "framework": framework,
    }


def build_framework(state: BookState) -> dict:
    """构建全局框架"""
    ocr_data = state["ocr_data"]
    summary_level = state["framework"]["summary_level"]
    book_title = state["book_title"]
    
    print(f"\n▶ 构建全局框架（{summary_level}模式）...")
    
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
        batches.append(full_text[i:i+batch_size])
    
    # 生成各批摘要
    llm = _get_llm(state)
    prompt = get_framework_prompt(summary_level)
    batch_summaries = []
    
    for idx, batch in enumerate(batches):
        print(f"   处理批次 {idx+1}/{len(batches)}...", end=" ")
        messages = [HumanMessage(content=f"书籍：《{book_title}》\n\n{prompt}\n\nOCR文本：\n{batch}")]
        try:
            response = llm.invoke(messages)
            batch_summaries.append(response.content)
            print(f"({len(response.content)}字)")
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
        framework = {
            "structure": "",
            "key_concepts": {},
            "main_themes": [],
            "key_figures": {},
        }
        framework["summary_level"] = summary_level
        return {"framework": framework}
    
    combined = "\n\n".join(valid_summaries)
    merge_prompt = f"""请合并以下书籍各部分的分析结果，生成一个统一的书籍框架。

书籍：《{book_title}》

各部分分析结果：
{combined[:8000]}

{get_framework_prompt(summary_level)}"""
    
    messages = [HumanMessage(content=merge_prompt)]
    try:
        response = llm.invoke(messages)
    except Exception as e:
        error_msg = str(e)
        if "security" in error_msg.lower() or "rejected" in error_msg.lower() or "18" in error_msg:
            print(f"⚠️ 安全过滤，使用合并摘要")
        else:
            print(f"⚠️ 合并框架失败: {error_msg[:50]}，使用合并摘要")
        # 使用合并的摘要作为框架
        framework = {
            "structure": combined[:2000],
            "key_concepts": {},
            "main_themes": [],
            "key_figures": {},
        }
        framework["summary_level"] = summary_level
        return {"framework": framework}
    
    # 解析JSON
    try:
        framework = json.loads(response.content)
    except json.JSONDecodeError:
        import re
        _fallback = {
            "structure": "",
            "key_concepts": {},
            "main_themes": [],
            "key_figures": {},
        }
        matches = list(re.finditer(r'\{[\s\S]*?\}', response.content))
        framework = None
        for m in reversed(matches):
            try:
                parsed = json.loads(m.group())
                if isinstance(parsed, dict) and ("structure" in parsed or "key_concepts" in parsed):
                    framework = parsed
                    break
            except json.JSONDecodeError:
                continue
        if framework is None:
            framework = _fallback
    
    framework["summary_level"] = summary_level
    print(f"✅ 框架构建完成")
    
    return {"framework": framework}


def prepare_chunks(state: BookState) -> dict:
    """准备分段信息（支持章节感知）"""
    # 如果已有chunks（从checkpoint恢复），直接使用
    existing_chunks = state.get("chunks", [])
    if existing_chunks and len(existing_chunks) > 0:
        print(f"   共 {len(existing_chunks)} 段（从checkpoint恢复）\n")
        return {"chunks": existing_chunks, "phase": "chunk_process"}

    from ..config import get_config
    from .chapter_detect import create_chapter_aware_chunks
    cfg = get_config()

    ocr_data = state.get("ocr_data", {})
    total = state["total_pages"]
    pages_per_chunk = cfg.processing.pages_per_chunk

    # 尝试章节感知分段
    if ocr_data:
        try:
            chunks = create_chapter_aware_chunks(
                ocr_data,
                max_chunk_size=pages_per_chunk,
                min_chunk_size=max(10, pages_per_chunk // 3),
            )
            print(f"   共 {len(chunks)} 段（章节感知模式）\n")
            return {"chunks": chunks, "phase": "chunk_process"}
        except Exception as e:
            print(f"   ⚠️ 章节检测失败，回退到简单分段: {str(e)[:50]}")

    # 回退到简单分页模式
    chunks = []
    for i in range(0, total, pages_per_chunk):
        end = min(i + pages_per_chunk, total)
        chunks.append({
            "start": i,
            "end": end,
            "label": f"第{i//pages_per_chunk+1}段（第{i+1}-{end}页）",
            "chapter": "",
            "subchapters": [],
        })

    print(f"   共 {len(chunks)} 段，每段 {pages_per_chunk} 页\n")
    return {"chunks": chunks, "phase": "chunk_process"}
