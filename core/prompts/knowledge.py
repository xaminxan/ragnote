"""知识提取提示词"""

# 注意：SYS_KNOWLEDGE_EXTRACT 和 SYS_GLOBAL_CHECK 均通过 .format() 调用，
# JSON 示例中的花括号必须双写 {{ }} 以避免 KeyError。

SYS_KNOWLEDGE_EXTRACT = """分析以下笔记，提取全书级别知识图谱信息。

已有概念列表（已在列表中的概念无需重复添加，只需在 new_relations 中更新关联关系）：
{existing_concepts}

笔记内容：
{note_content}

请提取本段新增的知识图谱信息，输出JSON格式（不要包含其他文字）：

{{
    "new_concepts": [
        {{"name": "概念名", "definition": "定义（来自笔记原文）", "importance": "core|important|minor"}}
    ],
    "new_figures": {{"人物名": "角色/简介（来自笔记原文）"}},
    "new_relations": [
        {{"source": "概念A", "target": "概念B", "relation_type": "depends_on|contrasts_with|extends|related_to"}}
    ],
    "timeline_events": [
        {{"period": "时间/年代", "event": "事件描述"}}
    ],
    "chunk_summary": "本段核心内容摘要（300字以内，概括主要论点和关键知识点）"
}}

提取规则：
- 只提取笔记中有明确依据的内容，不要推断或补充原文没有的内容
- new_concepts：仅提取本段首次出现或有新定义的概念；已在 existing_concepts 中的概念不要重复添加
- importance 判断标准：core=全书反复强调的核心概念，important=本章重要概念，minor=一般术语
- new_figures：仅本段出现的新人物；若无新人物则输出 {{}}
- new_relations：仅当两个概念之间有明确逻辑关系时才填写；relation_type 必须是 depends_on/contrasts_with/extends/related_to 之一
- timeline_events：仅当笔记中有明确时间/年代记录时才填写，否则输出空数组 []"""


SYS_GLOBAL_CHECK = """作为学术审稿人，审查以下分段笔记的全书一致性。

书籍：《{book_title}》

核心概念：{core_concepts}

笔记片段：
{notes_preview}

请检查以下四类一致性问题：
1. concept_conflict：同一概念在不同段落的定义相互矛盾
2. figure_inconsistency：同一人物的身份、立场或时代描述前后不符
3. timeline_conflict：时间线、年代或事件先后顺序有冲突
4. term_drift：同一事物在不同段落使用了不同术语，可能造成混淆

仅报告确实存在的问题，合理的概念延伸或细节补充不算问题。
输出JSON数组，每项包含以下字段。若无问题则输出空数组 []。
不要包含其他文字，只输出JSON。

[
  {{
    "type": "concept_conflict|figure_inconsistency|timeline_conflict|term_drift",
    "description": "问题的具体描述，指出哪些段落有冲突",
    "affected_chunks": ["第X段", "第Y段"],
    "suggestion": "建议的修正方向"
  }}
]"""
