"""
VTM - PDF笔记生成工具 v1.0.0
统一入口：python main.py <命令> [参数]
"""
import sys
import os

sys.stdout.reconfigure(encoding="utf-8")

VERSION = "1.0.0"

HELP_TEXT = f"""
VTM - PDF笔记生成工具 v{VERSION}

用法: python main.py <命令> [参数]

可用命令:
  process   PDF转结构化笔记（支持繁体竖排中文）
    参数: <PDF路径> [输出目录]
    示例: python main.py process book.pdf
          python main.py process book.pdf ./output
          python main.py process book.pdf -o notes --pages-per-chunk 20

  sop       从方法论书籍提取标准操作流程(SOP)
    参数: <PDF路径> [输出目录] [--remerge]
    示例: python main.py sop book.pdf
          python main.py sop book.pdf ./output
          python main.py sop book.pdf --remerge (重新合并已提取的SOP部分)

  batch     批量处理文件夹下所有PDF
    参数: <命令> <文件夹路径> [输出目录]
    示例: python main.py batch process <文件夹>          — 批量转笔记
          python main.py batch sop <文件夹>              — 批量SOP提取
          python main.py batch process <文件夹> -o notes — 批量转笔记到指定目录

  clip      YouTube/Bilibili视频/网页 → 结构化笔记
    参数: <URL> [--inbox <Obsidian目录>]
    示例: python main.py clip https://youtube.com/watch?v=xxx
          python main.py clip https://www.bilibili.com/video/BV1xx411c7mD
          python main.py clip https://example.com/article --inbox "D:\\notes"
    
    注意: YouTube视频需要cookies.txt文件（从浏览器导出）
          Bilibili视频优先获取字幕，无字幕时自动音频转录

  serve     启动FastAPI服务（Web/非AI客户端）
    参数: [--host <地址>] [--port <端口>]
    示例: python main.py serve
          python main.py serve --port 9000

  mcp       启动MCP Server（AI客户端专用）
    示例: python main.py mcp

  config    查看/修改配置
    子命令: show, set, init
    示例: python main.py config show
          python main.py config set llm.model gpt-4
          python main.py config init

  help      显示此帮助信息
"""


def print_help():
    print(HELP_TEXT)


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("help", "-h", "--help"):
        print_help()
        return

    command = sys.argv[1]

    if command == "process":
        _run_process()
    elif command == "sop":
        _run_sop()
    elif command == "batch":
        _run_batch()
    elif command == "clip":
        _run_clip()
    elif command == "serve":
        _run_serve()
    elif command == "mcp":
        _run_mcp()
    elif command == "config":
        _run_config()
    else:
        print(f"❌ 未知命令: {command}")
        print("运行 python main.py help 查看可用命令")
        sys.exit(1)


def _run_process():
    from commands.process import run

    if len(sys.argv) < 3:
        print("❌ 缺少参数: python main.py process <PDF路径> [输出目录]")
        sys.exit(1)

    class Args:
        pass

    args = Args()
    i = 2
    args.pdf_path = sys.argv[i]
    i += 1
    args.output = None
    args.no_individual = False
    args.no_merged = False
    args.pages_per_chunk = None

    while i < len(sys.argv):
        if sys.argv[i] == "-o" or sys.argv[i] == "--output":
            i += 1
            args.output = sys.argv[i]
        elif sys.argv[i] == "--no-individual":
            args.no_individual = True
        elif sys.argv[i] == "--no-merged":
            args.no_merged = True
        elif sys.argv[i] == "--pages-per-chunk":
            i += 1
            args.pages_per_chunk = int(sys.argv[i])
        i += 1

    if not os.path.exists(args.pdf_path):
        print(f"❌ 文件不存在: {args.pdf_path}")
        sys.exit(1)
    if not args.output:
        args.output = os.path.join(os.path.dirname(args.pdf_path), "output")

    run(args)


def _run_sop():
    from commands.sop import run

    if len(sys.argv) < 3:
        print("❌ 缺少参数: python main.py sop <PDF路径> [输出目录] [--remerge]")
        sys.exit(1)

    class Args:
        pass

    args = Args()
    args.pdf_path = sys.argv[2]
    args.output = None
    args.resume = False
    args.remerge = False
    
    i = 3
    while i < len(sys.argv):
        if sys.argv[i] in ("-o", "--output"):
            i += 1
            args.output = sys.argv[i]
        elif sys.argv[i] == "--resume":
            pass  # 向后兼容，现在默认自动恢复
        elif sys.argv[i] == "--remerge":
            args.remerge = True
        i += 1
    
    if not os.path.exists(args.pdf_path):
        print(f"❌ 文件不存在: {args.pdf_path}")
        sys.exit(1)
    if not args.output:
        args.output = os.path.join(os.path.dirname(args.pdf_path), "output")

    run(args)


