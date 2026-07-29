# P5 — Multi-Format Loader（多格式数据提取）

> RAG 实战训练营的 **多格式文档加载** 项目：把 PDF / Word / Excel / HTML 抽出统一的 `Block` 列表，写进 Chroma 向量库，并提供 ground truth + accuracy eval。

解决用户在面试/生产里被问的硬问题：**"你们怎么支持 PDF/Word/Excel？"**

## 📐 架构

```
任意格式文件 (.pdf/.docx/.xlsx/.html)
       │
       ▼ load() 按后缀分发
┌────────────────────────────────────────────────────────────┐
│  loaders/                                                   │
│  ├─ pdf.py    pdfplumber → text + tables + images           │
│  ├─ docx.py   python-docx → paragraphs + tables            │
│  ├─ xlsx.py   openpyxl → multi-sheet → tables              │
│  └─ html.py   beautifulsoup4 → semantic text + tables      │
└────────────────────────────────────────────────────────────┘
       │
       ▼ 产出统一 Block（text/table/heading/image）
   Block.content_hash ─── 去重 + 增量索引
       │
       ▼ chroma_store.add_document()
┌────────────────────────────────────────────────────────────┐
│  Chroma (PersistentClient)                                  │
│  ├─ 每条 Block = 一条 record                                │
│  ├─ text 用 Ollama nomic-embed-text 嵌入                   │
│  ├─ metadata: block_type / source_format / page_or_sheet   │
│  │            / headers ("|" 分隔) / content_hash          │
│  └─ 写到 ./chroma_db/ 持久化                                │
└────────────────────────────────────────────────────────────┘
       │
       ▼ query(text) → 语义检索 + 类型/格式过滤
```

## 🗂 目录结构

```
projects/p5-multi-format-loader/
├── README.md                  ← 本文件
├── src/
│   ├── models.py              ← Block / Document / BlockType / SourceFormat
│   ├── chroma_store.py        ← P5ChromaStore（写入 + 查询 + 去重）
│   ├── loaders/
│   │   ├── __init__.py        ← load(path) 按后缀分发
│   │   ├── pdf.py             ← pdfplumber + 表格/图片
│   │   ├── docx.py            ← python-docx + 表格
│   │   ├── xlsx.py            ← openpyxl + 多 sheet + 合并单元格
│   │   └── html.py            ← beautifulsoup4 + semantic 优先级
│   └── eval/
│       └── accuracy.py        ← 跟 ground truth 对比算 recall/precision
├── fixtures/
│   ├── sample.pdf             ← 含 1 个表 + 多页文本
│   ├── sample.docx            ← 含 1 个表 + 标题层级
│   ├── sample.xlsx            ← 含 2 sheet
│   ├── sample.html            ← 含 1 个表 + 1 张图 + heading
│   ├── architecture.pdf       ← 3 页 RAG Platform 复杂架构图(2026-07-26 新增)
│   ├── ground_truth.jsonl     ← 5 个文件的期望 block 列表
│   └── .derived/              ← PDF loader 派生的 cropped PNG(可删重建)
├── tests/
│   └── test_loaders.py        ← 5 个 loader 单测
└── scripts/
    ├── make_fixtures.py       ← 重新生成 4 个样本
    ├── make_arch_pdf.py       ← 生成 architecture.pdf(PIL + reportlab)
    ├── verify_arch_fixture.py ← 验 architecture.pdf 端到端
    ├── run_eval.py            ← 跑 accuracy eval
    └── smoke_chroma.py        ← 写入 Chroma + 语义检索
```

## 🚀 5 分钟上手

### 1. 装依赖

```bash
./.venv/bin/pip install pdfplumber python-docx openpyxl beautifulsoup4 markdownify chromadb reportlab
```

### 2. 生成样本（如未生成）

```bash
cd projects/p5-multi-format-loader
./.venv/bin/python scripts/make_fixtures.py
```

> 想重生成 3 页架构图(architecture.pdf):`./.venv/bin/python scripts/make_arch_pdf.py`,然后 `scripts/verify_arch_fixture.py` 端到端验证。
>
> `fixtures/.derived/` 是 PDF loader 抽图时落盘的 cropped PNG,UI 用它渲染原图;目录可删,下次跑 load() 会重新生成。

### 3. 跑 loader 单测

```bash
./.venv/bin/python tests/test_loaders.py
```

### 4. 写 Chroma + 语义检索

```bash
./.venv/bin/python scripts/smoke_chroma.py
```

### 5. 跑准确率评测

```bash
./.venv/bin/python scripts/run_eval.py
```

## 📊 实测结果

### Accuracy eval（最近一次）

