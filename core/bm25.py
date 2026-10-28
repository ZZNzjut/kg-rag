"""BM25：自己实现（k1=1.5, b=0.75），带进程内 LRU 缓存。

对应参考实现的 _bm25_search：命中为空时回退到文档首段，保证四路融合层始终有数据。
"""
from __future__ import annotations

import math
from collections import Counter, OrderedDict
from typing import Dict, List, Optional, Sequence, Tuple

from . import config
from .text import tokenize


class BM25:
    def __init__(self, corpus_tokens: Sequence[Sequence[str]],
                 k1: float = config.BM25_K1, b: float = config.BM25_B):
        self.k1 = k1
        self.b = b
        self.corpus = [list(t) for t in corpus_tokens]
        self.n = len(self.corpus)
        self.doc_len = [len(t) for t in self.corpus]
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 0.0
        self.tf: List[Counter] = [Counter(t) for t in self.corpus]
        df: Counter = Counter()
        for t in self.corpus:
            df.update(set(t))
        self.idf: Dict[str, float] = {
            term: math.log(1 + (self.n - c + 0.5) / (c + 0.5)) for term, c in df.items()
        }

    def scores(self, query: str) -> List[float]:
        q_tokens = tokenize(query)
        out = [0.0] * self.n
        for term in q_tokens:
            idf = self.idf.get(term)
            if not idf:
                continue
            for i, tf in enumerate(self.tf):
                f = tf.get(term, 0)
                if not f:
                    continue
                dl = self.doc_len[i] or 1
                denom = f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
                out[i] += idf * (f * (self.k1 + 1)) / (denom or 1)
        return out

    def top_k(self, query: str, k: int) -> List[Tuple[int, float]]:
        scored = [(i, s) for i, s in enumerate(self.scores(query)) if s > 0]
        scored.sort(key=lambda x: -x[1])
        return scored[:k]


class BM25Cache:
    """按 chunk id 列表做键的 LRU，避免每次问答重建索引。"""

    def __init__(self, capacity: int = 8):
        self.capacity = capacity
        self._store: "OrderedDict[str, BM25]" = OrderedDict()

    def get_or_build(self, key: str, texts: List[str]) -> BM25:
        if key in self._store:
            self._store.move_to_end(key)
            return self._store[key]
        idx = BM25([tokenize(t) for t in texts])
        self._store[key] = idx
        if len(self._store) > self.capacity:
            self._store.popitem(last=False)
        return idx


bm25_cache = BM25Cache()