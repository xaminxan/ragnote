# VTM - PDF笔记生成工具

将 PDF/视频/网页 转化为结构化笔记，支持繁体竖排中文。

## 功能

- **PDF转笔记** — OCR识别 + LLM分析，生成分段/合并笔记
- **SOP提取** — 从方法论书籍中提取可执行的操作流程
- **批量处理** — 一键处理文件夹内所有PDF
- **视频/网页转笔记** — YouTube、Bilibili视频、任意网页
- **MCP Server** — 供 AI 客户端直接调用
- **FastAPI** — Web/非AI客户端调用

## 快速开始

```bash
# 安装依赖（核心功能）
pip install -r requirements.txt

# 或安装全部依赖（含API服务和MCP Server）
pip install -r requirements-all.txt

# 初始化配置（复制模板并填入你的API密钥）
cp config.example.json config.json

# 查看配置
python main.py config show
```

## 使用方式

### 命令行

```bash
# PDF转笔记
python main.py process book.pdf
python main.py process book.pdf -o notes --pages-per-chunk 20

# SOP提取
python main.py sop book.pdf
python main.py sop book.pdf --remerge

# 批量处理
python main.py batch process ./pdf_folder
python main.py batch sop ./pdf_folder -o output

# 视频/网页转笔记
python main.py clip https://www.bilibili.com/video/BV1xx411c7mD
python main.py clip https://youtube.com/watch?v=xxx
python main.py clip https://example.com/article --inbox "D:\notes"

# 查看帮助
python main.py help
```

### API服务

```bash
# 启动服务
python main.py serve
python main.py serve --port 9000

# 接口文档
http://localhost:8000/docs
```

主要接口：
- `POST /process` — PDF转笔记
- `POST /process/upload` — 上传PDF并处理
- `POST /sop` — SOP提取
- `POST /clip` — 视频/网页转笔记
- `POST /batch` — 批量处理
- `GET /tasks/{task_id}` — 查询任务状态

### MCP Server

```bash
# stdio模式（默认）
python main.py mcp

# SSE模式
python mcp_server.py --transport sse --port 8080
```

MCP工具：
- `process_pdf` — PDF转笔记
- `extract_sop` — SOP提取
- `batch_process` — 批量处理
- `clip_url` — 视频/网页转笔记
- `get_config` / `update_config` — 配置管理

## 配置

编辑 `config.json` 或通过命令行修改：

```bash
python main.py config set llm.model gpt-4
python main.py config set processing.pages_per_chunk 20
python main.py config set output.include_toc true
```

配置项：
- `llm` — LLM模型配置（model, base_url, api_key）
- `vlm` — 视觉语言模型配置
- `ocr` — OCR参数（batch_size, dpi, lang）
- `processing` — 处理参数（pages_per_chunk, checkpoint等）
- `output` — 输出选项（toc, concept_index, timeline）

## 项目结构

```
vtm/
├── main.py          # 统一入口
├── api.py           # FastAPI接口
├── mcp_server.py    # MCP Server
├── config.json      # 配置文件
├── commands/
│   ├── process.py   # PDF转笔记
│   ├── sop.py       # SOP提取
│   └── clip.py      # 视频/网页转笔记
├── core/
│   ├── config.py    # 配置管理
│   ├── graph.py     # LangGraph流程
│   ├── nodes/       # 处理节点
│   └── prompts/     # LLM提示词
└── scripts/         # 辅助脚本
```

## YouTube/Bilibili说明

- YouTube需要 `cookies.txt`（从浏览器导出）
- Bilibili优先获取字幕，无字幕时自动音频转录
- 支持断点续传（`.clip_checkpoint.json`）

## License

MIT
