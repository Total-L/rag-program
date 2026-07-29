# L11 — Grounded Prompting

## 学习目标
- 看到系统 prompt 如何限制幻觉
- 看到 JSON-mode 响应 + 引用校验
- 看到"拒答"通道的设计

## 跑
```bash
make lab-L11
```

## 5 条铁律
1. **系统 prompt** 明确"仅基于证据"
2. **JSON 输出** 强制结构
3. **引用校验**：每条引用必须在 valid_evidence 中
4. **拒答通道**：`abstained=true` 优于胡编
5. **JSON-mode** + 解析失败的 fallback

## 面试题
1. JSON-mode vs free-form？前者更易校验
2. 引用全空但 answer 很长 → 必幻觉
3. abstained 阈值如何设？kbchat 用置信度
