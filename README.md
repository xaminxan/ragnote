# VTM - 智能笔记生成工具

将 PDF、视频、网页 转化为结构化笔记，专为知识管理设计。

## 为什么选择 VTM

- **繁体竖排中文原生支持** — OCR 识别古籍、竖排文献，自动判断文字方向
- **智能笔记流程** — 基于 LangGraph 的多阶段处理：预处理 → 分段生成 → 全局审查 → 合并输出
- **断点续传** — 处理中断后自动从上次位置继续，不重复工作
- **多源输入** — PDF、YouTube、Bilibili、任意网页，统一输出格式
- **Obsidian 集成** — 笔记自动保存到 Obsidian 仓库，支持自定义目录
- **批量处理** — 文件夹内所有 PDF 或播放列表/合集一键处理
- **MCP Server** — 可作为 AI 工具直接调用

## 快速开始

```bash
# 安装依赖（核心功能）
pip install -r requirements.txt

# 复制配置模板并填入你的 API 密钥
cp config.example.json config.json
```

## 使用方式

### 1. PDF 转笔记

```bash
# 单个 PDF
python main.py process book.pdf

# 指定输出目录和每段页数
python main.py process book.pdf -o notes --pages-per-chunk 20

# 批量处理文件夹内所有 PDF
python main.py batch process ./pdf_folder
python main.py batch process ./pdf_folder -o output
```

### 2. SOP 提取

从方法论书籍中提取可执行的操作流程：

```bash
# 单个 PDF
python main.py sop book.pdf

# 批量提取
python main.py batch sop ./pdf_folder

# 重新合并已提取的 SOP
python main.py sop book.pdf --remerge
```

### 3. 视频/网页/音频转笔记

```bash
# Bilibili 视频（自动获取字幕，无字幕时音频转录）
python main.py clip https://www.bilibili.com/video/BV1xx411c7mD

# YouTube 视频
python main.py clip https://youtube.com/watch?v=xxx

# 任意网页
python main.py clip https://example.com/article

# 本地音频文件（转录+总结）
python main.py clip D:\audio\lecture.mp3

# 本地音频文件夹（批量处理）
python main.py clip D:\audio\ --inbox "D:\notes"
```

**支持的音频格式：** mp3, wav, m4a, ogg, flac, wma

**批量处理播放列表/合集：**

```bash
# YouTube 播放列表（自动检测并批量处理）
python main.py clip "https://youtube.com/watch?v=xxx&list=PLxxxx"

# Bilibili 合集（自动检测并批量处理）
python main.py clip https://www.bilibili.com/video/BVxxx?sid=xxx
```

**断点续传：** 处理中断后，重新运行相同命令会自动跳过已完成的项目。

### 4. API 服务

```bash
# 启动 Web 服务
python main.py serve
python main.py serve --port 9000

# 接口文档
http://localhost:8000/docs
```

主要接口：
| 接口 | 说明 |
|------|------|
| `POST /process` | PDF 转笔记 |
| `POST /process/upload` | 上传 PDF 并处理 |
| `POST /sop` | SOP 提取 |
| `POST /clip` | 视频/网页转笔记 |
| `POST /batch` | 批量处理 |
| `GET /tasks/{id}` | 查询任务状态 |

### 5. MCP Server

供 AI 客户端（如 Cursor、Claude）直接调用：

```bash
# stdio 模式（默认）
python main.py mcp

# SSE 模式
python mcp_server.py --transport sse --port 8080
```

MCP 工具：
- `process_pdf` — PDF 转笔记
- `extract_sop` — SOP 提取
- `batch_process` — 批量处理
- `clip_url` — 视频/网页转笔记
- `get_config` / `update_config` — 配置管理

## 配置

编辑 `config.json` 或通过命令行修改：

```bash
python main.py config show
python main.py config set llm.model gpt-4
python main.py config set processing.pages_per_chunk 20
```

| 配置段 | 说明 |
|--------|------|
| `llm` | LLM 模型配置（model, base_url, api_key） |
| `vlm` | 视觉语言模型配置 |
| `ocr` | OCR 参数（batch_size, dpi, lang） |
| `processing` | 处理参数（pages_per_chunk, checkpoint 等） |
| `output` | 输出选项（toc, concept_index, timeline） |
| `obsidian` | Obsidian 笔记保存路径 |

## 项目结构

```
vtm/
├── main.py          # 统一入口
├── api.py           # FastAPI 接口
├── mcp_server.py    # MCP Server
├── config.json      # 配置文件（不提交到 git）
├── commands/
│   ├── process.py   # PDF 转笔记
│   ├── sop.py       # SOP 提取
│   └── clip.py      # 视频/网页转笔记
├── core/
│   ├── config.py    # 配置管理
│   ├── graph.py     # LangGraph 处理流程
│   ├── nodes/       # 处理节点
│   └── prompts/     # LLM 提示词
└── scripts/         # 辅助脚本
```

## YouTube/Bilibili 说明

- YouTube 需要 `cookies.txt`（从浏览器导出）
- Bilibili 优先获取字幕，无字幕时自动音频转录（需安装 whisper）
- 支持合集、播放列表、分P视频的批量处理
- 内置限流保护，避免被平台封禁

## License

MIT
