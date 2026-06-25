"""统一入口"""
import os
import re
import sys
import json
import time
import traceback

sys.stdout.reconfigure(encoding="utf-8")


def extract_title(pdf_path: str) -> str:
    """从PDF文件名提取书名"""
    basename = os.path.splitext(os.path.basename(pdf_path))[0]
    title = re.sub(r"^\d+[\[\(]", "", basename)
    title = re.sub(r"[\]\)]", "", title)
    return title.strip()


def run_langgraph(pdf_path: str, output_dir: str):
    """使用LangGraph运行"""
    from core.graph import build_pdf_graph
    from core.state import create_initial_state

    graph = build_pdf_graph()
    app = graph.compile()

    book_title = extract_title(pdf_path)
    print("=" * 56)
    print("PDF → 结构化笔记（LangGraph模式）")
    print("=" * 56)
    print(f"📖 {book_title}")
    print(f"📄 {pdf_path}")
    print(f"📁 {output_dir}\n")

    initial_state = create_initial_state(pdf_path, output_dir, book_title)
    result = app.invoke(initial_state, config={"recursion_limit": 100})

    print(f"\n{'='*56}")
    print(f"📊 处理完成")
    print(f"   共 {len(result['chunk_notes'])} 段笔记")
    print(f"   核心概念：{len(result['book_knowledge']['key_concepts'])} 个")
    print(f"   关键人物：{len(result['book_knowledge']['key_figures'])} 个")
    print(f"   输出目录：{output_dir}")
    print(f"{'='*56}")


def run_legacy(pdf_path: str, output_dir: str):
    """使用原有实现运行"""
    import pdf_summary
    pdf_summary.main(pdf_path, output_dir)


def run_sop(pdf_path: str, output_dir: str):
    """提取SOP"""
    import sop_extract
    sop_extract.main(pdf_path, output_dir)


def run_batch(mode: str, folder_path: str, output_dir: str):
    """批量处理文件夹下的所有PDF"""
    # 收集所有PDF文件
    pdf_files = sorted([
        os.path.join(folder_path, f)
        for f in os.listdir(folder_path)
        if f.lower().endswith('.pdf') and os.path.isfile(os.path.join(folder_path, f))
    ])

    if not pdf_files:
        print(f"❌ 文件夹中没有找到PDF文件: {folder_path}")
        sys.exit(1)

    total = len(pdf_files)
    print("=" * 56)
    print(f"批量处理模式：{mode}")
    print(f"📁 源文件夹：{folder_path}")
    print(f"📁 输出目录：{output_dir}")
    print(f"📄 共 {total} 个PDF文件")
    print("=" * 56)

    os.makedirs(output_dir, exist_ok=True)

    # 选择处理函数
    if mode == "sop":
        process_fn = run_sop
    elif mode == "legacy":
        process_fn = run_legacy
    else:
        process_fn = run_langgraph

    results = {"success": [], "failed": []}
    t_start = time.time()

    for i, pdf_path in enumerate(pdf_files, 1):
        filename = os.path.basename(pdf_path)
        print(f"\n{'─'*56}")
        print(f"[{i}/{total}] {filename}")
        print(f"{'─'*56}")

        try:
            process_fn(pdf_path, output_dir)
            results["success"].append(filename)
        except Exception as e:
            print(f"❌ 处理失败: {str(e)[:80]}")
            traceback.print_exc()
            results["failed"].append((filename, str(e)[:100]))

    # 汇总报告
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

    # 保存处理报告
    report_path = os.path.join(output_dir, "_batch_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "total": total,
            "success": results["success"],
            "failed": results["failed"],
            "elapsed_seconds": elapsed,
            "mode": mode,
        }, f, ensure_ascii=False, indent=2)


def main():
    if len(sys.argv) < 2:
        print("用法:")
        print("  python run.py langgraph <pdf路径> [输出目录]       — 单文件转笔记")
        print("  python run.py legacy <pdf路径> [输出目录]          — 单文件原有模式")
        print("  python run.py sop <pdf路径> [输出目录]             — 单文件SOP提取")
        print("  python run.py <pdf路径> [输出目录]                 — 默认单文件转笔记")
        print()
        print("  python run.py batch langgraph <文件夹> [输出目录]  — 批量转笔记")
        print("  python run.py batch sop <文件夹> [输出目录]        — 批量SOP提取")
        print("  python run.py batch <文件夹> [输出目录]            — 默认批量转笔记")
        return

    # 解析参数
    if sys.argv[1] == "batch":
        # 批量模式：batch [mode] <文件夹> [输出目录]
        mode = "langgraph"
        folder_path = None
        output_dir = None

        if len(sys.argv) >= 3 and sys.argv[2] in ("langgraph", "legacy", "sop"):
            mode = sys.argv[2]
            if len(sys.argv) >= 4:
                folder_path = sys.argv[3]
            if len(sys.argv) >= 5:
                output_dir = sys.argv[4]
        else:
            if len(sys.argv) >= 3:
                folder_path = sys.argv[2]
            if len(sys.argv) >= 4:
                output_dir = sys.argv[3]

        if not folder_path:
            folder_path = input("📁 PDF文件夹路径: ").strip().strip('"')
        if not output_dir:
            output_dir = input("📁 输出目录 (回车=文件夹同级output): ").strip().strip('"')

        if not os.path.isdir(folder_path):
            print(f"❌ 文件夹不存在: {folder_path}")
            sys.exit(1)

        if not output_dir:
            output_dir = os.path.join(os.path.dirname(folder_path.rstrip("/\\")), "output")

        run_batch(mode, folder_path, output_dir)

    else:
        # 单文件模式
        mode = "langgraph"
        pdf_path = None
        output_dir = None

        if sys.argv[1] in ("langgraph", "legacy", "sop"):
            mode = sys.argv[1]
            if len(sys.argv) >= 3:
                pdf_path = sys.argv[2]
            if len(sys.argv) >= 4:
                output_dir = sys.argv[3]
        else:
            pdf_path = sys.argv[1]
            if len(sys.argv) >= 3:
                output_dir = sys.argv[2]

        if not pdf_path:
            pdf_path = input("📄 PDF路径: ").strip().strip('"')
        if not output_dir:
            output_dir = input("📁 输出目录: ").strip().strip('"')

        if not os.path.exists(pdf_path):
            print(f"❌ 文件不存在: {pdf_path}")
            sys.exit(1)

        if not output_dir:
            output_dir = os.path.join(os.path.dirname(pdf_path), "output")

        if mode == "sop":
            run_sop(pdf_path, output_dir)
        elif mode == "legacy":
            run_legacy(pdf_path, output_dir)
        else:
            run_langgraph(pdf_path, output_dir)


if __name__ == "__main__":
    main()
