"""SOP提取子命令"""
import os
import re
import sys
import json
import tempfile
import subprocess
import fitz
import time
from openai import OpenAI

sys.stdout.reconfigure(encoding="utf-8")

PAGES_PER_CHUNK = 30
MAX_RETRIES = 3
RETRY_DELAY = 5


def _get_config():
    from core.config import get_config
    return get_config()


def _llm(client, system, user, temp=0.3, timeout=180, model_override=None):
    config = _get_config()
    model = model_override or config.llm.model
    r = client.chat.completions.create(
        model=model,
        temperature=temp,
        timeout=timeout,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return r.choices[0].message.content


def _llm_with_retry(client, system, user, temp=0.3, timeout=180, max_retries=MAX_RETRIES, model_override=None):
    for attempt in range(max_retries):
        try:
            return _llm(client, system, user, temp, timeout, model_override)
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"   ⚠️ 重试 {attempt + 1}/{max_retries}...")
                time.sleep(RETRY_DELAY)
            else:
                raise


def _load_checkpoint(checkpoint_path):
    if os.path.exists(checkpoint_path):
        with open(checkpoint_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _save_checkpoint(checkpoint_path, data):
    with open(checkpoint_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _save_intermediate_results(output_dir, book_title, merged, all_sop_parts, phase, step=None):
    safe_title = re.sub(r'[\\/*?:"<>|]', "", book_title).strip()[:80] or "未命名"
    intermediate_dir = os.path.join(output_dir, "_intermediate")
    os.makedirs(intermediate_dir, exist_ok=True)
    
    # 保存合并结果
    merged_path = os.path.join(intermediate_dir, f"{safe_title}_merged.md")
    with open(merged_path, "w", encoding="utf-8") as f:
        f.write(merged)
    
    # 保存所有SOP部分
    parts_dir = os.path.join(intermediate_dir, "parts")
    os.makedirs(parts_dir, exist_ok=True)
    for i, part in enumerate(all_sop_parts):
        part_path = os.path.join(parts_dir, f"part_{i}.md")
        with open(part_path, "w", encoding="utf-8") as f:
            f.write(part)
    
    # 保存状态信息
    status_path = os.path.join(intermediate_dir, "status.json")
    status = {
        "book_title": book_title,
        "phase": phase,
        "step": step,
        "total_parts": len(all_sop_parts),
        "merged_length": len(merged) if merged else 0,
        "failed_parts": [],
        "timestamp": time.time()
    }
    with open(status_path, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    
    print(f"   💾 中间结果已保存到: {intermediate_dir}")


def remerge(output_dir, book_title, llm_model=None, llm_base_url=None, llm_api_key=None):
    """从中间结果重新合并"""
    from core.config import get_llm_config
    
    safe_title = re.sub(r'[\\/*?:"<>|]', "", book_title).strip()[:80] or "未命名"
    checkpoint_path = os.path.join(output_dir, f"{safe_title}_checkpoint.json")
    
    checkpoint = _load_checkpoint(checkpoint_path)
    if not checkpoint:
        print("❌ 未找到检查点文件")
        return
    
    all_sop_parts = checkpoint.get("all_sop_parts", [])
    if not all_sop_parts:
        print("❌ 检查点中没有SOP部分")
        return
    
    print(f"📖 重新合并: {book_title}")
    print(f"   共 {len(all_sop_parts)} 个部分")
    
    config = _get_config()
    llm_config = get_llm_config(llm_model, llm_base_url, llm_api_key)
    client = OpenAI(api_key=llm_config.api_key, base_url=llm_config.base_url)
    
    # 合并所有部分
    merged = all_sop_parts[0]
    for i in range(1, len(all_sop_parts)):
        print(f"   合并 {i+1}/{len(all_sop_parts)-1}...", end=" ")
        try:
            merged = _llm_with_retry(
                client, SYS_SOP_MERGE,
                f"书籍：《{book_title}》\n\n已合并的内容：\n{merged[:8000]}\n\n新增内容：\n{all_sop_parts[i][:8000]}\n\n请合并为统一的SOP。",
                temp=config.llm.temperature,
                max_retries=5,
                model_override=llm_model,
            )
            print(f"✅ ({len(merged)}字)")
            
            # 更新检查点
            checkpoint["merged"] = merged
            checkpoint["merge_progress"] = i + 1
            _save_checkpoint(checkpoint_path, checkpoint)
            
        except Exception as e:
            print(f"❌ 失败: {str(e)[:50]}")
            print(f"   💾 已保存当前进度，可稍后继续")
            break
    
    # 保存最终结果
    final_path = os.path.join(output_dir, f"{safe_title}_SOP.md")
    with open(final_path, "w", encoding="utf-8") as f:
        f.write(merged)
    
    print(f"\n{'='*56}")
    print(f"📄 重新合并完成")
    print(f"   输出：{final_path}")
    print(f"   SOP长度：{len(merged)}字")
    print(f"{'='*56}")


def _encode_image(image_path):
    import base64
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _analyze_image_with_vlm(client, image_path, context=""):
    import base64
    from core.config import get_config
    config = get_config()
    base64_image = _encode_image(image_path)
    prompt = f"""请详细描述这张图片的内容。这是一个书籍PDF中的页面，可能包含：
- 图表、排盘图、示意图
- 表格数据
- 特殊符号或公式
请用简洁的中文描述图片中的关键信息，保留所有重要细节。
{f'背景信息：{context}' if context else ''}"""
    try:
        response = client.chat.completions.create(
            model="agnes-2.0-flash",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_image}"}}
                ]
            }],
            max_tokens=1000,
            temperature=0.3,
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"[VLM分析失败: {str(e)[:50]}]"


