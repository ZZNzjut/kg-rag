"""实体关系抽取：规则优先 + 可选 LLM 增强。

规则部分对中文企业文档做了专门设计（部门/系统/制度/角色/时限），
不依赖任何模型即可产出可解释的三元组；有 LLM 时叠加一次语义抽取并合并。
"""
from __future__ import annotations

import json
import re
import urllib.request
from typing import Dict, List, Set, Tuple

from . import config
from .text import split_sentences, truncate

# 实体模式：顺序很重要，先匹配更长的前缀
ENTITY_PATTERNS: List[Tuple[str, str]] = [
    ("DOC", r"《[^》]{2,40}》"),
    ("DEPT", r"[\u4e00-\u9fa5A-Za-z0-9]{2,12}(?:事业部|部门|中心|委员会|办公室|团队|小组|科室)"),
    ("SYSTEM", r"[\u4e00-\u9fa5A-Za-z0-9]{2,14}(?:系统|平台|中台|门户|工具箱|控制台)"),
    ("POLICY", r"[\u4e00-\u9fa5A-Za-z0-9]{2,14}(?:制度|规范|流程|细则|办法|手册|标准|预案)"),
    ("ROLE", r"[\u4e00-\u9fa5A-Za-z0-9]{2,10}(?:经理|主管|专员|工程师|负责人|管理员|审批人|接口人)"),
    ("ORG", r"[\u4e00-\u9fa5A-Za-z0-9]{2,12}(?:公司|集团|银行|大学|医院|工厂)"),
    ("METRIC", r"\d+(?:\.\d+)?\s*(?:个工作日|工作日|小时|分钟|天|周|月|%|万元|元|次|人)"),
]

_REL_CUES: List[Tuple[str, List[str]]] = [
    ("负责", ["负责", "牵头", "统筹", "承办"]),
    ("审批", ["审批", "审核", "批准", "签批"]),
    ("适用", ["适用", "适用于", "针对", "覆盖"]),
    ("依赖", ["依赖", "前置", "基于", "依托"]),
    ("包含", ["包括", "包含", "涵盖", "分为"]),
    ("上报", ["上报", "汇报", "提交", "报送"]),
]


_SUFFIX_RE = re.compile(
    r"(事业部|部门|中心|委员会|办公室|团队|小组|科室|系统|平台|中台|门户|控制台|"
    r"制度|规范|流程|细则|办法|手册|标准|预案|经理|主管|专员|工程师|负责人|管理员|"
    r"审批人|接口人|公司|集团|银行|大学|医院|工厂)$"
)
# 实体名里出现这些字，基本说明跨了句子成分（“业务系统的功能变更由研发中心”）
_BAD_CHARS = set("的了是由在于和与对为将对把被从向按经需应可未已并或及等其该本此")


def _clean(name: str) -> str:
    name = name.strip()
    name = re.sub(r"^[，。、；：:（）()【】\[\]“”\"']+|[，。、；：:（）()【】\[\]“”\"']+$", "", name)
    name = name.strip("《》")
    return name


def _valid(name: str, etype: str) -> bool:
    if not name or len(name) < 2 or len(name) > 24:
        return False
    if etype in {"DEPT", "SYSTEM", "POLICY", "ROLE", "ORG"}:
        base = _SUFFIX_RE.sub("", name)
        if len(base) < 2 or len(base) > 10:
            return False
        if any(ch in _BAD_CHARS for ch in base):
            return False
    return True


def extract_entities(text: str) -> List[Dict]:
    found: Dict[str, Dict] = {}
    for etype, pat in ENTITY_PATTERNS:
        for m in re.finditer(pat, text or ""):
            name = _clean(m.group(0))
            if not name or len(name) < 2:
                continue
            # 过滤明显不是实体的泛词
            if name in {"该系统", "本系统", "相关系统", "以上流程", "本流程"}:
                continue
            if etype == "METRIC":
                continue  # 指标只用于关系描述，不建节点
            if not _valid(name, etype):
                continue
            key = (etype, name)
            if key not in found:
                found[key] = {"name": name, "type": etype, "mentions": 0}
            found[key]["mentions"] += 1
    return list(found.values())


