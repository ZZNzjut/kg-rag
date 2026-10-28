"""全局配置：路径、切块参数、四路召回权重、LLM 开关。

设计原则：所有可调参数集中在此，便于复现实验与写进 README 说明。
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("KGRAG_DATA", ROOT / "data"))
SAMPLES_DIR = ROOT / "data" / "samples"
UPLOAD_DIR = DATA_DIR / "uploads"
VECTOR_DIR = DATA_DIR / "vectors"
GRAPH_DIR = DATA_DIR / "graphs"
DB_PATH = DATA_DIR / "kgrag.sqlite3"

for _d in (UPLOAD_DIR, VECTOR_DIR, GRAPH_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---- 切块 ----
CHUNK_SIZE = 360          # 目标字符数（中文按字符计）
CHUNK_OVERLAP = 72
MIN_CHUNK_CHARS = 40

# ---- 检索 ----
EMBED_DIM = 384           # 本地 hashing 向量维度
BM25_K1 = 1.5
BM25_B = 0.75
TOP_K = 6                 # 最终送入上下文的块数
RECALL_K = 10             # 每路召回数量

# 四路召回融合权重（参考实现原值：向量 1.0 / BM25 0.8 / 实体关系 0.9 / 位置 0.7）
# entity 由 0.9 降到 0.45：图谱扩展块是“相关但非直接命中”，在 20 题评测集上
# 0.9 会把命中@1 从 65% 拉到 45%，0.45 时升到 70%（见 eval/report.md 的扫参记录）。
ROUTE_WEIGHTS = {
    "vector": 1.0,
    "bm25": 0.8,
    "entity": 0.45,
    "position": 0.7,
}

# ---- 缓存 TTL（秒），模拟 Redis ----
CACHE_TTL = {
    "qa_history": 300,
    "graph": 600,
    "answer": 900,
}

# ---- LLM（可选）----
# 兼容 OpenAI 协议：DashScope compatible-mode / OpenAI / 本地 vLLM 均可
LLM_BASE_URL = os.environ.get("KGRAG_LLM_BASE_URL", "")
LLM_API_KEY = os.environ.get("KGRAG_LLM_API_KEY", "")
LLM_MODEL = os.environ.get("KGRAG_LLM_MODEL", "qwen-plus")
LLM_TIMEOUT = float(os.environ.get("KGRAG_LLM_TIMEOUT", "45"))

EMBED_BASE_URL = os.environ.get("KGRAG_EMBED_BASE_URL", "")
EMBED_API_KEY = os.environ.get("KGRAG_EMBED_API_KEY", "")
EMBED_MODEL = os.environ.get("KGRAG_EMBED_MODEL", "text-embedding-v3")

# 支持上传的扩展名
SUPPORTED_EXT = {".txt", ".md", ".markdown", ".csv", ".docx", ".xlsx", ".pptx", ".pdf"}


def llm_enabled() -> bool:
    return bool(LLM_BASE_URL and LLM_API_KEY)