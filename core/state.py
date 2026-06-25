"""状态定义"""
from typing import TypedDict, Literal


class ConceptNode(TypedDict):
    name: str
    definition: str
    first_seen_chunk: int
    last_seen_chunk: int
    related_concepts: list[str]
    importance: Literal["core", "important", "minor"]


class TopicNode(TypedDict):
    title: str
    chunk_range: list[int]
    subtopics: list[str]
    summary: str


class TimelineEvent(TypedDict):
    period: str
    event: str
    chunk_idx: int


class ConceptRelation(TypedDict):
    source: str
    target: str
    relation_type: Literal["depends_on", "contrasts_with", "extends", "related_to"]
    evidence_chunk: int


class BookFramework(TypedDict):
    structure: str
    key_concepts: dict[str, str]
    main_themes: list[str]
    key_figures: dict[str, str]
    summary_level: Literal["detailed", "medium", "concise"]


class BookKnowledge(TypedDict):
    key_concepts: dict[str, ConceptNode]
    key_figures: dict[str, str]
    topic_hierarchy: list[TopicNode]
    timeline: list[TimelineEvent]
    chunk_summaries: dict[int, str]
    concept_relations: list[ConceptRelation]


class BookIndex(TypedDict):
    chapter_map: dict[str, str]
    concept_dependencies: list[dict]
    argument_flow: str
    cross_chapter_links: list[dict]
    key_frameworks: list[dict]


class BookState(TypedDict):
    pdf_path: str
    output_dir: str
    book_title: str

    # LLM配置覆盖（客户端可指定）
    llm_model: str | None
    llm_base_url: str | None
    llm_api_key: str | None

    ocr_data: dict[int, str]
    vlm_results: dict[int, str]
    page_has_images: dict[int, bool]
    page_direct_text: dict[int, str]
    total_pages: int
    total_chars: int
    framework: BookFramework
    book_index: BookIndex

    chunks: list[dict]
    current_chunk_idx: int
    current_chunk_text: str
    current_chunk_context: str
    current_chunk_label: str
    current_draft: str

    book_knowledge: BookKnowledge
    chunk_notes: dict[int, str]
    processed_chunks: list[int]

    phase: Literal["preprocess", "chunk_process", "global_review", "merge", "done"]
    errors: list[str]
    consistency_issues: list[dict]
    final_output: str
    global_review_round: int

    _all_imgs: dict[int, str]


def create_initial_state(pdf_path: str, output_dir: str, book_title: str,
                         llm_model: str = None, llm_base_url: str = None, llm_api_key: str = None) -> BookState:
    return {
        "pdf_path": pdf_path,
        "output_dir": output_dir,
        "book_title": book_title,
        "llm_model": llm_model,
        "llm_base_url": llm_base_url,
        "llm_api_key": llm_api_key,
        "ocr_data": {},
        "vlm_results": {},
        "page_has_images": {},
        "page_direct_text": {},
        "total_pages": 0,
        "total_chars": 0,
        "framework": {
            "structure": "",
            "key_concepts": {},
            "main_themes": [],
            "key_figures": {},
            "summary_level": "medium",
        },
        "book_index": {
            "chapter_map": {},
            "concept_dependencies": [],
            "argument_flow": "",
            "cross_chapter_links": [],
            "key_frameworks": [],
        },
        "chunks": [],
        "current_chunk_text": "",
        "current_chunk_context": "",
        "current_chunk_label": "",
        "current_draft": "",
        "book_knowledge": {
            "key_concepts": {},
            "key_figures": {},
            "topic_hierarchy": [],
            "timeline": [],
            "chunk_summaries": {},
            "concept_relations": [],
        },
        "chunk_notes": {},
        "processed_chunks": [],
        "phase": "preprocess",
        "errors": [],
        "consistency_issues": [],
        "final_output": "",
        "global_review_round": 0,
        "_all_imgs": {},
    }
