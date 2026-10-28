# KG-RAG：面向企业知识库的知识图谱生成与 RAG 智能问答

把一批企业内部文档（员工手册、报销制度、IT 运维规范、安全分级规范、客服 SLA、采购制度……）
变成四件东西：

1. **可检索的向量库**（解析 → 切块 → 本地 TF-IDF 哈希向量，可选远程 Embedding）
2. **知识图谱**（规则/LLM 抽取实体与关系 → 共现补边 → 社区发现）
3. **可交互的图谱可视化**（力导向布局、搜索、邻居高亮、社区分色）
4. **带引用的问答**（四路召回 → rank 融合 → 抽取式回答 / 可选 LLM 生成）

这是对一个公开参考实现（Knowledge-Graph-Web）的**独立可运行复现版**：
接口与页面按其功能重写，但不依赖 MySQL/Neo4j/Chroma/Redis，
默认只需 Python 3.9+ 标准库即可跑通，装了 FastAPI 就自动走 FastAPI。

## 快速开始

```powershell
# 0) 生成演示数据（7 个样例文档：6 个 md + 1 个 docx，见 data/samples/）
python scripts/seed.py

# 1) 启动（优先 FastAPI，没有则自动退回标准库 http.server）
python app.py --port 8765
# 或强制指定后端
python app.py --backend stdlib --port 8765
```

打开 http://127.0.0.1:8765 ，按「上传 → 建图 → 问答 / 图谱」使用。

```powershell
# 命令行冒烟测试（不依赖 HTTP）
python tests/smoke.py

# 跑评测（20 题，输出 eval/report.md）
python eval/run_eval.py
```

## 目录结构

```
kg-rag/
├── app.py                  # 入口：选择 FastAPI / 标准库后端
├── core/
│   ├── config.py           # 路径、切块参数、四路权重、LLM 开关（全部集中可调）
│   ├── parsers.py          # 解析 md/txt/docx/xlsx/pptx/pdf（python-docx/openpyxl/python-pptx/pypdf 可选）
│   ├── splitter.py         # 标题感知切块 + 重叠 + 短块合并 + 块前缀
│   ├── text.py             # 分词、分句、哈希、截断
│   ├── embed.py            # 本地 TF-IDF 哈希向量 / 远程 OpenAI 兼容 Embedding
│   ├── vectorstore.py      # 单文件 JSON 向量库 + 余弦检索
│   ├── bm25.py             # 自实现 BM25 + 进程内 LRU 索引缓存
│   ├── extractor.py        # 规则实体/关系抽取（可选 LLM 抽取）
│   ├── graphstore.py       # 实体图：节点/边/邻接/社区发现(LPA)/子图/持久化
│   ├── store.py            # SQLite：文件元数据、问答历史、带 TTL 的缓存
│   ├── retrieval.py        # 四路召回 + 线性 rank 融合
│   ├── qa.py               # 上下文拼装、抽取式回答、LLM 回答、引用与历史
│   ├── ingest.py           # 入库 / 建图 / 全量重建流水线
│   ├── api.py              # 纯逻辑 HTTP 路由（框架无关）
│   ├── server_stdlib.py    # 标准库 http.server 适配
│   └── server_fastapi.py   # FastAPI 适配
├── web/                    # 无构建前端：index.html + app.js + style.css
├── data/                   # 运行时数据（uploads/vectors/graphs/kgrag.sqlite3），已 gitignore
├── data/samples/           # 演示文档
├── eval/                   # 评测脚本与报告
├── scripts/seed.py         # 一键重建演示数据
└── tests/smoke.py          # 端到端冒烟
```

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查：LLM/Embedding 模式、文件数、DB 统计 |
| GET | `/api/status` | 首页统计：文件、块、实体数 |
| GET | `/api/files` | 文件列表 + 向量库统计 |
| POST | `/api/upload` | 上传：body 为文件二进制，文件名放 `X-Filename` 头（URL 编码） |
| POST | `/api/build` | 建图，body `{use_llm, reset}`；`reset=true` 先清空再全量重建 |
| GET | `/api/graph?limit=120` | 子图（节点/边/社区/统计），默认按度取样 |
| GET | `/api/graph/stats` | 图谱统计（类型分布、关系分布、Top 实体） |
| POST | `/api/ask` | 问答，body `{question, use_graph, top_k, file_id}` |
| GET/DELETE | `/api/history` | 问答历史（最多 20 条） |

兼容别名：`/api/qa`、`/api/qa/history`、`/api/rebuild`。

## 检索：四路召回 + 线性 rank 融合

| 路 | 做法 | 权重 |
| --- | --- | --- |
| vector | 本地 TF-IDF 哈希向量（384 维，带符号哈希）余弦相似度；配置 `KGRAG_EMBED_*` 后走远程 Embedding | 1.0 |
| bm25 | 自实现 BM25（k1=1.5, b=0.75），中文按字 bigram + 连读词，空命中回退首段 | 0.8 |
| entity | 图谱路：问题点名实体 + 向量/BM25 种子块里的实体 → 实体的其他块与 1 跳邻居块 | 0.45 |
| position | 文档首尾块（制度类文档的“总则/附则”常含关键约束） | 0.7 |

