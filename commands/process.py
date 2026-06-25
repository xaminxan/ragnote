"""PDF处理子命令"""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")


def extract_title(pdf_path: str) -> str:
    basename = os.path.splitext(os.path.basename(pdf_path))[0]
    title = re.sub(r"^\d+[\[\(]", "", basename)
    title = re.sub(r"[\]\)]", "", title)
    return title.strip()


def run(args):
    from core.config import AppConfig, set_config
    from core.graph import build_pdf_graph
    from core.state import create_initial_state

    pdf_path = args.pdf_path
    output_dir = args.output

    if not os.path.exists(pdf_path):
        print(f"❌ 文件不存在: {pdf_path}")
        sys.exit(1)

    if not output_dir:
        output_dir = os.path.join(os.path.dirname(pdf_path), "output")

    book_title = extract_title(pdf_path)

    print("=" * 56)
    print("VTM - PDF笔记生成工具")
    print("=" * 56)
    print(f"📖 {book_title}")
    print(f"📄 {pdf_path}")
    print(f"📁 {output_dir}\n")

    config = AppConfig.load()
    if args.pages_per_chunk:
        config.processing.pages_per_chunk = args.pages_per_chunk
    if args.no_individual:
        config.output.save_individual = False
    if args.no_merged:
        config.output.save_merged = False
    set_config(config)

    graph = build_pdf_graph()
    app = graph.compile()
    initial_state = create_initial_state(pdf_path, output_dir, book_title,
                                         llm_model=getattr(args, 'llm_model', None),
                                         llm_base_url=getattr(args, 'llm_base_url', None),
                                         llm_api_key=getattr(args, 'llm_api_key', None))
    result = app.invoke(initial_state, config={"recursion_limit": 100})

    book_index = result.get("book_index", {})
    index_stats = ""
    if book_index:
        chapters = len(book_index.get("chapter_map", {}))
        deps = len(book_index.get("concept_dependencies", []))
        frameworks = len(book_index.get("key_frameworks", []))
        index_stats = f"   全书索引：{chapters}章节, {deps}概念依赖, {frameworks}框架"

    print(f"\n{'='*56}")
    print(f"📊 处理完成")
    print(f"   共 {len(result['chunk_notes'])} 段笔记")
    print(f"   核心概念：{len(result['book_knowledge']['key_concepts'])} 个")
    print(f"   关键人物：{len(result['book_knowledge']['key_figures'])} 个")
    if index_stats:
        print(index_stats)
    print(f"{'='*56}")
