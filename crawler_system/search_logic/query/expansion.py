"""Bilingual Chinese-English Query Expansion and Intent Analysis:
Provides semantic synonyms, bilingual cross-mapping, and live engine suggestion fusion.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional
from ..adapters.bing import BingAdapter
from ..adapters.google import GoogleAdapter


# Known semantic mappings for specialized topics and common technical domains
SEMANTIC_DICTIONARY = {
    # Brain teasers / Lateral thinking puzzles
    "海龟汤": [
        "lateral thinking puzzles",
        "situation puzzles",
        "turtle soup puzzle",
        "情境推理游戏",
        "海龟汤 题目和答案"
    ],
    "lateral thinking puzzle": [
        "海龟汤",
        "situation puzzles",
        "情境推理",
        "lateral thinking puzzles with answers"
    ],
    "lateral thinking puzzles": [
        "海龟汤",
        "situation puzzles",
        "情景推理",
        "lateral thinking puzzles with answers"
    ],
    "situation puzzle": [
        "海龟汤",
        "lateral thinking puzzle",
        "情景推理"
    ],

    # Technical research and papers
    "retrieval augmented generation": [
        "检索增强生成",
        "RAG survey",
        "retrieval-augmented generation benchmark",
        "知识库问答"
    ],
    "rag": [
        "retrieval augmented generation",
        "检索增强生成"
    ],
    "检索增强生成": [
        "retrieval augmented generation",
        "RAG architecture",
        "retrieval-augmented generation"
    ],
    "机器学习": [
        "machine learning",
        "machine learning tutorial",
        "机器学习算法"
    ],
    "machine learning": [
        "机器学习",
        "machine learning algorithms",
        "machine learning course"
    ],
    "深度学习": [
        "deep learning",
        "deep learning neural networks",
        "深度学习实战"
    ],
    "deep learning": [
        "深度学习",
        "deep learning models",
        "deep neural networks"
    ]
}


def is_predominantly_chinese(text: str) -> bool:
    """Check if text contains Chinese characters."""
    return bool(re.search(r"[\u4e00-\u9fa5]", text))


class QueryExpander:
    def __init__(self, use_live_suggestions: bool = True):
        self.use_live_suggestions = use_live_suggestions
        self.google_adapter = GoogleAdapter()
        self.bing_adapter = BingAdapter()

    def expand(self, query: str, max_variants: int = 6) -> List[Dict[str, str]]:
        """Expand user query into bilingual and semantic variants.

        Returns:
            List of dicts: [{"query": ..., "lang": ..., "intent": ..., "source": ...}]
        """
        raw_query = query.strip()
        variants: List[Dict[str, str]] = []
        seen = set()

        def add_variant(q_str: str, lang: str, intent: str, source: str):
            clean = q_str.strip()
            if not clean or clean.casefold() in seen:
                return
            seen.add(clean.casefold())
            variants.append({
                "query": clean,
                "lang": lang,
                "intent": intent,
                "source": source
            })

        # 1. Always retain original query as primary
        is_zh = is_predominantly_chinese(raw_query)
        primary_lang = "zh" if is_zh else "en"
        add_variant(raw_query, primary_lang, "primary", "original")

        # 2. Match semantic dictionary
        normalized_key = raw_query.casefold()
        matched_terms = []
        for dict_key, expansions in SEMANTIC_DICTIONARY.items():
            dk = dict_key.casefold()
            is_dk_zh = is_predominantly_chinese(dk)
            if is_dk_zh:
                if dk == normalized_key or (len(dk) >= 2 and dk in normalized_key):
                    matched_terms.extend(expansions)
            else:
                if dk == normalized_key or bool(re.search(r"\b" + re.escape(dk) + r"\b", normalized_key)):
                    matched_terms.extend(expansions)

        dict_limit = max(1, max_variants - 3) if (self.use_live_suggestions and max_variants >= 4) else (max_variants - 1)
        for term in matched_terms[:dict_limit]:
            t_lang = "zh" if is_predominantly_chinese(term) else "en"
            add_variant(term, t_lang, "cross_lingual_semantic", "dictionary")

        # 3. Live suggestion expansions (from Google and Bing APIs)
        if self.use_live_suggestions and len(variants) < max_variants:
            try:
                bing_suggs = self.bing_adapter.get_suggestions(raw_query, "zh-CN" if is_zh else "en-US")
                for s in bing_suggs:
                    if len(variants) < max_variants - 1:
                        s_lang = "zh" if is_predominantly_chinese(s) else "en"
                        add_variant(s, s_lang, "search_intent_related", "bing_suggestions")
            except Exception:
                pass

            try:
                g_suggs = self.google_adapter.get_suggestions(raw_query, "zh" if is_zh else "en")
                for s in g_suggs:
                    if len(variants) < max_variants:
                        s_lang = "zh" if is_predominantly_chinese(s) else "en"
                        add_variant(s, s_lang, "search_intent_related", "google_suggestions")
            except Exception:
                pass

        return variants[:max_variants]
