"""SOP提取脚本 - 从方法论书籍中提取标准化操作流程"""
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

VLM_API_KEY = "simon123"
VLM_BASE_URL = "http://10.10.10.200:8317/v1"
VLM_MODEL = "agnes-2.0-flash"

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

# ================= VLM =================
_vlm_client = None
def _get_vlm_client():
    global _vlm_client
    if _vlm_client is None:
        _vlm_client = OpenAI(api_key=VLM_API_KEY, base_url=VLM_BASE_URL)
    return _vlm_client

def encode_image(image_path):
    import base64
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def analyze_image_with_vlm(image_path, context=""):
    import base64
    client = _get_vlm_client()
    base64_image = encode_image(image_path)
    prompt = f"""请详细描述这张图片的内容。这是一个书籍PDF中的页面，可能包含：
- 图表、排盘图、示意图
- 表格数据
- 特殊符号或公式
请用简洁的中文描述图片中的关键信息，保留所有重要细节。
{f'背景信息：{context}' if context else ''}"""
    try:
        response = client.chat.completions.create(
            model=VLM_MODEL,
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

def detect_image_pages(ocr_data, threshold=50):
    return [pg for pg, text in ocr_data.items() if len(text.strip()) < threshold]

def analyze_pages_with_vlm(pages_to_analyze, all_imgs, book_title="", batch_size=5):
    import base64
    results = {}
    total = len(pages_to_analyze)
    if total == 0:
        return results
    print(f"   🔍 VLM分析 {total} 个图表页...")
    for i in range(0, total, batch_size):
        batch = pages_to_analyze[i:i+batch_size]
        batch_num = i // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size
        print(f"      批次 {batch_num}/{total_batches}...", end=" ")
        for pg in batch:
            if pg in all_imgs:
                img_path = all_imgs[pg]
                if os.path.exists(img_path):
                    desc = analyze_image_with_vlm(img_path, f"书籍：《{book_title}》第{pg+1}页")
                    results[pg] = desc
        print(f"✅")
    return results

def get_vlm_context(vlm_results):
    if not vlm_results:
        return ""
    parts = ["【图表内容分析】"]
    for pg in sorted(vlm_results.keys()):
        desc = vlm_results[pg]
        if desc and not desc.startswith("[VLM分析失败"):
            parts.append(f"第{pg+1}页图表：{desc[:500]}")
    return "\n".join(parts) if len(parts) > 1 else ""

# ================= OCR =================
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

# ================= SOP提取提示词 =================
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

# ================= 主程序 =================
def main(pdf_path=None, output_dir=None):
    print("=" * 56)
    print("📖 方法论书籍 → 标准操作流程（SOP）提取")
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

    doc = fitz.open(pdf_path)
    total = len(doc)
    print(f"   总页数：{total} 页")

    # ================= 渲染 + OCR =================
    print("\n▶ 阶段1/4：渲染页面...")
    all_imgs = {}
    for pg in range(total):
        pix = doc[pg].get_pixmap(dpi=150)
        p = os.path.join(tempfile.gettempdir(), f"_pdf_{pg}.png")
        pix.save(p)
        all_imgs[pg] = p
        if (pg+1) % 100 == 0:
            print(f"   已渲染 {pg+1}/{total}")
    print(f"   ✅ {len(all_imgs)} 页渲染完成")

    print("\n▶ 阶段2/4：OCR识别...")
    ocr_result = ocr_all(list(all_imgs.values()))
    ocr_data = {}
    for pg, path in all_imgs.items():
        ocr_data[pg] = ocr_result.get(path, "")
    non_empty = sum(1 for v in ocr_data.values() if v.strip())
    print(f"   ✅ OCR 完成：{non_empty}/{total} 页有内容")

    # VLM分析图表
    image_pages = detect_image_pages(ocr_data, threshold=50)
    vlm_results = {}
    if image_pages:
        print(f"   📊 检测到 {len(image_pages)} 个图表页面")
        vlm_results = analyze_pages_with_vlm(image_pages, all_imgs, book_title)
    
    # 清理临时图片
    for p in all_imgs.values():
        try: os.remove(p)
        except: pass
    doc.close()

    # ================= 分段提取SOP =================
    print("\n▶ 阶段3/4：分段提取SOP...\n")

    chunks = []
    for i in range(0, total, PAGES_PER_CHUNK):
        end = min(i + PAGES_PER_CHUNK, total)
        chunks.append({"start": i, "end": end, "label": f"第{i//PAGES_PER_CHUNK+1}段（第{i+1}-{end}页）"})
    print(f"   共 {len(chunks)} 段\n")

    all_sop_parts = []
    
    for idx, ch in enumerate(chunks, 1):
        label = ch["label"]
        print(f"[{idx}/{len(chunks)}] {label}")

        # 获取OCR文本
        texts = []
        for pg in range(ch["start"], ch["end"]):
            t = ocr_data.get(pg, "")
            if t.strip():
                texts.append(f"---第{pg+1}页---\n{t}")
        
        # 添加VLM图表描述
        for pg in range(ch["start"], ch["end"]):
            if pg in vlm_results:
                texts.append(f"---第{pg+1}页图表---\n{vlm_results[pg]}")
        
        full = "\n".join(texts)
        print(f"   内容：{len(full)}字")

        if len(full) < 80:
            print("   ⏭ 内容过短，跳过\n")
            continue

        try:
            sop_part = llm(SYS_SOP_EXTRACT,
                f"书籍：《{book_title}》\n分段：{label}\n\n内容：\n{full[:35000]}\n\n请从这段内容中提取所有可操作的方法论和流程，整理成标准SOP。",
                temp=0.3)
            all_sop_parts.append(sop_part)
            print(f"   ✅ 提取SOP：{len(sop_part)}字")
        except Exception as e:
            print(f"   ❌ {e}")
        print()

    # ================= 合并SOP =================
    print("\n▶ 阶段4/4：合并完整SOP...")
    
    if not all_sop_parts:
        print("   ❌ 未提取到任何SOP内容")
        return

    if len(all_sop_parts) == 1:
        final_sop = all_sop_parts[0]
    else:
        # 分批合并
        merged = all_sop_parts[0]
        for i in range(1, len(all_sop_parts)):
            print(f"   合并 {i}/{len(all_sop_parts)-1}...", end=" ")
            try:
                merged = llm(SYS_SOP_MERGE,
                    f"书籍：《{book_title}》\n\n已合并的内容：\n{merged[:8000]}\n\n新增内容：\n{all_sop_parts[i][:8000]}\n\n请合并为统一的SOP。",
                    temp=0.3)
                print(f"✅ ({len(merged)}字)")
            except Exception as e:
                print(f"⚠️ {str(e)[:30]}")
        final_sop = merged

    # 保存
    safe_title = re.sub(r'[\\/*?:"<>|]', '', book_title).strip()[:80] or "未命名"
    final_path = os.path.join(output_dir, f"{safe_title}_SOP.md")
    with open(final_path, "w", encoding="utf-8") as f:
        f.write(final_sop)

    print(f"\n{'='*56}")
    print(f"📄 SOP提取完成")
    print(f"   输出：{final_path}")
    print(f"   SOP长度：{len(final_sop)}字")
    print(f"{'='*56}")

    return final_path


if __name__ == "__main__":
    main()
