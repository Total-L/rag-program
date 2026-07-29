# ADR 0003 — ACL 三档敏感度

## 状态
已采纳（**D4 部分修订**：见 [ADR-0004](./0004-persistent-incremental-index.md)）

## 背景
企业内不同部门/角色对文档可见性有严格要求。`rag-course` 训练营里的 path-based ACL 不够，需要 **content-level** 敏感度。
注：P1 自实现 path ACL（`_compat.is_path_allowed`，详见 ADR-0005），同时支持 content-level 敏感度过滤。

## 决策
- 文档 frontmatter 中标记 `sensitivity: public|internal|restricted`
- 用户角色：`intern / employee / manager / executive / admin`
- 角色 → 可见级别映射：
  - `intern` → public
  - `employee / manager` → public + internal
  - `executive / admin` → 全部
- ~~在 `index_corpus()` 阶段就过滤（不是检索时）~~（**D4 修订**：见 ADR-0004，
  build 期不过滤，query 期按 `sensitivity` 过滤；语义不变，实现位置变了）

## 后果
- ✅ 简单的"角色驱动"模型，面试好讲
- ✅ 数据不进 restricted 用户索引 → 物理隔离
- ⚠️ 角色变化需要 reindex（接受）（**D4 修订**：已不需要，query 期即时过滤）
- ⚠️ 文档级粒度，不支持 per-section

## 取舍
- **不**用 ABAC（attribute-based，复杂）
- **不**用 RBAC 数据库（增加外部依赖）
- **不**做"按段"权限（粒度过细，实践难）
