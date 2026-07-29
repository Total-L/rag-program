# L10 — Context Packing 报告

## 5 条规则
1. **去重**（sha1 前 8 字符）
2. **per-source cap**（每源最多 N 条）
3. **token budget**（总字符数上限）
4. **'lost in the middle' 排序**（最相关的放首尾）
5. **截断**（超过的留到下轮）

## 面试要点
- 'Lost in the middle' 是 Liu et al. 2023 的发现：LLM 倾向于关注首尾
- 排序策略：1, N, 2, N-1, 3, N-2, ...
- per_source_cap 防止单源独大影响答案多样性
