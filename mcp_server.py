"""VTM MCP Server - AI工具接口"""
import os
import sys
import json
import time
import uuid
import shutil
import traceback
from typing import Optional

sys.stdout.reconfigure(encoding="utf-8")

from fastmcp import FastMCP

mcp = FastMCP(
    "VTM",
    instructions="PDF笔记生成工具 - 支持繁体竖排中文，生成结构化笔记/SOP",
)

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)


def _run_process(pdf_path: str, output_dir: str, pages_per_chunk: int = None,
                 no_individual: bool = False, no_merged: bool = False,
                 llm_model: str = None, llm_base_url: str = None, llm_api_key: str = None) -> dict:
    from commands.process import run as process_run

    class Args:
        pass

    args = Args()
    args.pdf_path = pdf_path
    args.output = output_dir
    args.pages_per_chunk = pages_per_chunk
    args.no_individual = no_individual
    args.no_merged = no_merged
    args.llm_model = llm_model
    args.llm_base_url = llm_base_url
    args.llm_api_key = llm_api_key

    process_run(args)
    return {"status": "done", "output_dir": output_dir}


def _run_sop(pdf_path: str, output_dir: str, remerge: bool = False,
             llm_model: str = None, llm_base_url: str = None, llm_api_key: str = None) -> dict:
    from commands.sop import run as sop_run

    class Args:
        pass

    args = Args()
    args.pdf_path = pdf_path
    args.output = output_dir
    args.remerge = remerge
    args.resume = False
    args.llm_model = llm_model
    args.llm_base_url = llm_base_url
    args.llm_api_key = llm_api_key

    sop_run(args)
    return {"status": "done", "output_dir": output_dir}


def _run_clip(url: str, inbox: str = None,
              llm_model: str = None, llm_base_url: str = None, llm_api_key: str = None) -> dict:
    from commands.clip import run as clip_run

    class Args:
        pass

    args = Args()
    args.url = url
    args.inbox = inbox
    args.llm_model = llm_model
    args.llm_base_url = llm_base_url
    args.llm_api_key = llm_api_key

    clip_run(args)
    return {"status": "done"}


@mcp.tool()
def process_pdf(
    pdf_path: str,
    output_dir: str = "",
    pages_per_chunk: int = 0,
    no_individual: bool = False,
    no_merged: bool = False,
    llm_model: str = "",
    llm_base_url: str = "",
    llm_api_key: str = "",
) -> str:
    """PDF转结构化笔记（支持繁体竖排中文）。

    Args:
        pdf_path: PDF文件的完整路径
        output_dir: 输出目录，留空则在PDF同目录下创建output文件夹
        pages_per_chunk: 每段处理的页数，0表示使用默认值(30)
        no_individual: 是否不保存分段笔记
        no_merged: 是否不保存合并笔记
        llm_model: 可选，覆盖默认LLM模型名称
        llm_base_url: 可选，覆盖默认LLM API地址
        llm_api_key: 可选，覆盖默认LLM API密钥

    Returns:
        处理结果摘要
    """
    if not os.path.exists(pdf_path):
        return f"错误: 文件不存在 {pdf_path}"

    if not output_dir:
        output_dir = os.path.join(os.path.dirname(pdf_path), "output")
    os.makedirs(output_dir, exist_ok=True)

    try:
        result = _run_process(
            pdf_path, output_dir,
            pages_per_chunk=pages_per_chunk if pages_per_chunk > 0 else None,
            no_individual=no_individual,
            no_merged=no_merged,
            llm_model=llm_model or None,
            llm_base_url=llm_base_url or None,
            llm_api_key=llm_api_key or None,
        )
        return f"处理完成\n输出目录: {output_dir}"
    except Exception as e:
        return f"处理失败: {str(e)}"


@mcp.tool()
def extract_sop(
    pdf_path: str,
    output_dir: str = "",
    remerge: bool = False,
    llm_model: str = "",
    llm_base_url: str = "",
    llm_api_key: str = "",
) -> str:
    """从方法论书籍PDF提取标准操作流程(SOP)。

    从PDF中识别操作步骤、判断方法、分析流程，整理成可执行的SOP文档。

    Args:
        pdf_path: PDF文件的完整路径
        output_dir: 输出目录，留空则在PDF同目录下创建output文件夹
        remerge: 是否重新合并已提取的SOP部分
        llm_model: 可选，覆盖默认LLM模型名称
        llm_base_url: 可选，覆盖默认LLM API地址
        llm_api_key: 可选，覆盖默认LLM API密钥

    Returns:
        处理结果摘要
    """
    if not os.path.exists(pdf_path):
        return f"错误: 文件不存在 {pdf_path}"

    if not output_dir:
        output_dir = os.path.join(os.path.dirname(pdf_path), "output")
    os.makedirs(output_dir, exist_ok=True)

    try:
        result = _run_sop(pdf_path, output_dir, remerge=remerge,
                          llm_model=llm_model or None,
                          llm_base_url=llm_base_url or None,
                          llm_api_key=llm_api_key or None)
        return f"SOP提取完成\n输出目录: {output_dir}"
    except Exception as e:
        return f"SOP提取失败: {str(e)}"


