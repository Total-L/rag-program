# P2 — Code & Tech Docs RAG（轻量 1）

> 仓库级代码与技术文档检索。
> 用于作品集"轻量 1"位。栈：AST 切分 + NumPy 向量检索。
>
> **自包含**：无 kbchat 依赖；默认语料是仓库自带的 `datasets/fixtures/code_docs/sample_corpus/`。

## 特点
- 纯 AST 切分（Python 源码 → module/class/function 三层 doc-units）
- NumPy 余弦检索
- 不依赖 rag-course / kbchat
- 可指向任意 Python 项目（环境变量 `P2_CODE_TARGET` 或 `--source`）

## 跑
```bash
# 1. 用默认 sample_corpus 跑 smoke（11 个示例 .py）
make verify-p2

# 2. 指向任意 Python 项目
P2_CODE_TARGET=/path/to/python/project make verify-p2

# 3. 重新生成 code_docs.jsonl
python -m datasets.generators.code_docs
# 或指向自己的项目：
python -m datasets.generators.code_docs --source /path/to/python/project
```

## 默认 sample_corpus 内容
`datasets/fixtures/code_docs/sample_corpus/` 下 11 个示例文件，覆盖常见代码检索 Q&A 目标：

| 文件 | 主题 |
|---|---|
| `calculator.py` | 四则运算 + 零除守卫 |
| `string_utils.py` | slug / truncate / 中英混排字数 |
| `cli.py` | argparse + dispatch table 模式 |
| `retry.py` | 指数退避重试装饰器 |
| `cache.py` | OrderedDict LRU |
| `rate_limit.py` | 线程安全 token bucket |
| `config_loader.py` | 简易 YAML 风格配置 |
| `dataframe.py` | 列存 in-memory DataFrame |
| `event_bus.py` | 同步 pub-sub 事件总线 |
| `diff.py` | LCS 长度 + 编辑脚本 |
| `http_client.py` | stdlib HTTP/1.1 GET |
