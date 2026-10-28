"""向量化：默认本地 hashed TF-IDF（零依赖、确定性），可选远程 embedding。

为什么用 hashed TF-IDF 而不是随机伪向量：
1) 不需要 API key 也能跑通四路召回，方便复现；
2) 检索效果与「字符/词袋」语义强相关，对中文企业文档（术语密集）足够；
3) 向量可持久化成 JSON，和 Chroma 的 per-collection 结构一一对应，
   有 key 时替换成 DashScope/OpenAI embedding 只需改一个函数。
"""
from __future__ import annotations

import json
import math
import urllib.request
from typing import Dict, Iterable, List, Optional

from . import config
from .text import tokenize


def _stable_bucket(tok: str, dim: int) -> int:
    h = 0
    for ch in tok:
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    return h % dim


def _sign(tok: str) -> float:
    h = 0
    for ch in tok:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return 1.0 if (h & 1) == 0 else -1.0


class LocalEmbedder:
    """语料级 TF-IDF + hashing 到固定维度，L2 归一化。"""

    def __init__(self, dim: int = config.EMBED_DIM):
        self.dim = dim
        self.idf: Dict[str, float] = {}
        self.n_docs = 0

    def fit(self, docs_tokens: List[List[str]]) -> "LocalEmbedder":
        df: Dict[str, int] = {}
        for toks in docs_tokens:
            for t in set(toks):
                df[t] = df.get(t, 0) + 1
        self.n_docs = max(1, len(docs_tokens))
        self.idf = {t: math.log((1 + self.n_docs) / (1 + c)) + 1.0 for t, c in df.items()}
        return self

    def _tf(self, toks: List[str]) -> Dict[str, float]:
        tf: Dict[str, float] = {}
        for t in toks:
            tf[t] = tf.get(t, 0.0) + 1.0
        return tf

    def transform(self, text: str) -> List[float]:
        toks = tokenize(text)
        tf = self._tf(toks)
        vec = [0.0] * self.dim
        default_idf = 1.0
        for t, c in tf.items():
            w = (1.0 + math.log(c)) * self.idf.get(t, default_idf)
            idx = _stable_bucket(t, self.dim)
            vec[idx] += _sign(t) * w
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    def to_dict(self) -> Dict:
        return {"dim": self.dim, "idf": self.idf, "n_docs": self.n_docs, "kind": "local_tfidf"}

    @classmethod
    def from_dict(cls, d: Dict) -> "LocalEmbedder":
        e = cls(dim=d.get("dim", config.EMBED_DIM))
        e.idf = d.get("idf", {}) or {}
        e.n_docs = d.get("n_docs", 0) or 0
        return e


class RemoteEmbedder:
    """OpenAI 兼容 /embeddings 接口（DashScope compatible-mode 同样适用）。"""

    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def transform(self, text: str) -> List[float]:
        payload = json.dumps({"model": self.model, "input": [text]}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/embeddings",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=config.LLM_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        vec = data["data"][0]["embedding"]
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def fit(self, docs_tokens: List[List[str]]) -> "RemoteEmbedder":
        return self

    def to_dict(self) -> Dict:
        return {"kind": "remote", "model": self.model}


def make_embedder(corpus_texts: Iterable[str]):
    """有远程 embedding 配置就用远程，否则本地 TF-IDF。"""
    if config.EMBED_BASE_URL and config.EMBED_API_KEY:
        return RemoteEmbedder(config.EMBED_BASE_URL, config.EMBED_API_KEY, config.EMBED_MODEL)
    return LocalEmbedder().fit([tokenize(t) for t in corpus_texts])


def cosine(a: List[float], b: List[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    s = 0.0
    for i in range(n):
        s += a[i] * b[i]
    return s if s > 0 else 0.0