@mcp.tool()
def batch_process(
    command: str,
    folder_path: str,
    output_dir: str = "",
    llm_model: str = "",
    llm_base_url: str = "",
    llm_api_key: str = "",
) -> str:
    """批量处理文件夹下所有PDF。

    Args:
        command: 处理类型 - "process"(转笔记) 或 "sop"(提取SOP)
        folder_path: 包含PDF文件的文件夹路径
        output_dir: 输出目录，留空则在上级目录创建output文件夹
        llm_model: 可选，覆盖默认LLM模型名称
        llm_base_url: 可选，覆盖默认LLM API地址
        llm_api_key: 可选，覆盖默认LLM API密钥

    Returns:
        批量处理结果摘要
    """
    if not os.path.isdir(folder_path):
        return f"错误: 文件夹不存在 {folder_path}"
    if command not in ("process", "sop"):
        return "错误: command 必须为 process 或 sop"

    pdf_files = sorted([
        f for f in os.listdir(folder_path)
        if f.lower().endswith(".pdf") and os.path.isfile(os.path.join(folder_path, f))
    ])

    if not pdf_files:
        return f"文件夹中没有PDF文件: {folder_path}"

    if not output_dir:
        output_dir = os.path.join(os.path.dirname(folder_path.rstrip("/\\")), "output")
    os.makedirs(output_dir, exist_ok=True)

    if command == "sop":
        from commands.sop import run as process_fn
    else:
        from commands.process import run as process_fn

    class BatchArgs:
        pass

    results = {"success": [], "failed": []}
    t_start = time.time()

    for filename in pdf_files:
        pdf_path = os.path.join(folder_path, filename)
        try:
            ba = BatchArgs()
            ba.pdf_path = pdf_path
            ba.output = output_dir
            ba.no_individual = False
            ba.no_merged = False
            ba.pages_per_chunk = None
            ba.llm_model = llm_model or None
            ba.llm_base_url = llm_base_url or None
            ba.llm_api_key = llm_api_key or None
            process_fn(ba)
            results["success"].append(filename)
        except Exception as e:
            results["failed"].append(f"{filename}: {str(e)[:100]}")

    elapsed = time.time() - t_start
    summary = (
        f"批量{command}完成\n"
        f"总计: {len(pdf_files)} | 成功: {len(results['success'])} | 失败: {len(results['failed'])}\n"
        f"耗时: {int(elapsed // 60)}分{int(elapsed % 60)}秒\n"
        f"输出目录: {output_dir}"
    )

    if results["failed"]:
        summary += "\n失败文件:\n" + "\n".join(f"  - {f}" for f in results["failed"])

    return summary


@mcp.tool()
def clip_url(url: str, inbox: str = "",
             llm_model: str = "", llm_base_url: str = "", llm_api_key: str = "") -> str:
    """视频/网页/音频转结构化笔记。

    支持YouTube、Bilibili视频（自动获取字幕或音频转录），任意网页内容，以及本地音频文件。

    Args:
        url: 视频/网页URL，或本地音频文件/文件夹路径（支持mp3, wav, m4a等）
        inbox: Obsidian笔记保存目录，留空则使用默认目录
        llm_model: 可选，覆盖默认LLM模型名称
        llm_base_url: 可选，覆盖默认LLM API地址
        llm_api_key: 可选，覆盖默认LLM API密钥

    Returns:
        处理结果摘要
    """
    try:
        result = _run_clip(url, inbox=inbox if inbox else None,
                           llm_model=llm_model or None,
                           llm_base_url=llm_base_url or None,
                           llm_api_key=llm_api_key or None)
        return f"笔记生成完成"
    except Exception as e:
        return f"处理失败: {str(e)}"


@mcp.tool()
def get_config() -> str:
    """获取当前VTM配置。

    返回LLM、VLM、OCR、处理参数、输出选项等所有配置信息。

    Returns:
        JSON格式的配置信息
    """
    from core.config import AppConfig
    config = AppConfig.load()
    return config.model_dump_json(indent=2)


@mcp.tool()
def update_config(section: str, key: str, value: str) -> str:
    """修改VTM配置。

    Args:
        section: 配置段 - llm, vlm, ocr, processing, output
        key: 配置键名
        value: 新值（会自动转换为对应类型）

    Returns:
        修改结果
    """
    from core.config import AppConfig
    config = AppConfig.load()

    section_obj = getattr(config, section, None)
    if section_obj is None:
        return f"错误: 未知配置段 {section}"
    if not hasattr(section_obj, key):
        return f"错误: 未知配置键 {key}"

    old_val = getattr(section_obj, key)
    if isinstance(old_val, bool):
        setattr(section_obj, key, value.lower() in ("true", "1", "yes"))
    elif isinstance(old_val, int):
        setattr(section_obj, key, int(value))
    elif isinstance(old_val, float):
        setattr(section_obj, key, float(value))
    else:
        setattr(section_obj, key, value)

    config.save()
    return f"已设置 {section}.{key} = {getattr(section_obj, key)}"


@mcp.resource("config://vtm")
def get_config_resource() -> str:
    """返回VTM当前配置（作为MCP资源）。"""
    from core.config import AppConfig
    config = AppConfig.load()
    return config.model_dump_json(indent=2)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    if args.transport == "sse":
        mcp.run(transport="sse", host=args.host, port=args.port)
    else:
        mcp.run()