def _sentence_relations(sent: str, ents: List[Dict]) -> List[Dict]:
    """句子内：实体对 + 关系词 → 三元组。"""
    names = [e["name"] for e in ents if e["name"] in sent]
    if len(names) < 2:
        return []
    rel = "相关"
    for rname, cues in _REL_CUES:
        if any(c in sent for c in cues):
            rel = rname
            break
    out = []
    # 只连接相邻实体，避免 n^2 爆炸
    for a, b in zip(names, names[1:]):
        out.append({"head": a, "relation": rel, "tail": b, "sentence": truncate(sent, 160)})
    return out


def rule_extract(text: str) -> Dict:
    ents = extract_entities(text)
    triples: List[Dict] = []
    ent_set = {e["name"] for e in ents}
    for sent in split_sentences(text):
        hit = [e for e in ents if e["name"] in sent]
        if len(hit) >= 2:
            triples.extend(_sentence_relations(sent, hit))
        elif hit and re.search(r"(负责|审批|适用于|依赖)", sent):
            # 单实体 + 关系词：连到最近提及的另一个实体（跨句共指很弱，这里跳过）
            pass
    # 去重
    seen: Set[Tuple[str, str, str]] = set()
    uniq = []
    for t in triples:
        k = (t["head"], t["relation"], t["tail"])
        if k in seen or t["head"] == t["tail"]:
            continue
        seen.add(k)
        uniq.append(t)
    return {"entities": [e for e in ents if e["name"] in ent_set], "triplets": uniq}


def llm_extract(text: str) -> Dict:
    """可选：调用 OpenAI 兼容接口做零样本三元组抽取。"""
    if not config.llm_enabled():
        return {"entities": [], "triplets": []}
    prompt = (
        "从下面的企业文档片段中抽取实体与关系。只输出 JSON，格式："
        '{"entities":[{"name":"...","type":"DEPT|SYSTEM|POLICY|ROLE|ORG|DOC"}],'
        '"triplets":[{"head":"...","relation":"负责|审批|适用|依赖|包含|上报","tail":"..."}]}\n'
        f"文档片段：\n{truncate(text, 3000)}"
    )
    try:
        payload = json.dumps({
            "model": config.LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {config.LLM_API_KEY}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=config.LLM_TIMEOUT) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
        data = json.loads(content)
        return {"entities": data.get("entities", []) or [], "triplets": data.get("triplets", []) or []}
    except Exception:
        return {"entities": [], "triplets": []}


def extract(text: str, use_llm: bool = False) -> Dict:
    """规则抽取 + 可选 LLM 合并（按 name/三元组去重）。"""
    base = rule_extract(text)
    if not use_llm:
        return base
    extra = llm_extract(text)
    ents = {(e.get("type", "ORG"), e["name"]): e for e in base["entities"]}
    for e in extra.get("entities", []):
        name = _clean(str(e.get("name", "")))
        if not name:
            continue
        etype = str(e.get("type", "ORG"))
        if not _valid(name, etype):
            continue
        key = (etype, name)
        ents.setdefault(key, {"name": name, "type": etype, "mentions": 1})
    trips = {(t["head"], t["relation"], t["tail"]): t for t in base["triplets"]}
    for t in extra.get("triplets", []):
        h, r, tl = _clean(str(t.get("head", ""))), str(t.get("relation", "相关")), _clean(str(t.get("tail", "")))
        if h and tl and h != tl:
            trips.setdefault((h, r, tl), {"head": h, "relation": r, "tail": tl, "sentence": ""})
    return {"entities": list(ents.values()), "triplets": list(trips.values())}