# L11 — Grounded Prompting 报告

## 5 条铁律
1. **系统 prompt** 明确「仅基于证据」——降低幻觉
2. **JSON 输出** 强制结构，便于校验
3. **引用校验**：每条引用必须在 valid_evidence 中
4. **拒答通道**：abstained=true 优于胡编
5. **JSON-mode** + 解析失败的 fallback（kbchat.mmx_client 有实现）

## 面试题
1. JSON-mode vs free-form？前者更易校验
2. 引用全空但 answer 很长 → 必幻觉
3. abstained 阈值如何设？kbchat 用置信度
