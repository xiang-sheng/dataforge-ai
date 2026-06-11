## 数据治理优化方案：Embedding 预筛 + LLM 精判

### 问题

当前 GovernanceAgent 的流程是全 LLM 驱动的：list_tables → LLM 自己判断哪些表可能冗余 → 逐对 describe_table / compare_tables。对于 50 张表的数据库，LLM 可能需要 20-40 次工具调用才能完成筛选，其中大量调用花在了"确认这两张表确实不像"上。

核心矛盾：**LLM 擅长语义判断，但不擅长高效遍历**。让 LLM 做 N² 的遍历筛选是浪费 token。

### 方案总览

```
┌──────────────────────────────────────────────────────────────┐
│                   GovernanceAgent (LLM)                       │
│                                                               │
│  用户: "扫描冗余表"                                            │
│         │                                                     │
│         ▼                                                     │
│  ┌─────────────────────────────────┐                          │
│  │ 第1层: scan_redundancy_candidates│  ← 新工具，零 LLM 调用   │
│  │  · 提取全库 schema 元信息         │                         │
│  │  · Embedding 编码               │                         │
│  │  · 余弦相似度矩阵               │                         │
│  │  · 阈值过滤 → 候选表对列表       │                         │
│  └──────────────┬──────────────────┘                          │
│                 │ 返回: [(orders, orders_bak, 0.92), ...]      │
│                 ▼                                             │
│  ┌─────────────────────────────────┐                          │
│  │ 第2层: LLM 精判（仅候选对）      │  ← 少量 LLM 调用        │
│  │  · compare_tables 详细对比       │                         │
│  │  · get_sample_data 确认数据重叠  │                         │
│  │  · 语义分析 + 治理建议           │                         │
│  └──────────────┬──────────────────┘                          │
│                 │                                             │
│                 ▼                                             │
│          治理报告输出                                          │
└──────────────────────────────────────────────────────────────┘
```

### 新增工具：`scan_redundancy_candidates`

一个工具完成全部预筛工作，LLM 只需调用一次。

```python
@tool
def scan_redundancy_candidates(
    similarity_threshold: float = 0.5,
    top_k: int = 20,
) -> str:
    """扫描全库，用 Embedding + 余弦相似度预筛冗余候选表对。

    返回按相似度降序排列的候选表对列表，供后续 compare_tables 深入分析。

    Args:
        similarity_threshold: 最低相似度阈值，默认 0.5（低于此值直接排除）
        top_k: 最多返回多少对候选，默认 20
    """
```

#### 内部流程

```
Step 1: 提取 schema 元信息（纯 DuckDB 查询，零外部依赖）
────────────────────────────────────────────────────
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'main'
ORDER BY table_name, ordinal_position

→ 构建每张表的文本表示:
  "orders | order_id BIGINT, user_id BIGINT, total_amount DECIMAL,
   created_at TIMESTAMP, status VARCHAR | 1200 rows"

Step 2: Embedding 编码
──────────────────────
  方案A (推荐): sentence-transformers / all-MiniLM-L6-v2
    - 80MB 模型，纯 CPU，单条 < 10ms
    - 384 维向量，质量足够 schema 级别匹配

  方案B (复用 Ollama): nomic-embed-text
    - 复用现有 Ollama 基础设施，零额外依赖
    - 768 维向量，质量更好但需要 Ollama 在线

Step 3: 余弦相似度矩阵
──────────────────────
  N 张表 → N 个向量 → N×N 相似度矩阵（numpy 矩阵运算，微秒级）
  
  sim_matrix = embeddings @ embeddings.T  # 归一化后等价于余弦相似度

Step 4: 阈值过滤 + 排序
──────────────────────
  candidates = [
      (table_a, table_b, similarity)
      for i, j in combinations(range(N), 2)
      if sim_matrix[i][j] >= threshold
  ]
  candidates.sort(key=lambda x: -x[2])  # 降序
  return candidates[:top_k]
```

#### 输出格式

```
冗余候选扫描结果（50 张表 → 5 对候选，阈值 ≥ 0.5）:

  1. orders ↔ orders_bak         相似度 94.2%  ⚠ 高度冗余
     orders: 8列, 1200行  |  orders_bak: 8列, 1200行
  2. order_items ↔ order_items_v2 相似度 87.6%  ⚠ 高度冗余
     order_items: 6列, 3600行  |  order_items_v2: 7列, 3600行
  3. users ↔ user_profiles       相似度 62.3%  △ 部分重叠
     users: 5列, 500行  |  user_profiles: 9列, 500行
  4. dws_orders ↔ ads_orders     相似度 58.1%  △ 部分重叠
     dws_orders: 10列, 800行  |  ads_orders: 6列, 800行
  5. products ↔ product_archive  相似度 51.4%  △ 部分重叠
     products: 12列, 2000行  |  product_archive: 12列, 1800行

建议: 对相似度 ≥ 80% 的表对使用 compare_tables 做详细分析。
```

### 改造后的 Agent 流程

