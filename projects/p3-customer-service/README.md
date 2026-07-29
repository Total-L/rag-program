# P3 — Customer Service & Tickets（轻量 2）

> 客服场景：意图路由 + 多轮 + 工单生成。
> 栈：LangGraph（简化） + FAQ 向量检索。

## 特点
- 6 个意图分类（billing / technical / account / refund / shipping / general）
- 60 条 FAQ → 向量检索
- 多轮上下文：把历史 FAQ 拼进 context
- 转人工：置信度低或用户说"人工"→ 转人工 + 生成工单

## 跑
```bash
python -m datasets.generators.customer_service
make verify-p3
```
