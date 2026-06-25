"""主图构建"""
from langgraph.graph import StateGraph, START, END

from .state import BookState
from .conditions import should_continue_chunking, should_fill_gaps
from .nodes.preprocess import (
    init_book_state,
    render_pdf_pages,
    run_ocr_all,
    merge_text_sources,
    run_vlm_analysis,
    estimate_length,
    build_framework,
    prepare_chunks,
)
from .nodes.book_index import generate_book_index
from .nodes.chunk_process import (
    prepare_chunk_context,
    generate_draft_note,
    review_knowledge,
    validate_context,
    polish_note,
    update_book_knowledge,
)
from .nodes.global_review import global_consistency_check, fill_knowledge_gaps
from .nodes.merge import save_notes, merge_all_notes


def build_pdf_graph() -> StateGraph:
    graph = StateGraph(BookState)

    # 预处理阶段
    graph.add_node("init_book", init_book_state)
    graph.add_node("render_pages", render_pdf_pages)
    graph.add_node("run_ocr", run_ocr_all)
    graph.add_node("merge_text", merge_text_sources)
    graph.add_node("vlm_analysis", run_vlm_analysis)
    graph.add_node("estimate_length", estimate_length)
    graph.add_node("build_framework", build_framework)
    graph.add_node("book_index", generate_book_index)
    graph.add_node("prepare_chunks", prepare_chunks)

    # 分段处理阶段
    graph.add_node("prepare_chunk", prepare_chunk_context)
    graph.add_node("draft_note", generate_draft_note)
    graph.add_node("review_knowledge", review_knowledge)
    graph.add_node("validate_context", validate_context)
    graph.add_node("polish_note", polish_note)
    graph.add_node("update_knowledge", update_book_knowledge)

    # 全局审查阶段
    graph.add_node("global_check", global_consistency_check)
    graph.add_node("fill_gaps", fill_knowledge_gaps)

    # 合并输出阶段
    graph.add_node("save_notes", save_notes)
    graph.add_node("merge_notes", merge_all_notes)

    # 预处理边
    graph.add_edge(START, "init_book")
    graph.add_edge("init_book", "render_pages")
    graph.add_edge("render_pages", "run_ocr")
    graph.add_edge("run_ocr", "merge_text")
    graph.add_edge("merge_text", "vlm_analysis")
    graph.add_edge("vlm_analysis", "estimate_length")
    graph.add_edge("estimate_length", "build_framework")
    graph.add_edge("build_framework", "book_index")
    graph.add_edge("book_index", "prepare_chunks")
    graph.add_edge("prepare_chunks", "prepare_chunk")

    # 分段处理边
    graph.add_edge("prepare_chunk", "draft_note")
    graph.add_edge("draft_note", "review_knowledge")
    graph.add_edge("review_knowledge", "validate_context")
    graph.add_edge("validate_context", "polish_note")
    graph.add_edge("polish_note", "update_knowledge")

    # 条件边：继续处理下一段 or 进入全局审查
    graph.add_conditional_edges(
        "update_knowledge",
        should_continue_chunking,
        {
            "next_chunk": "prepare_chunk",
            "global_review": "global_check",
        },
    )

    # 全局审查条件边
    graph.add_conditional_edges(
        "global_check",
        should_fill_gaps,
        {
            "fill_gaps": "fill_gaps",
            "merge": "save_notes",
        },
    )
    graph.add_edge("fill_gaps", "global_check")

    # 合并输出
    graph.add_edge("save_notes", "merge_notes")
    graph.add_edge("merge_notes", END)

    return graph
