"""VTM FastAPI 统一接口"""
import os
import sys
import uuid
import time
import shutil
import traceback
from enum import Enum
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, Field

sys.stdout.reconfigure(encoding="utf-8")

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

tasks: dict[str, dict] = {}


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class ProcessRequest(BaseModel):
    pdf_path: str = Field(..., description="PDF文件路径")
    output_dir: Optional[str] = Field(None, description="输出目录")
    pages_per_chunk: Optional[int] = Field(None, description="每段页数")
    no_individual: bool = Field(False, description="不保存分段笔记")
    no_merged: bool = Field(False, description="不保存合并笔记")


class SopRequest(BaseModel):
    pdf_path: str = Field(..., description="PDF文件路径")
    output_dir: Optional[str] = Field(None, description="输出目录")
    remerge: bool = Field(False, description="重新合并已有SOP")


class ClipRequest(BaseModel):
    url: str = Field(..., description="视频/网页URL")
    inbox: Optional[str] = Field(None, description="Obsidian保存目录")


class BatchRequest(BaseModel):
    command: str = Field(..., description="子命令: process 或 sop")
    folder_path: str = Field(..., description="PDF文件夹路径")
    output_dir: Optional[str] = Field(None, description="输出目录")


class ConfigUpdate(BaseModel):
    section: str = Field(..., description="配置段: llm, vlm, ocr, processing, output")
    key: str = Field(..., description="配置键")
    value: str = Field(..., description="配置值")


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="VTM API",
    description="PDF笔记生成工具 - 支持繁体竖排中文，生成结构化笔记",
    version="1.0.0",
    lifespan=lifespan,
)


def _run_task(task_id: str, func, args):
    tasks[task_id]["status"] = TaskStatus.RUNNING
    tasks[task_id]["started_at"] = time.time()
    try:
        func(args, task_id)
        tasks[task_id]["status"] = TaskStatus.DONE
    except Exception as e:
        tasks[task_id]["status"] = TaskStatus.FAILED
        tasks[task_id]["error"] = str(e)[:500]
        tasks[task_id]["traceback"] = traceback.format_exc()[-1000:]
    finally:
        tasks[task_id]["finished_at"] = time.time()


def _process_task(args, task_id: str):
    from commands.process import run as process_run
    process_run(args)


def _sop_task(args, task_id: str):
    from commands.sop import run as sop_run
    sop_run(args)


def _clip_task(args, task_id: str):
    from commands.clip import run as clip_run
    clip_run(args)


def _batch_task(args, task_id: str):
    import time as _time
    import traceback as _tb

    folder_path = args["folder_path"]
    command = args["command"]
    output_dir = args["output_dir"]

    pdf_files = sorted([
        os.path.join(folder_path, f)
        for f in os.listdir(folder_path)
        if f.lower().endswith(".pdf") and os.path.isfile(os.path.join(folder_path, f))
    ])

    if not pdf_files:
        tasks[task_id]["result"] = {"message": "文件夹中没有PDF文件"}
        return

    if command == "sop":
        from commands.sop import run as process_fn
    else:
        from commands.process import run as process_fn

    class BatchArgs:
        pass

    results = {"success": [], "failed": []}
    t_start = _time.time()

    for idx, pdf_path in enumerate(pdf_files, 1):
        filename = os.path.basename(pdf_path)
        tasks[task_id]["progress"] = f"[{idx}/{len(pdf_files)}] {filename}"
        try:
            ba = BatchArgs()
            ba.pdf_path = pdf_path
            ba.output = output_dir
            ba.no_individual = False
            ba.no_merged = False
            ba.pages_per_chunk = None
            process_fn(ba)
            results["success"].append(filename)
        except Exception as e:
            results["failed"].append({"file": filename, "error": str(e)[:200]})

    elapsed = _time.time() - t_start
    tasks[task_id]["result"] = {
        "total": len(pdf_files),
        "success": len(results["success"]),
        "failed": len(results["failed"]),
        "elapsed_seconds": round(elapsed, 1),
        "details": results,
    }


@app.get("/")
async def root():
    return {"name": "VTM API", "version": "1.0.0", "docs": "/docs"}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/process")
async def process_pdf(req: ProcessRequest, background_tasks: BackgroundTasks):
    if not os.path.exists(req.pdf_path):
        raise HTTPException(404, f"文件不存在: {req.pdf_path}")

    task_id = uuid.uuid4().hex[:12]
    output = req.output_dir or os.path.join(os.path.dirname(req.pdf_path), "output")
    os.makedirs(output, exist_ok=True)

    args_dict = {
        "pdf_path": req.pdf_path,
        "output": output,
        "pages_per_chunk": req.pages_per_chunk,
        "no_individual": req.no_individual,
        "no_merged": req.no_merged,
    }

    class Args:
        pass

    a = Args()
    for k, v in args_dict.items():
        setattr(a, k, v)

    tasks[task_id] = {"status": TaskStatus.PENDING, "command": "process", "created_at": time.time()}
    background_tasks.add_task(_run_task, task_id, _process_task, a)

    return {"task_id": task_id, "status": "pending", "output_dir": output}