```
file            blocks   recall  precision
----------------------------------------------------------------------
  sample.pdf         3    1.000      1.000
  sample.docx        9    1.000      1.000
  sample.xlsx        2    1.000      1.000
  sample.html        8    0.800      0.625

Per-format:
  pdf       n=1  recall=1.000  precision=1.000  f1=1.000
  docx      n=1  recall=1.000  precision=1.000  f1=1.000
  xlsx      n=1  recall=1.000  precision=1.000  f1=1.000
  html      n=1  recall=0.800  precision=0.625  f1=0.702

Overall: recall=0.950  precision=0.906
✓ THRESHOLD PASS  (recall>=0.85, precision>=0.70)
```

### Chroma 语义检索（实测）

| Query | Top-1 命中 | block_type |
|---|---|---|
| "What was APAC revenue?" | PDF 表格 `Region / Revenue / Growth` | table ✓ |
| "new hire first week tasks" | DOCX `Week 1 Tasks` 标题 | heading ✓ |
| "Engineering team size Q3" | XLSX Headcount sheet | table ✓ |
| "laptop product image" | HTML `<img alt="Pro Laptop hero image">` | image ✓ |

22 条记录全部入库，支持 `block_type=image` 等结构化过滤。

## 🎯 解决的生产问题

| 痛点 | 怎么解决 |
|---|---|
| PDF 表格怎么抽 | `pdfplumber.extract_tables()` → 保留表头 + 行 → 转 markdown 存 text 字段 |
| PDF 图片怎么办 | pypdfium2 渲染整页 → 裁 bbox → PaddleOCR 抽出图内文字 → 真存 text 字段 |
| HTML 图片怎么办 | 拼本地路径 → 直接 PaddleOCR，同时 alt + figcaption 拼到 text |
| Excel 多 sheet | 每个 sheet 独立 `TableBlock`，`page_or_sheet = sheet_name` |
| Excel 合并单元格 | 左上角值填所有 sub-cells（用户在 UI 看到的） |
| Word 表格 vs 段落顺序 | 按 body 顺序遍历，段落和表格按文档流交替产出 |
| HTML 噪声 | `script/style/nav/footer/aside` 整段 decompose；`<article>` / `<main>` 提权 |
| 跨格式检索 | Block 都进 Chroma 一条记录；metadata 含 `source_format` + `block_type` 可过滤 |
| 重复入库 | `content_hash` 去重；同一 Block 二次 add 不增记录 |
| 持久化 | `PersistentClient` 写到 `./chroma_db/` 磁盘目录，重启可恢复 |

## 🛠 API 速查

```python
from src.loaders import load
from src.chroma_store import P5ChromaStore
from src.models import BlockType

# 抽
doc = load(Path("report.pdf"))  # 自动按后缀
for blk in doc.blocks:
    print(blk.block_type, blk.text[:60])

# 写
store = P5ChromaStore("./chroma_db", embed_fn=ollama_embed)
n = store.add_document(doc)  # 返回新增条数

# 查
results = store.query("APAC revenue", k=3)
results = store.query("image", k=3, where={"block_type": "image"})
```

## ⚠️ 已知限制 / 可补强

| 限制 | 影响 | 补强方向 |
|---|---|---|
| OCR 后端是 PaddleOCR（PP-OCRv4，中英兼顾）| 首次调用需下载模型（~10MB），离线环境要预置 | 版面分析用 PP-Structure / 换多模态 LLM |
| 图 OCR 准确率受分辨率 / 字体影响 | 小字 / 手写图识别差 | 升级到 paddleocr / 多模态 LLM |
| 无版面分析 | 双栏 PDF 文本顺序乱 | 接 `pdfplumber.find_words()` + 自定义排序 |
| Excel 公式只取 cached value | 改了未打开的公式 None | 加 `data_only=False` 路径返回 formula |
| Ground truth 只有 4 份 | 评测样本太少 | 加更多公开样本（财报、政府文档）+ 自动校验 |
| 无扫描件支持 | 扫描 PDF 全空 | 多页 OCR pipeline（每页整张识别）|

## 🔗 在 P1 / 面试里怎么用

**简历故事**：面试被问"你们怎么支持 PDF/Word/Excel"——直接打开 `p5-multi-format-loader/`，展示：
1. 4 个 loader（代码不超过 80 行/个）
2. 统一 `Block` 数据模型（跨格式 metadata 一致）
3. Chroma 写入 + 语义检索 demo
4. 真实 ground truth + recall/precision 评测报告

**在 P1 里的位置**：P1 现在只读 `.md`，未来可加：
```python
# P1 主项目里加一行：
docs = [load(p) for p in kb_dir.glob("*")]
P5ChromaStore("./chroma_db").add_documents(docs)
# 然后 P1 ask() 改用 Chroma.query() 替代 numpy + BM25
```

## 📜 License

训练营内部资料，私有。