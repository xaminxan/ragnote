"""断点续传模块"""
import os
import json
import hashlib
from typing import Optional


def _checkpoint_path(output_dir: str, pdf_path: str) -> str:
    """根据PDF路径生成checkpoint文件路径"""
    size = os.path.getsize(pdf_path) if os.path.exists(pdf_path) else 0
    key = f"{pdf_path}_{size}"
    h = hashlib.md5(key.encode()).hexdigest()[:12]
    return os.path.join(output_dir, f".checkpoint_{h}.json")


def _ocr_cache_path(output_dir: str, pdf_path: str) -> str:
    """根据PDF路径生成OCR缓存文件路径"""
    size = os.path.getsize(pdf_path) if os.path.exists(pdf_path) else 0
    key = f"ocr_{pdf_path}_{size}"
    h = hashlib.md5(key.encode()).hexdigest()[:12]
    return os.path.join(output_dir, f".ocr_cache_{h}.json")


def save_ocr_cache(output_dir: str, pdf_path: str, ocr_data: dict):
    """保存OCR结果缓存"""
    path = _ocr_cache_path(output_dir, pdf_path)
    os.makedirs(output_dir, exist_ok=True)

    # 将int key转为str以便JSON序列化
    serializable = {str(k): v for k, v in ocr_data.items()}

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False)
    except Exception as e:
        print(f"   ⚠️ 保存OCR缓存失败: {str(e)[:50]}")


def load_ocr_cache(output_dir: str, pdf_path: str) -> Optional[dict]:
    """加载OCR结果缓存，返回None表示无可用缓存"""
    path = _ocr_cache_path(output_dir, pdf_path)
    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            cache = json.load(f)

        # 恢复int key
        result = {int(k): v for k, v in cache.items()}
        non_empty = sum(1 for v in result.values() if v.strip())
        print(f"   📂 发现OCR缓存：{len(result)}页（{non_empty}页有内容）")
        return result
    except Exception as e:
        print(f"   ⚠️ 加载OCR缓存失败: {str(e)[:50]}")
        return None


def save_checkpoint(output_dir: str, pdf_path: str, state: dict):
    """保存当前处理进度"""
    from .config import get_config
    if not get_config().processing.enable_checkpoint:
        return

    path = _checkpoint_path(output_dir, pdf_path)
    os.makedirs(output_dir, exist_ok=True)

    # 只保存必要字段，避免文件过大
    checkpoint = {
        "pdf_path": pdf_path,
        "processed_chunks": state.get("processed_chunks", []),
        "chunk_notes": {str(k): v for k, v in state.get("chunk_notes", {}).items()},
        "book_knowledge": state.get("book_knowledge", {}),
        "book_index": state.get("book_index", {}),
        "current_chunk_idx": state.get("current_chunk_idx", 0),
        "chunks": state.get("chunks", []),
    }

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(checkpoint, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"   ⚠️ 保存checkpoint失败: {str(e)[:50]}")


def load_checkpoint(output_dir: str, pdf_path: str) -> Optional[dict]:
    """加载之前的处理进度，返回None表示无可用checkpoint"""
    from .config import get_config
    if not get_config().processing.enable_checkpoint:
        return None

    path = _checkpoint_path(output_dir, pdf_path)
    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            checkpoint = json.load(f)

        # 验证是同一个PDF
        if checkpoint.get("pdf_path") != pdf_path:
            return None

        # 恢复chunk_notes的int key
        chunk_notes = {int(k): v for k, v in checkpoint.get("chunk_notes", {}).items()}

        processed = checkpoint.get("processed_chunks", [])
        idx = checkpoint.get("current_chunk_idx", 0)

        if processed:
            print(f"   📂 发现checkpoint：已处理 {len(processed)} 段，从第{idx+1}段继续")

        return {
            "processed_chunks": processed,
            "chunk_notes": chunk_notes,
            "book_knowledge": checkpoint.get("book_knowledge", {}),
            "book_index": checkpoint.get("book_index", {}),
            "current_chunk_idx": idx,
            "chunks": checkpoint.get("chunks", []),
        }
    except Exception as e:
        print(f"   ⚠️ 加载checkpoint失败: {str(e)[:50]}")
        return None


def clear_checkpoint(output_dir: str, pdf_path: str):
    """处理完成后清除checkpoint"""
    path = _checkpoint_path(output_dir, pdf_path)
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass
