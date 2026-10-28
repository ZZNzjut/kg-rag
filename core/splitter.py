"""切块：按标题分节，节内滑窗（带 overlap），保留标题上下文与位置信息。

等价于 LangChain 的 RecursiveCharacterTextSplitter + MarkdownHeaderTextSplitter 的简化实现，
但把「标题前缀」和「位置比例」显式存下来，供四路召回里的位置路使用。
"""
from __future__ import annotations

import re
from typing import Dict, List

from . import config
from .text import truncate

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def _sections(text: str) -> List[Dict]:
    """把文本按 markdown 标题切成节；无标题时整篇一节。"""
    sections: List[Dict] = []
    cur = {"heading": "", "level": 0, "lines": []}
    for line in text.splitlines():
        m = _HEADING_RE.match(line.strip())
        if m:
            if cur["lines"] or cur["heading"]:
                sections.append(cur)
            cur = {"heading": m.group(2).strip(), "level": len(m.group(1)), "lines": []}
        else:
            cur["lines"].append(line)
    if cur["lines"] or cur["heading"]:
        sections.append(cur)
    # 合并空节
    return [s for s in sections if "".join(s["lines"]).strip() or s["heading"]]


def _window(lines: List[str], heading: str) -> List[str]:
    """节内按行累加，超过 CHUNK_SIZE 就切，带 overlap 回退。"""
    chunks: List[str] = []
    buf: List[str] = []
    buf_len = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 超长行（表格行/没有标点的段落）硬切
        while len(line) > config.CHUNK_SIZE:
            if buf:
                chunks.append("\n".join(buf))
                buf, buf_len = [], 0
            chunks.append(line[: config.CHUNK_SIZE])
            line = line[config.CHUNK_SIZE - config.CHUNK_OVERLAP :]
        buf.append(line)
        buf_len += len(line) + 1
        if buf_len >= config.CHUNK_SIZE:
            chunks.append("\n".join(buf))
            tail = "\n".join(buf)
            keep = tail[-config.CHUNK_OVERLAP :] if config.CHUNK_OVERLAP else ""
            buf = [keep] if keep else []
            buf_len = len(keep)
    if buf:
        chunks.append("\n".join(buf))
    out = [c for c in chunks if len(c.strip()) >= config.MIN_CHUNK_CHARS]
    if not out and chunks:
        # 整篇都短于 MIN_CHUNK_CHARS（短通知/短条款）：合并成一块，别丢
        joined = "\n".join(c.strip() for c in chunks if c.strip())
        out = [joined] if joined else []
    return out


def split_document(doc_id: str, title: str, text: str) -> List[Dict]:
    """返回 chunk 列表：[{chunk_id, doc_id, doc_title, heading, text, pos}]"""
    sections = _sections(text)
    total = max(1, len(sections))
    chunks: List[Dict] = []
    idx = 0
    for si, sec in enumerate(sections):
        body = "\n".join(sec["lines"]).strip()
        if not body:
            continue
        heading = sec["heading"]
        pieces = _window(body.splitlines(), heading)
        for pi, piece in enumerate(pieces):
            idx += 1
            prefix = f"《{title}》"
            if heading:
                prefix += f" · {heading}"
            chunks.append({
                "chunk_id": f"{doc_id}-{idx:03d}",
                "doc_id": doc_id,
                "doc_title": title,
                "heading": heading,
                "text": piece,
                "display": truncate(piece.replace("\n", " "), 120),
                # 位置比例 0~1：用于位置路（首尾段通常信息密度最高）
                "pos": round(si / total, 4),
                "prefix": prefix,
                "index_in_doc": idx,
            })
    return chunks