```
修改前（全 LLM 驱动）:
  LLM 调用次数: list_tables(1) + describe_table × ~10 + compare_tables × ~8 = ~20 次
  Token 消耗: 高（每次 describe/compare 都带完整上下文）
  
修改后（预筛 + 精判）:
  LLM 调用次数: scan_redundancy_candidates(1) + compare_tables × 3-5 + get_sample_data × 2-3 = ~8 次
  Token 消耗: 降低 50-60%
  精度: 更高（预筛基于语义向量，不依赖 LLM 逐表猜测）
```

#### 更新后的 System Prompt

```diff
 ## 工作流程

-1. list_tables 获取所有表清单
-2. 根据表名和行数初步筛选可能冗余的表对
-3. describe_table 逐表查看字段结构
-4. compare_tables 对疑似冗余的表对做详细对比
-5. get_sample_data 确认数据是否真的重叠
-6. 输出治理报告
+1. scan_redundancy_candidates 获取预筛结果（Embedding 语义匹配）
+2. 对相似度 ≥ 80% 的候选对逐一 compare_tables 详细验证
+3. get_sample_data 抽查确认数据是否重叠
+4. 对相似度 50-80% 的候选对，选择性 compare_tables 或 describe_table
+5. 输出治理报告
```

### Embedding 方案对比

| 维度 | all-MiniLM-L6-v2 (sentence-transformers) | nomic-embed-text (Ollama) |
|------|------------------------------------------|--------------------------|
| 模型大小 | 80MB | 274MB |
| 向量维度 | 384 | 768 |
| 推理速度 | ~5ms/条 (CPU) | ~50ms/条 (HTTP → Ollama) |
| 依赖 | sentence-transformers (含 torch ~2GB) | 无额外依赖（复用 Ollama） |
| 离线 | 支持（模型本地加载） | 需 Ollama 在线 |
| Schema 匹配质量 | 足够 | 更好（更大模型） |
| 适用场景 | 追求速度、部署简单 | 追求质量、已有 Ollama |

**建议**: 默认用 Ollama 的 nomic-embed-text（复用现有基础设施），fallback 到 all-MiniLM-L6-v2。

### 新增文件结构

```
src/warehouse/
├── embedding.py           # Embedding 编码 + 相似度计算（新）
│   ├── SchemaEmbedder      # 表 schema → 文本 → 向量
│   ├── cosine_similarity() # numpy 矩阵运算
│   └── find_candidates()   # 阈值过滤 + 排序
├── tools.py               # 新增 scan_redundancy_candidates 工具
└── ...
```

### `embedding.py` 核心设计

```python
class SchemaEmbedder:
    """将表 schema 编码为向量，用于相似度预筛。"""

    def __init__(self, model_name="nomic-embed-text", backend="ollama"):
        """
        backend: "ollama" | "sentence-transformers"
        """
        ...

    def _table_to_text(self, table_name, columns, row_count) -> str:
        """将表元信息拼成可 embedding 的文本。"""
        # "orders | order_id BIGINT PK, user_id BIGINT FK,
        #  amount DECIMAL(18,2), created_at TIMESTAMP | 1200 rows"
        ...

    def embed_tables(self, table_schemas: list[dict]) -> np.ndarray:
        """批量编码 N 张表 → (N, dim) 矩阵。"""
        ...

    def find_candidates(
        self,
        table_schemas: list[dict],
        threshold: float = 0.5,
        top_k: int = 20,
    ) -> list[tuple[str, str, float]]:
        """返回 [(table_a, table_b, similarity), ...] 按相似度降序。"""
        embeddings = self.embed_tables(table_schemas)
        # 归一化
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        normalized = embeddings / norms
        # 余弦相似度矩阵
        sim_matrix = normalized @ normalized.T
        # 提取上三角（去对角线），过滤 + 排序
        ...
```

### 依赖变更

```toml
# pyproject.toml — 新增
[project.optional-dependencies]
governance = [
    "sentence-transformers>=3.0.0,<4.0.0",  # 可选，仅 ollama 不可用时
]
```

核心依赖不变。默认走 Ollama HTTP 调用 nomic-embed-text，sentence-transformers 作为可选的本地 fallback。

### 性能预估

| 场景 | 现有方案 | 改进后 |
|------|---------|--------|
| 20 张表 | ~12 次 LLM 调用 | 1 次预筛 + ~4 次验证 |
| 50 张表 | ~25 次 LLM 调用 | 1 次预筛 + ~6 次验证 |
| 100 张表 | ~40+ 次 LLM 调用（可能触发 MAX_ITERATIONS） | 1 次预筛 + ~8 次验证 |
| 预筛耗时 | — | < 500ms（100 张表） |

### 实施步骤

1. 新建 `src/warehouse/embedding.py`，实现 SchemaEmbedder
2. `tools.py` 新增 `scan_redundancy_candidates` 工具，内部调用 SchemaEmbedder
3. 更新 GOVERNANCE_SYSTEM_PROMPT，引导 Agent 先调预筛再精判
4. 测试：新增 embedding 模块单测 + scan 工具集成测试
5. demo.py 验证完整流程
