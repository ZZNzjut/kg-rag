"""问答编排：四路召回 → 上下文拼装 → 生成（LLM 或抽取式兜底）→ 引用溯源。

没有 LLM key 时用「抽取式答案」：从命中的块里挑与问题词重叠最高的句子，
并按 [n] 标注来源。这样离线也能给出可验证的答案，而不是假装在生成。
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from typing import Dict, List, Optional

from . import config, retrieval, store
from .text import split_sentences, text_hash, tokenize, truncate


def build_context(chunks: List[Dict]) -> List[Dict]:
    cites = []
    for i, c in enumerate(chunks, 1):
        cites.append({
            "n": i,
            "doc_id": c.get("doc_id"),
            "doc_title": c.get("doc_title", ""),
            "chunk_id": c.get("chunk_id"),
            "heading": c.get("heading", ""),
            "text": c.get("text", ""),
        })
    return cites


def _context_text(cites: List[Dict], limit: int = 6000) -> str:
    buf, used = [], 0
    for c in cites:
        seg = f"[{c['n']}] 《{c['doc_title']}》{('·' + c['heading']) if c['heading'] else ''}\n{c['text']}"
        if used + len(seg) > limit:
            break
        buf.append(seg)
        used += len(seg)
    return "\n\n".join(buf)


def _best_sentences(question: str, cites: List[Dict], max_sent: int = 4) -> List[Dict]:
    q_tokens = set(tokenize(question))
    sentences = []
    df: Dict[str, int] = {}
    for c in cites:
        for s in split_sentences(c["text"]):
            if len(s) < 12:
                continue
            toks = set(tokenize(s))
            sentences.append((c, s, toks))
            for t in toks:
                df[t] = df.get(t, 0) + 1
    scored = []
    seen = set()
    for c, s, toks in sentences:
        norm = re.sub(r"\s+", "", s)
        if norm in seen:  # 切块 overlap 会重复句子
            continue
        overlap = q_tokens & toks
        if not overlap:
            continue
        seen.add(norm)
        # 稀有词加权：命中「标准」这种到处出现的词得分低，命中「住宿」得高分
        score = sum(1.0 / df.get(t, 1) for t in overlap) / (1 + 0.02 * len(s))
        scored.append({"sentence": s.strip(), "cite": c["n"], "doc": c["doc_title"], "score": score})
    scored.sort(key=lambda x: -x["score"])
    if not scored:
        return []
    floor = scored[0]["score"] * 0.4
    # 每个来源最多两句，避免答案全来自一段
    out, per = [], {}
    for item in scored:
        if item["score"] < floor:
            break
        if per.get(item["cite"], 0) >= 2:
            continue
        per[item["cite"]] = per.get(item["cite"], 0) + 1
        out.append(item)
        if len(out) >= max_sent:
            break
    return out


def extractive_answer(question: str, cites: List[Dict]) -> str:
    picked = _best_sentences(question, cites)
    if not picked:
        head = cites[0] if cites else None
        if not head:
            return "知识库中暂未检索到与该问题相关的内容。请先上传文档并建图。"
        return (f"未找到与「{question}」直接相关的句子。以下是与问题最接近的原文片段"
                f"（《{head['doc_title']}》）：\n\n{truncate(head['text'], 300)} [{head['n']}]")
    lines = []
    for item in picked:
        lines.append(f"- {item['sentence']} [{item['cite']}]")
    return "根据知识库检索结果：\n\n" + "\n".join(lines)


def llm_answer(question: str, cites: List[Dict]) -> Optional[str]:
    if not config.llm_enabled():
        return None
    system = (
        "你是企业知识库问答助手。只能依据给定资料回答，不得编造。"
        "每个结论后用 [编号] 标注来源；资料不足时明确说“知识库中没有相关说明”。"
    )
    user = f"问题：{question}\n\n资料：\n{_context_text(cites)}"
    try:
        payload = json.dumps({
            "model": config.LLM_MODEL,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.2,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {config.LLM_API_KEY}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=config.LLM_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        text = (body["choices"][0]["message"]["content"] or "").strip()
        return text or None
    except Exception:
        return None


def ask(question: str, file_id: Optional[str] = None, use_graph: bool = True,
        top_k: int = config.TOP_K) -> Dict:
    t0 = time.perf_counter()
    question = (question or "").strip()
    if not question:
        raise ValueError("问题不能为空")

    cache_key = f"answer:{text_hash(question + '|' + (file_id or 'all') + '|' + str(use_graph) + '|' + str(top_k))}"
    cached = store.cache_get(cache_key)
    if cached:
        cached["cached"] = True
        return cached

    hit = retrieval.retrieve(question, file_id=file_id, top_k=top_k, use_graph=use_graph)
    cites = build_context(hit["chunks"])
    answer = llm_answer(question, cites) if cites else None
    mode = "llm"
    if not answer:
        answer = extractive_answer(question, cites)
        mode = "extractive"

    latency = round((time.perf_counter() - t0) * 1000, 1)
    result = {
        "question": question,
        "answer": answer,
        "mode": mode,
        "citations": cites,
        "routes": hit["route_counts"],
        "candidates": hit["candidates"],
        "file_id": file_id or "",
        "use_graph": use_graph,
        "latency_ms": latency,
        "cached": False,
    }
    result["history_id"] = store.add_history(result)
    store.cache_set(cache_key, result, ttl=config.CACHE_TTL["answer"])
    return result