融合公式与参考实现一致：`score += (len(route) - rank) * weight`，同分按块长降序，
再加一道分数闸门（低于第一名的 35% 截断）。

> **调参记录**：参考实现原值 entity=0.9，在 20 题评测集上会把命中@1 从 65% 压到 45%
> （相关块挤掉了直接命中块）；扫参后取 0.45，命中@1 70%、MRR 0.817。见 `eval/report.md`。

## 知识图谱

- 实体类型：文档 / 部门 / 系统 / 制度 / 角色 / 组织 / 指标，正则规则 + 词表清洗（长度、停用词、字符校验）
- 关系：负责 / 审批 / 适用 / 依赖 / 包含 / 上报，同块实体按“以出现最多的实体为中心”连共现边
- 图结构：`GraphStore` 保存节点（类型、chunk_ids、mention）与边（source, target, rel, weight）
- 社区发现：LPA（标签传播），前端按社区分色
- 可视化：canvas 力导向（斥力 + 弹簧 + 向心力），支持搜索、点击邻居高亮、拖拽/滚轮缩放

## 问答

1. `retrieval.retrieve()` 四路召回 → 融合取 top_k（默认 6）块
2. `build_context()` 生成引用列表（编号、文档、标题、chunk_id、原文）
3. `extractive_answer()`：分句 → 与问题 token 的 IDF 加权重合 + 位置偏置 → 去重后取最高分句；
   支持简单数字/枚举型问题（“几天”“几个等级”）的完整分句保留
4. `llm_answer()`：设置了 `KGRAG_LLM_BASE_URL` + `KGRAG_LLM_API_KEY` 时走 OpenAI 兼容
   `/chat/completions`；prompt 强制“只依据资料、必须带 [n] 编号、无依据时回答不知道”
5. 全部回答写入 SQLite 历史；相同问题 15 分钟内走缓存

## 评测

`python eval/run_eval.py` 在 20 个手写问题上对比 `use_graph` 开关（top_k=6）：

| 指标 | graph | no-graph |
| --- | --- | --- |
| 命中@1 | 70.0% | 65.0% |
| 命中@3 | 95.0% | 95.0% |
| MRR | 0.817 | 0.793 |
| 要点覆盖 | 85.0% | 85.0% |
| 全要点覆盖 | 65.0% | 65.0% |
| p50 延迟 | 38 ms | 28 ms |

> 指标口径与局限：命中判定为“期望文档的块出现在 top_k 中”，要点覆盖为关键词命中比例；
> 20 题规模小、数字仅用于回归对比，不代表线上效果。LLM 生成的质量需另测。
> 第 14 题「离职后账号多久删除」的期望文档标注有误（答案实际写在 03_IT运维服务规范），
> 保留原样未改题，因此命中@3 里那 5% 的缺口是题目问题而非检索问题。

## 与参考实现的差异（有意为之）

| 参考实现 | 本复现 |
| --- | --- |
| FastAPI + MySQL + Neo4j + Chroma + Redis + DashScope Embedding | 默认零依赖：SQLite + JSON 向量库 + 内存图 + 进程内缓存；接口预留，可切换 |
| 仓库缺 `app/database/` 与 `graph_rag_service.py`，克隆即 ImportError | 全部模块自包含，`python app.py` 直接可跑 |
| 前后端分离 Vue3 + Vite | 单页原生 JS（无构建），接口路径保持一致 |
| 只按“问题里出现实体名”走实体路 | 增加“种子块实体扩展”，实体路触发率从 0% 提升到 100%（并做了权重校准） |

## 可选依赖

```powershell
# 用 FastAPI 后端
pip install fastapi uvicorn
# 解析 docx/xlsx/pptx/pdf
pip install python-docx openpyxl python-pptx pypdf
# 远程 Embedding / LLM（OpenAI 兼容协议）
$env:KGRAG_EMBED_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
$env:KGRAG_EMBED_API_KEY="sk-..."
$env:KGRAG_LLM_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
$env:KGRAG_LLM_API_KEY="sk-..."
$env:KGRAG_LLM_MODEL="qwen-plus"
```

## 已知限制

- 本地哈希向量是词面相似度，不是语义相似度；跨表述（“年假” vs “带薪休假”）靠 BM25 词表兜底
- 规则抽取召回有限，实体有漏抽与歧义（同名不同系统）；LLM 抽取默认关闭
- 社区发现用 LPA，结果与节点遍历顺序有关，仅用于展示分组
- 单进程、无鉴权、无并发隔离，只适合演示与小团队内网使用
- 图谱文件全量 JSON 读写，文档量上千需要换图库

## 复现步骤

1. `python scripts/seed.py` → 7 文件 / 39 块 / 47 实体 / 80 关系 / 6 社区
2. `python app.py --port 8765` → 打开页面，上传一份新文档（如 docx），建图，问答
3. `python eval/run_eval.py` → 得到上表的命中率/延迟，写进 README
4. `python tests/smoke.py` → 全链路断言（解析→切块→检索→问答→图谱）