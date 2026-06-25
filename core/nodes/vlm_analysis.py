"""VLM图表分析模块"""
import base64
import os
from openai import OpenAI


_vlm_client = None


def _get_vlm_client():
    global _vlm_client
    if _vlm_client is None:
        from ..config import get_vlm_config
        cfg = get_vlm_config()
        _vlm_client = OpenAI(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            timeout=60,
        )
    return _vlm_client


def encode_image(image_path):
    """将图片编码为base64"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def analyze_image_with_vlm(image_path, context="", page_type="auto"):
    """使用VLM分析图片内容"""
    from ..config import get_vlm_config
    cfg = get_vlm_config()
    client = _get_vlm_client()
    base64_image = encode_image(image_path)

    # 根据页面类型调整提示词
    type_hints = {
        "chart": "这是一张图表/数据图。请提取图表的标题、坐标轴标签、数据趋势、关键数值。",
        "diagram": "这是一张示意图/流程图。请描述图中的各个组件及其关系。",
        "table": "这是一个表格。请提取表格的行列结构和所有数据内容。",
        "photo": "这是一张照片/插图。请描述图中的主要视觉元素。",
        "formula": "这是公式/方程。请准确识别所有数学符号和公式内容。",
        "score": "这是一个排盘/命盘图。请提取所有宫位、星曜、天干地支等信息。",
    }

    prompt = f"""请详细描述这张图片的内容。这是一个书籍PDF中的页面，可能包含：
- 图表、排盘图、示意图
- 表格数据
- 特殊符号或公式
- 任何视觉信息

请用简洁的中文描述图片中的关键信息，保留所有重要细节。
{f'页面类型提示：{type_hints.get(page_type, "")}' if page_type != "auto" else ""}
{f'背景信息：{context}' if context else ''}"""

    try:
        response = client.chat.completions.create(
            model=cfg.model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{base64_image}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=cfg.max_tokens,
            temperature=cfg.temperature,
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"[VLM分析失败: {str(e)[:50]}]"


def detect_image_pages(ocr_data, page_has_images=None, threshold=50):
    """检测需要VLM分析的页面：
    1. OCR文字较少的页面（可能是纯图表页）
    2. 包含嵌入式图片的页面（混合页面中的图表）
    """
    image_pages = []
    for pg, text in ocr_data.items():
        is_text_light = len(text.strip()) < threshold
        has_embedded_img = page_has_images.get(pg, False) if page_has_images else False

        # 需要VLM分析的情况：
        # - 纯图表页（OCR文字少）
        # - 混合页面（有嵌入图片，无论OCR文字多少）
        if is_text_light or has_embedded_img:
            image_pages.append(pg)

    return sorted(set(image_pages))


def guess_page_type(ocr_text):
    """根据OCR文本特征猜测页面类型"""
    text = ocr_text.strip().lower()
    if not text:
        return "auto"

    # 排盘/命盘关键词
    score_keywords = ["宫", "命盘", "紫微", "天府", "天相", "天梁", "天同", "七杀", "破军",
                      "贪狼", "太阴", "太阳", "巨门", "天机", "廉贞", "武曲"]
    if any(k in text for k in score_keywords):
        return "score"

    # 表格特征：多个竖线分隔或制表符
    if text.count("|") > 5 or text.count("\t") > 5:
        return "table"

    # 公式特征
    formula_chars = ["=", "∑", "∫", "√", "∞", "±", "×", "÷", "²", "³"]
    if sum(1 for c in formula_chars if c in text) >= 2:
        return "formula"

    # 图表特征
    chart_keywords = ["图", "表", "数据", "趋势", "比例", "百分比", "%"]
    if any(k in text for k in chart_keywords):
        return "chart"

    return "auto"


def analyze_pages_with_vlm(pages_to_analyze, all_imgs, book_title="", ocr_data=None, batch_size=5):
    """批量分析页面"""
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
                    # 猜测页面类型以优化提示词
                    page_type = "auto"
                    if ocr_data:
                        page_type = guess_page_type(ocr_data.get(pg, ""))
                    desc = analyze_image_with_vlm(img_path, f"书籍：《{book_title}》第{pg+1}页", page_type)
                    results[pg] = desc
        
        print(f"✅")
    
    return results


def get_vlm_context(vlm_results):
    """将VLM分析结果格式化为上下文"""
    if not vlm_results:
        return ""
    
    parts = ["【图表内容分析】"]
    for pg in sorted(vlm_results.keys()):
        desc = vlm_results[pg]
        if desc and not desc.startswith("[VLM分析失败"):
            parts.append(f"第{pg+1}页图表：{desc[:500]}")
    
    return "\n".join(parts) if len(parts) > 1 else ""