def _ocr_all(img_paths, batch_size=20, timeout=600):
    runner = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ocr_runner.py")
    if not os.path.exists(runner):
        runner = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "ocr_runner.py")
    result = {}
    total = len(img_paths)
    for i in range(0, total, batch_size):
        batch = img_paths[i:i + batch_size]
        bnum = i // batch_size + 1
        bn = (total + batch_size - 1) // batch_size
        print(f"    OCR 批次 {bnum}/{bn}", end="")
        try:
            r = subprocess.run(
                [sys.executable, runner, json.dumps(batch, ensure_ascii=False)],
                capture_output=True, timeout=timeout,
            )
            out = r.stdout.decode("utf-8", errors="replace")
            ok = False
            if r.returncode == 0 and out.strip():
                for line in reversed(out.strip().split("\n")):
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


SYS_SOP_EXTRACT = """你是一位方法论提炼专家。请从以下书籍内容中提取标准化操作流程（SOP）。

要求：
1. 识别书中描述的所有操作步骤、判断方法、分析流程
2. 将散落各处的方法论整理成清晰的、可执行的步骤
3. 每个步骤都要具体、可操作，不要抽象描述
4. 包含判断条件、分支逻辑、特殊情况处理
5. 如果有多个分析维度，按维度分别整理SOP
6. 保留所有关键细节（如口诀、公式、判断标准）

输出格式：

# {书名} — 标准操作流程（SOP）

## 一、总体分析框架
（简述整体分析思路，3-5句话）

## 二、核心SOP流程

### SOP 1：[流程名称]
**目的**：这个流程解决什么问题
**前提条件**：执行前需要什么信息

**步骤：**
1. **步骤名称**
   - 操作：具体做什么
   - 判断：如何判断/选择
   - 输出：产生什么结果
2. ...

**特殊情况处理：**
- 情况1：如何处理
- 情况2：如何处理

**口诀/记忆要点：**
- 关键口诀或记忆方法

### SOP 2：[流程名称]
...

## 三、关键判断标准速查表
| 判断项 | 标准A | 标准B | 结论 |
|--------|-------|-------|------|

## 四、常见错误与注意事项
1. 错误做法 → 正确做法
2. ...

## 五、核心口诀汇总
1. 口诀1：内容
2. 口诀2：内容
"""

