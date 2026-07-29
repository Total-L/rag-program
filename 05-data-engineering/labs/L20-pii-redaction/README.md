# L20 — PII / 密钥检测 + 脱敏

> "怎么优化数据"的核心：审计判定为 **critical** 的缺口——RAG 写库前不做 PII 脱敏，企业里上不了线。

## 学习目标
- 看到 9 类 PII / 密钥的正则模式（中国手机号/身份证/银行卡/邮箱/IP/JWT/API key/DSN/私钥）
- 看到**三道防线**：写库前（MASK/DROP）vs 写 trace 前（HASH）vs 自动升级 sensitivity
- 看到 ★ **顺序敏感**：`classify_sensitivity` 必须在 `redact_for_index` **之前**

## 跑

```bash
make lab-L20
```

## 关键代码点
- `PATTERNS` —— 9 类正则 + 策略（MASK/DROP）+ 保留方式
- `classify_sensitivity()` —— **只升不降**（安全语义）
- `redact_for_index()` —— 密钥类 DROP、个人信息 MASK（保留尾号）
- `redact_for_trace()` —— 一律 HASH + 截断（不留原文）

## ★ 顺序
```
classify_sensitivity → redact_for_index
        ↑
        一旦脱敏，身份证已被遮蔽，
        classify 检不出来 → 文档被错判成 public
```

## 边界
正则覆盖结构化 PII，**覆盖不了**自由文本里的姓名/地址/病情。
真上生产要接 Presidio / NER。本模块不假装解决了全部问题。

## 输出
- `artifacts/L20/samples.md` —— 原文/脱敏后/分级/原因对照

## 面试题
1. classify_sensitivity 必须在 redact_for_index **之前**的原因？
2. JWT / API Key 为什么要 DROP 而不是 MASK？
3. redact_for_trace 为什么用 HASH 而不是 DROP？（答：trace 需要把同一敏感值在多条日志里关联起来，HASH 保持等值性）