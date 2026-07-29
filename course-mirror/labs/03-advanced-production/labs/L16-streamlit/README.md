# L16 — Streamlit 烟雾测试

## 学习目标
- 验证 Streamlit 三面板（Chat / Sources / Diagnostics）能跑
- 验证 Pipeline.ask 接口
- 启动 streamlit server 看 UI

## 跑
```bash
make lab-L16
```

## 启动
```bash
# 在 /Users/totallai/rag-course 目录下：
streamlit run kbchat/app.py

# 或 rag-program 自己的（如果有）：
PYTHONPATH=/Users/totallai/rag-course streamlit run projects/p1-enterprise-kb/src/app.py
```

## 三面板
- **Chat**：用户消息 + 流式回答
- **Sources**：引用 chunk 列表，可点 obsidian:// 跳转
- **Diagnostics**：trace 摘要、p50/p95、错误率
