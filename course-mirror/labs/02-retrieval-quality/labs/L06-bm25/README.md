# L06 — BM25 稀疏检索

## 学习目标
- 手写 BM25Okapi 核心公式
- 看到 BM25 与 dense 的互补性
- 与 `rank_bm25` 库、`kbchat.BM25Index` 对拍

## 跑
```bash
make lab-L06
```

## 核心公式
```
BM25(d, q) = Σ IDF(qi) · (tf(qi,d)·(k1+1)) / (tf(qi,d) + k1·(1-b+b·|d|/avgdl))
```

## 与 dense 对比
| | BM25 | dense |
|---|---|---|
| 精确词 | ✓ | ✗ |
| paraphrase | ✗ | ✓ |
| 缩写/型号 | ✓ | △ |
| 跨语言 | ✗ | ✓ |
| 训练成本 | 0 | 高 |

混合（hybrid）= BM25 + dense + RRF，几乎总是优于单一。
