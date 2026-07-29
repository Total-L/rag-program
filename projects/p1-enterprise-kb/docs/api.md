# P1 API（Python）

## 入口

```python
from projects.p1_enterprise_kb.src.acl import User
from projects.p1_enterprise_kb.src.pipeline import P1Pipeline

user = User(user_id="alice", role="manager")  # 5 档角色
pipeline = P1Pipeline(user=user)
pipeline.index_corpus()
result = pipeline.ask("员工每年能休多少天年假？")
print(result.answer, result.citations, result.abstained)
```

## `User`

```python
@dataclass
class User:
    user_id: str
    role: str  # intern | employee | manager | executive | admin
```

## `P1Pipeline`

```python
class P1Pipeline:
    def __init__(self, user: User):
        """构造：仅持有 user；不立即索引。"""

    def index_corpus(self) -> None:
        """加载 + chunking + embedding + ACL 过滤。"""

    def ask(self, question: str) -> P1Result:
        """端到端问答。"""


@dataclass
class P1Result:
    question: str
    answer: str
    citations: list[str]  # ["E1", "E2"]
    abstained: bool
    user_role: str
    latency_ms: float
    stages: dict  # {"indexed_chunks": ..., "retrieved": ...}
```

## 角色 → 可见级别

| role | public | internal | restricted |
|---|---|---|---|
| intern | ✓ | | |
| employee | ✓ | ✓ | |
| manager | ✓ | ✓ | |
| executive | ✓ | ✓ | ✓ |
| admin | ✓ | ✓ | ✓ |

## Streamlit

```bash
streamlit run projects/p1-enterprise-kb/src/app.py
```

三面板：
- **Chat**：消息流
- **Sources**：引用列表
- **Diagnostics**：索引大小、当前 role、trace 路径