@app.post("/process/upload")
async def process_pdf_upload(
    file: UploadFile = File(...),
    output_dir: Optional[str] = None,
    pages_per_chunk: Optional[int] = None,
    background_tasks: BackgroundTasks = None,
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "仅支持PDF文件")

    task_id = uuid.uuid4().hex[:12]
    save_path = os.path.join(UPLOAD_DIR, f"{task_id}_{file.filename}")
    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    out = output_dir or os.path.join(OUTPUT_DIR, task_id)
    os.makedirs(out, exist_ok=True)

    class Args:
        pass

    a = Args()
    a.pdf_path = save_path
    a.output = out
    a.pages_per_chunk = pages_per_chunk
    a.no_individual = False
    a.no_merged = False

    tasks[task_id] = {"status": TaskStatus.PENDING, "command": "process", "created_at": time.time()}
    background_tasks.add_task(_run_task, task_id, _process_task, a)

    return {"task_id": task_id, "status": "pending", "output_dir": out}


@app.post("/sop")
async def extract_sop(req: SopRequest, background_tasks: BackgroundTasks):
    if not os.path.exists(req.pdf_path):
        raise HTTPException(404, f"文件不存在: {req.pdf_path}")

    task_id = uuid.uuid4().hex[:12]
    output = req.output_dir or os.path.join(os.path.dirname(req.pdf_path), "output")
    os.makedirs(output, exist_ok=True)

    class Args:
        pass

    a = Args()
    a.pdf_path = req.pdf_path
    a.output = output
    a.remerge = req.remerge
    a.resume = False

    tasks[task_id] = {"status": TaskStatus.PENDING, "command": "sop", "created_at": time.time()}
    background_tasks.add_task(_run_task, task_id, _sop_task, a)

    return {"task_id": task_id, "status": "pending", "output_dir": output}


@app.post("/sop/upload")
async def extract_sop_upload(
    file: UploadFile = File(...),
    output_dir: Optional[str] = None,
    background_tasks: BackgroundTasks = None,
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "仅支持PDF文件")

    task_id = uuid.uuid4().hex[:12]
    save_path = os.path.join(UPLOAD_DIR, f"{task_id}_{file.filename}")
    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    out = output_dir or os.path.join(OUTPUT_DIR, task_id)
    os.makedirs(out, exist_ok=True)

    class Args:
        pass

    a = Args()
    a.pdf_path = save_path
    a.output = out
    a.remerge = False
    a.resume = False

    tasks[task_id] = {"status": TaskStatus.PENDING, "command": "sop", "created_at": time.time()}
    background_tasks.add_task(_run_task, task_id, _sop_task, a)

    return {"task_id": task_id, "status": "pending", "output_dir": out}


@app.post("/clip")
async def clip_url(req: ClipRequest, background_tasks: BackgroundTasks):
    task_id = uuid.uuid4().hex[:12]

    class Args:
        pass

    a = Args()
    a.url = req.url
    a.inbox = req.inbox

    tasks[task_id] = {"status": TaskStatus.PENDING, "command": "clip", "created_at": time.time()}
    background_tasks.add_task(_run_task, task_id, _clip_task, a)

    return {"task_id": task_id, "status": "pending"}


@app.post("/batch")
async def batch_process(req: BatchRequest, background_tasks: BackgroundTasks):
    if not os.path.isdir(req.folder_path):
        raise HTTPException(404, f"文件夹不存在: {req.folder_path}")
    if req.command not in ("process", "sop"):
        raise HTTPException(400, "command 必须为 process 或 sop")

    task_id = uuid.uuid4().hex[:12]
    output = req.output_dir or os.path.join(os.path.dirname(req.folder_path.rstrip("/\\")), "output")
    os.makedirs(output, exist_ok=True)

    tasks[task_id] = {"status": TaskStatus.PENDING, "command": "batch", "created_at": time.time()}
    background_tasks.add_task(
        _run_task, task_id, _batch_task,
        {"command": req.command, "folder_path": req.folder_path, "output_dir": output},
    )

    return {"task_id": task_id, "status": "pending", "output_dir": output}


@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    if task_id not in tasks:
        raise HTTPException(404, "任务不存在")
    t = tasks[task_id]
    return {
        "task_id": task_id,
        "status": t["status"],
        "command": t.get("command"),
        "progress": t.get("progress"),
        "error": t.get("error"),
        "result": t.get("result"),
        "created_at": t.get("created_at"),
        "started_at": t.get("started_at"),
        "finished_at": t.get("finished_at"),
    }


@app.get("/tasks")
async def list_tasks():
    return [
        {"task_id": tid, "status": t["status"], "command": t.get("command")}
        for tid, t in tasks.items()
    ]


@app.get("/config")
async def get_config():
    from core.config import AppConfig
    config = AppConfig.load()
    return config.model_dump()


@app.post("/config")
async def update_config(req: ConfigUpdate):
    from core.config import AppConfig
    config = AppConfig.load()

    section = getattr(config, req.section, None)
    if section is None:
        raise HTTPException(400, f"未知配置段: {req.section}")

    if not hasattr(section, req.key):
        raise HTTPException(400, f"未知配置键: {req.key}")

    old_val = getattr(section, req.key)
    if isinstance(old_val, bool):
        setattr(section, req.key, req.value.lower() in ("true", "1", "yes"))
    elif isinstance(old_val, int):
        setattr(section, req.key, int(req.value))
    elif isinstance(old_val, float):
        setattr(section, req.key, float(req.value))
    else:
        setattr(section, req.key, req.value)

    config.save()
    return {"ok": True, "section": req.section, "key": req.key, "value": getattr(section, req.key)}


@app.get("/output/{task_id}/{filename}")
async def get_output_file(task_id: str, filename: str):
    path = os.path.join(OUTPUT_DIR, task_id, filename)
    if not os.path.exists(path):
        raise HTTPException(404, "文件不存在")
    return FileResponse(path, filename=filename)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
