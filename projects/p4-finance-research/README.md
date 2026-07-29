# P4 — Financial Research & Announcements（轻量 3）

> 金融研报与公告：表格解析 + 时间过滤 + 引用溯源。
> 栈：pypdf + 自研 markdown 表格解析 + 时间元数据过滤。

## 特点
- 50 份 A 股年报（脱敏）+ 30 份研报
- 表格问答：利润表/资产负债表解析
- 时间敏感：只能引用"问题时刻之前"的公告
- 引用溯源：每条事实带 [Doc X, Row Y] 引用

## 跑
```bash
python -m datasets.generators.finance
make verify-p4
```