SYS_SOP_MERGE = """你是一位方法论整合专家。请将以下多段SOP分析结果合并为一个完整、统一的标准操作流程。

要求：
1. 去除重复内容，合并相似流程
2. 确保流程完整，不遗漏任何步骤
3. 统一术语和格式
4. 按照实际操作顺序重新组织
5. 确保每个SOP都是可独立执行的

输出格式与单段SOP相同，但内容是完整的全书SOP。"""


def run(args):
    from core.config import get_config, get_llm_config

    pdf_path = args.pdf_path
    output_dir = args.output
    remerge_mode = getattr(args, "remerge", False)
    llm_model = getattr(args, "llm_model", None)
    llm_base_url = getattr(args, "llm_base_url", None)
    llm_api_key = getattr(args, "llm_api_key", None)

    if not os.path.exists(pdf_path):
        print(f"❌ 不存在: {pdf_path}")
        sys.exit(1)

    config = get_config()
    os.makedirs(output_dir, exist_ok=True)

    book_title = re.sub(r"^\d+[\[\(]", "", os.path.splitext(os.path.basename(pdf_path))[0])
    book_title = re.sub(r"[\]\)]", "", book_title)

    # 如果是重新合并模式，直接执行重新合并
    if remerge_mode:
        remerge(output_dir, book_title, llm_model, llm_base_url, llm_api_key)
        return

    # 检查点路径
    safe_title = re.sub(r'[\\/*?:"<>|]', "", book_title).strip()[:80] or "未命名"
    checkpoint_path = os.path.join(output_dir, f"{safe_title}_checkpoint.json")
    
    # 加载检查点（自动检测）
    checkpoint = _load_checkpoint(checkpoint_path)
    if checkpoint:
        print("🔄 检测到已有检查点，自动恢复处理...")
        print(f"   检查点时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(checkpoint['timestamp']))}")
    
    print("=" * 56)
    print("📖 方法论书籍 → 标准操作流程（SOP）提取")
    print("=" * 56)
    print(f"\n📖 {book_title}")

    doc = fitz.open(pdf_path)
    total = len(doc)
    print(f"   总页数：{total} 页")

    # 使用客户端覆盖的LLM配置
    llm_config = get_llm_config(llm_model, llm_base_url, llm_api_key)
    client = OpenAI(api_key=llm_config.api_key, base_url=llm_config.base_url)

    # 阶段1: 渲染
    if checkpoint and "ocr_data" in checkpoint:
        print("\n▶ 阶段1/4：渲染页面... (从检查点跳过)")
    else:
        print("\n▶ 阶段1/4：渲染页面...")
        all_imgs = {}
        for pg in range(total):
            pix = doc[pg].get_pixmap(dpi=config.ocr.dpi)
            p = os.path.join(tempfile.gettempdir(), f"_pdf_{pg}.png")
            pix.save(p)
            all_imgs[pg] = p
            if (pg + 1) % 100 == 0:
                print(f"   已渲染 {pg+1}/{total}")
        print(f"   ✅ {len(all_imgs)} 页渲染完成")

    # 阶段2: OCR
    if checkpoint and "ocr_data" in checkpoint:
        print("\n▶ 阶段2/4：OCR识别... (从检查点恢复)")
        ocr_data = {int(k): v for k, v in checkpoint["ocr_data"].items()}
        non_empty = sum(1 for v in ocr_data.values() if v.strip())
        print(f"   ✅ OCR 完成：{non_empty}/{total} 页有内容")
    else:
        print("\n▶ 阶段2/4：OCR识别...")
        ocr_result = _ocr_all(list(all_imgs.values()), config.ocr.batch_size, config.ocr.timeout)
        ocr_data = {}
        for pg, path in all_imgs.items():
            ocr_data[pg] = ocr_result.get(path, "")
        non_empty = sum(1 for v in ocr_data.values() if v.strip())
        print(f"   ✅ OCR 完成：{non_empty}/{total} 页有内容")
        
        # 保存检查点
        _save_checkpoint(checkpoint_path, {
            "pdf_path": pdf_path,
            "book_title": book_title,
            "ocr_data": ocr_data,
            "timestamp": time.time()
        })

    # VLM分析图表
    if checkpoint and "vlm_results" in checkpoint:
        print("   📊 VLM图表分析... (从检查点恢复)")
        vlm_results = {int(k): v for k, v in checkpoint["vlm_results"].items()}
    else:
        image_pages = [pg for pg, text in ocr_data.items() if len(text.strip()) < 50]
        vlm_results = {}
        if image_pages:
            print(f"   📊 检测到 {len(image_pages)} 个图表页面")
            print(f"   🔍 VLM分析中...")
            for pg in image_pages:
                if pg in all_imgs and os.path.exists(all_imgs[pg]):
                    desc = _analyze_image_with_vlm(client, all_imgs[pg], f"书籍：《{book_title}》第{pg+1}页")
                    vlm_results[pg] = desc
        
        # 更新检查点
        checkpoint_data = _load_checkpoint(checkpoint_path) or {}
        checkpoint_data["vlm_results"] = vlm_results
        _save_checkpoint(checkpoint_path, checkpoint_data)

    # 清理临时图片
    if not checkpoint or "ocr_data" not in checkpoint:
        for p in all_imgs.values():
            try:
                os.remove(p)
            except:
                pass
    doc.close()

    # 阶段3: 分段提取SOP
    if checkpoint and "all_sop_parts" in checkpoint:
        print("\n▶ 阶段3/4：分段提取SOP... (从检查点恢复)")
        all_sop_parts = checkpoint["all_sop_parts"]
        print(f"   已恢复 {len(all_sop_parts)} 个SOP部分")
    else:
        print("\n▶ 阶段3/4：分段提取SOP...\n")
        chunks = []
        for i in range(0, total, PAGES_PER_CHUNK):
            end = min(i + PAGES_PER_CHUNK, total)
            chunks.append({"start": i, "end": end, "label": f"第{i // PAGES_PER_CHUNK + 1}段（第{i+1}-{end}页）"})
        print(f"   共 {len(chunks)} 段\n")

        all_sop_parts = []
        for idx, ch in enumerate(chunks, 1):
            label = ch["label"]
            print(f"[{idx}/{len(chunks)}] {label}")

            texts = []
            for pg in range(ch["start"], ch["end"]):
                t = ocr_data.get(pg, "")
                if t.strip():
                    texts.append(f"---第{pg+1}页---\n{t}")
            for pg in range(ch["start"], ch["end"]):
                if pg in vlm_results:
                    texts.append(f"---第{pg+1}页图表---\n{vlm_results[pg]}")

            full = "\n".join(texts)
            print(f"   内容：{len(full)}字")

            if len(full) < 80:
                print("   ⏭ 内容过短，跳过\n")
                continue

            try:
                sop_part = _llm_with_retry(
                    client, SYS_SOP_EXTRACT,
                    f"书籍：《{book_title}》\n分段：{label}\n\n内容：\n{full[:35000]}\n\n请从这段内容中提取所有可操作的方法论和流程，整理成标准SOP。",
                    temp=config.llm.temperature,
                    model_override=llm_model,
                )
                all_sop_parts.append(sop_part)
                print(f"   ✅ 提取SOP：{len(sop_part)}字")
            except Exception as e:
                print(f"   ❌ {e}")
            print()

        # 保存检查点
        checkpoint_data = _load_checkpoint(checkpoint_path) or {}
        checkpoint_data["all_sop_parts"] = all_sop_parts
        _save_checkpoint(checkpoint_path, checkpoint_data)

    # 阶段4: 合并SOP
    print("\n▶ 阶段4/4：合并完整SOP...")

    if not all_sop_parts:
        print("   ❌ 未提取到任何SOP内容")
        return

    if len(all_sop_parts) == 1:
        final_sop = all_sop_parts[0]
    else:
        # 检查是否有合并进度
        merged = None
        merge_progress = 0
        if checkpoint and "merged" in checkpoint and "merge_progress" in checkpoint:
            merged = checkpoint["merged"]
            merge_progress = checkpoint["merge_progress"]
            print(f"   从检查点恢复合并进度: {merge_progress}/{len(all_sop_parts)-1}")

        if merged is None:
            merged = all_sop_parts[0]
            merge_progress = 0

        failed_parts = []  # 记录失败的部分索引
        for i in range(merge_progress, len(all_sop_parts)):
            print(f"   合并 {i+1}/{len(all_sop_parts)-1}...", end=" ")
            try:
                merged = _llm_with_retry(
                    client, SYS_SOP_MERGE,
                    f"书籍：《{book_title}》\n\n已合并的内容：\n{merged[:8000]}\n\n新增内容：\n{all_sop_parts[i][:8000]}\n\n请合并为统一的SOP。",
                    temp=config.llm.temperature,
                    max_retries=3,
                    model_override=llm_model,
                )
                print(f"✅ ({len(merged)}字)")
                
                # 保存中间结果
                _save_intermediate_results(output_dir, book_title, merged, all_sop_parts, "merging", i)
                
                # 更新检查点
                checkpoint_data = _load_checkpoint(checkpoint_path) or {}
                checkpoint_data["merged"] = merged
                checkpoint_data["merge_progress"] = i + 1
                _save_checkpoint(checkpoint_path, checkpoint_data)
                
            except Exception as e:
                print(f"⚠️ 重试3次后仍然失败: {str(e)[:50]}")
                failed_parts.append(i)
                # 保存当前合并结果和失败信息
                checkpoint_data = _load_checkpoint(checkpoint_path) or {}
                checkpoint_data["merged"] = merged
                checkpoint_data["merge_progress"] = i + 1
                checkpoint_data["failed_parts"] = failed_parts
                _save_checkpoint(checkpoint_path, checkpoint_data)
                _save_intermediate_results(output_dir, book_title, merged, all_sop_parts, "partial", i)
                print(f"   💾 已保存当前进度，将在最后统一处理失败部分")
        
        # 如果有失败的部分，尝试单独合并
        if failed_parts:
            print(f"\n   ⚠️ 有 {len(failed_parts)} 个部分合并失败，尝试单独合并...")
            for idx in failed_parts:
                print(f"   重新合并第 {idx+1} 部分...", end=" ")
                try:
                    merged = _llm_with_retry(
                        client, SYS_SOP_MERGE,
                        f"书籍：《{book_title}》\n\n已合并的内容：\n{merged[:8000]}\n\n新增内容：\n{all_sop_parts[idx][:8000]}\n\n请合并为统一的SOP。",
                        temp=config.llm.temperature,
                        max_retries=5,
                        model_override=llm_model,
                    )
                    print(f"✅ ({len(merged)}字)")
                    
                    # 更新检查点
                    checkpoint_data = _load_checkpoint(checkpoint_path) or {}
                    checkpoint_data["merged"] = merged
                    checkpoint_data["failed_parts"] = [f for f in failed_parts if f != idx]
                    _save_checkpoint(checkpoint_path, checkpoint_data)
                except Exception as e:
                    print(f"❌ 仍然失败: {str(e)[:50]}")
        
        final_sop = merged

    # 保存最终结果
    final_path = os.path.join(output_dir, f"{safe_title}_SOP.md")
    with open(final_path, "w", encoding="utf-8") as f:
        f.write(final_sop)

    # 清理检查点文件（可选，保留以便调试）
    # if os.path.exists(checkpoint_path):
    #     os.remove(checkpoint_path)

    print(f"\n{'='*56}")
    print(f"📄 SOP提取完成")
    print(f"   输出：{final_path}")
    print(f"   SOP长度：{len(final_sop)}字")
    if os.path.exists(os.path.join(output_dir, "_intermediate")):
        print(f"   💾 中间结果保存在: {os.path.join(output_dir, '_intermediate')}")
    print(f"{'='*56}")
