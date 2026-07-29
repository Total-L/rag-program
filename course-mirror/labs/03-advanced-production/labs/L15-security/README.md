# L15 — Security

## 学习目标
- 看到 4 类攻击面
- 演示 ACL / 路径穿越 / URI 注入 / prompt injection 防御
- 复用 kbchat.security 做生产级单入口

## 跑
```bash
make lab-L15
```

## 4 类攻击面
1. **路径穿越**（`../`、`/etc/`、`~/`）
2. **分类排除绕过**（exclusion 模式不够严）
3. **URI 注入**（`obsidian://open?path=` 后注入 `;?#`）
4. **prompt injection**（用户问题中含"忽略以上指令"）

## 防御
1. `Path.resolve() + 包含检查`
2. exclusion 列表 + 黑名单字符集
3. URI 用 `urllib.parse.quote` 编码
4. 关键词 regex + LLM 二次确认

## 面试要点
- `is_allowed` 是 single entry point，**所有路径必须过**
- prompt injection 无 100% 防御 → 纵深防御 + 监控
- URI 注入常被忽略（看似"安全链接"）