def _run_batch():
    import time
    import traceback

    if len(sys.argv) < 4:
        print("❌ 缺少参数: python main.py batch <process|sop> <文件夹路径> [输出目录]")
        sys.exit(1)

    sub_command = sys.argv[2]
    if sub_command not in ("process", "sop"):
        print(f"❌ 未知子命令: {sub_command}（可选: process, sop）")
        sys.exit(1)

    folder_path = sys.argv[3]
    output_dir = None

    i = 4
    while i < len(sys.argv):
        if sys.argv[i] in ("-o", "--output"):
            i += 1
            output_dir = sys.argv[i]
        i += 1

    if not os.path.isdir(folder_path):
        print(f"❌ 文件夹不存在: {folder_path}")
        sys.exit(1)

    if not output_dir:
        output_dir = os.path.join(os.path.dirname(folder_path.rstrip("/\\")), "output")

    # 收集PDF
    pdf_files = sorted([
        os.path.join(folder_path, f)
        for f in os.listdir(folder_path)
        if f.lower().endswith('.pdf') and os.path.isfile(os.path.join(folder_path, f))
    ])

    if not pdf_files:
        print(f"❌ 文件夹中没有PDF文件: {folder_path}")
        sys.exit(1)

    total = len(pdf_files)
    print("=" * 56)
    print(f"批量{sub_command}模式")
    print(f"📁 源文件夹：{folder_path}")
    print(f"📁 输出目录：{output_dir}")
    print(f"📄 共 {total} 个PDF文件")
    print("=" * 56)

    os.makedirs(output_dir, exist_ok=True)

    # 选择处理函数
    if sub_command == "sop":
        from commands.sop import run as process_fn
    else:
        from commands.process import run as process_fn

    class BatchArgs:
        pass

    results = {"success": [], "failed": []}
    t_start = time.time()

    for idx, pdf_path in enumerate(pdf_files, 1):
        filename = os.path.basename(pdf_path)
        print(f"\n{'─'*56}")
        print(f"[{idx}/{total}] {filename}")
        print(f"{'─'*56}")

        try:
            args = BatchArgs()
            args.pdf_path = pdf_path
            args.output = output_dir
            args.no_individual = False
            args.no_merged = False
            args.pages_per_chunk = None
            process_fn(args)
            results["success"].append(filename)
        except Exception as e:
            print(f"❌ 处理失败: {str(e)[:80]}")
            traceback.print_exc()
            results["failed"].append((filename, str(e)[:100]))

    # 汇总
    elapsed = time.time() - t_start
    minutes = int(elapsed // 60)
    seconds = int(elapsed % 60)

    print(f"\n{'='*56}")
    print(f"📊 批量处理完成")
    print(f"{'='*56}")
    print(f"   总计：{total} 个文件")
    print(f"   成功：{len(results['success'])} 个")
    print(f"   失败：{len(results['failed'])} 个")
    print(f"   耗时：{minutes}分{seconds}秒")
    if results['failed']:
        print(f"\n   ❌ 失败文件列表：")
        for name, err in results['failed']:
            print(f"      - {name}: {err}")
    print(f"   输出目录：{output_dir}")
    print(f"{'='*56}")


def _run_clip():
    from commands.clip import run

    if len(sys.argv) < 3:
        print("❌ 缺少参数: python main.py clip <URL> [--inbox <目录>]")
        sys.exit(1)

    class Args:
        pass

    args = Args()
    args.url = sys.argv[2]
    args.inbox = None
    i = 3
    while i < len(sys.argv):
        if sys.argv[i] == "--inbox":
            i += 1
            args.inbox = sys.argv[i]
        i += 1

    run(args)


def _run_serve():
    host = "0.0.0.0"
    port = 8000
    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--host":
            i += 1
            host = sys.argv[i]
        elif sys.argv[i] == "--port":
            i += 1
            port = int(sys.argv[i])
        i += 1

    print(f"🚀 VTM API 启动中: http://{host}:{port}")
    print(f"📖 文档地址: http://{host}:{port}/docs")

    import uvicorn
    uvicorn.run("api:app", host=host, port=port)


def _run_mcp():
    from mcp_server import mcp
    mcp.run()


def _run_config():
    if len(sys.argv) < 3:
        print("❌ 缺少参数: python main.py config <show|set|init>")
        sys.exit(1)

    action = sys.argv[2]

    if action == "show":
        from core.config import AppConfig
        config = AppConfig.load()
        print("当前配置：")
        print(config.model_dump_json(indent=2))

    elif action == "set":
        if len(sys.argv) < 5:
            print("❌ 用法: python main.py config set <key> <value>")
            sys.exit(1)
        from core.config import AppConfig
        config = AppConfig.load()
        keys = sys.argv[3].split(".")
        obj = config
        for k in keys[:-1]:
            obj = getattr(obj, k)
        setattr(obj, keys[-1], sys.argv[4])
        config.save()
        print(f"✅ 已设置 {sys.argv[3]} = {sys.argv[4]}")

    elif action == "init":
        from core.config import AppConfig
        config = AppConfig()
        config.save()
        print("✅ 已创建默认配置文件 config.json")

    else:
        print(f"❌ 未知操作: {action}（可选: show, set, init）")


if __name__ == "__main__":
    main()
