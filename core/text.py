"""中文友好的轻量分词与文本工具。

不依赖 jieba：中文用「单字 + 相邻二字」切分（对 BM25 足够稳），
英文/数字按词切；停用词与标点过滤。这样在任何机器上零依赖可跑。
"""
from __future__ import annotations

import hashlib
import re
from typing import List

_CJK = r"\u4e00-\u9fff"
_TOKEN_RE = re.compile(rf"[A-Za-z][A-Za-z0-9_\-\.]*|\d+(?:\.\d+)?|[{_CJK}]+")

STOPWORDS = {
    "的", "了", "是", "在", "和", "与", "或", "及", "等", "对", "为", "于", "中",
    "我", "我们", "你", "他", "她", "它", "这", "那", "这个", "那个", "可以",
    "如何", "什么", "怎么", "哪些", "是否", "需要", "进行", "通过", "根据",
    "以及", "一个", "没有", "如果", "因为", "所以", "但是", "并且", "以上",
    "a", "an", "the", "is", "are", "of", "to", "and", "or", "in", "on", "for",
    "with", "how", "what", "which", "do", "does", "can", "we", "our",
}


def tokenize(text: str) -> List[str]:
    """返回检索用的 token 列表（已去停用词、小写化）。"""
    out: List[str] = []
    for m in _TOKEN_RE.finditer(text or ""):
        tok = m.group(0).lower()
        if re.fullmatch(rf"[{_CJK}]+", tok):
            if len(tok) == 1:
                if tok not in STOPWORDS:
                    out.append(tok)
                continue
            # 中文串：输出二元组，保留单字作为弱信号
            for i in range(len(tok) - 1):
                bg = tok[i:i + 2]
                if bg not in STOPWORDS:
                    out.append(bg)
            if len(tok) >= 2 and tok not in STOPWORDS and len(tok) <= 8:
                out.append(tok)  # 整词，提升专有名词匹配
        else:
            if tok not in STOPWORDS and len(tok) > 1:
                out.append(tok)
    return out


_BULLET_RE = re.compile(r"^\s*(?:[-•·*]|\d+[\.、\)])\s+")


def split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[。！？!?；;\n])", text)
    out = []
    for p in parts:
        p = _BULLET_RE.sub("", p.strip())
        if p:
            out.append(p)
    return out


def normalize_ws(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def stable_hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def text_hash(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def truncate(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"