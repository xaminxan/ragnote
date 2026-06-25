"""条件边函数"""
from .state import BookState
from .config import get_config


def should_continue_chunking(state: BookState) -> str:
    """判断是否继续处理下一段"""
    if state["current_chunk_idx"] < len(state["chunks"]):
        return "next_chunk"
    return "global_review"


def should_fill_gaps(state: BookState) -> str:
    """判断是否需要补充缺失内容"""
    issues = state.get("consistency_issues", [])
    rounds = state.get("global_review_round", 0)
    max_rounds = get_config().processing.max_global_review_rounds
    # 只有存在可定位到具体段落的问题时才修正，且限制轮数
    fixable = sum(1 for i in issues if isinstance(i, dict) and i.get("affected_chunks"))
    if fixable > 0 and rounds < min(max_rounds, 2):
        return "fill_gaps"
    return "merge"
