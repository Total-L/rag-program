# L06 — BM25 报告

## 核心公式
BM25(d, q) = Σ IDF(qi) · (tf(qi, d)·(k1+1)) / (tf(qi, d) + k1·(1-b+b·|d|/avgdl))

## 与 dense 对比
- **BM25 强项**：精确词匹配、缩写、型号、专有名词
- **BM25 弱项**：paraphrase、同义词、跨语言
- **dense 强项**：语义相似、paraphrase
- **dense 弱项**：精确词、稀有词
- **混合（hybrid）**：BM25 + dense + RRF 融合，几乎总是优于单一
