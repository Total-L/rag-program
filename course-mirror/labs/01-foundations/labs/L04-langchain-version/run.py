"""L04 — LangChain 复刻 L03：同一链路，对比代码量与可读性。

运行：
    python 01-foundations/labs/L04-langchain-version/run.py

对比 L03 后的观察写到 artifacts/L04/report.md
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# 跟 L03 共用同一份语料 + system prompt(见 ../_shared/lab_corpus.py)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _shared.lab_corpus import DOCS_TEXT as CORPUS, SYSTEM_PROMPT as SYSTEM  # noqa: E402

ART = Path("artifacts/L04")
ART.mkdir(parents=True, exist_ok=True)


def main() -> None:
    print("════════════════════════════════════════════")
    print("  L04 — LangChain 复刻最小 RAG")
    print("════════════════════════════════════════════\n")

    try:
        from langchain_community.embeddings import OllamaEmbeddings
        from langchain_community.llms import Ollama
        from langchain_community.vectorstores import Chroma
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.runnables import RunnablePassthrough
        from langchain_core.output_parsers import StrOutputParser
    except ImportError as e:
        print(f"✗ LangChain 依赖未安装：{e}")
        print("  跑：pip install langchain langchain-community chromadb")
        return

    # 1. 嵌入 + 向量库
    embed = OllamaEmbeddings(model=os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text"))
    vectordb = Chroma.from_texts(CORPUS, embedding=embed, collection_name="l04_minimal")
    retriever = vectordb.as_retriever(search_kwargs={"k": 3})

    # 2. LLM
    llm = Ollama(model=os.environ.get("OLLAMA_LLM_MODEL", "llama3.1:8b"),
                 temperature=0.0, num_predict=512)

    # 3. Prompt + Chain（LCEL 风格）
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM),
        ("human", "上下文：\n{context}\n\n问题：{question}\n\n回答："),
    ])

    def fmt(docs):
        return "\n".join(f"[{i+1}] {d.page_content}" for i, d in enumerate(docs))

    chain = (
        {"context": retriever | fmt, "question": RunnablePassthrough()}
        | prompt
        | llm
        | StrOutputParser()
    )

    # 4. 跑 slim golden 题库 + 记录 (retrieved_docs, answer, latency, abstained)
    golden_path = Path(__file__).resolve().parents[1] / "_shared" / "lab_golden.jsonl"
    queries = [json.loads(line) for line in golden_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    log = []
    for q in queries:
        qid = q["id"]
        question = q["question"]
        must_abstain = q.get("must_abstain", False)
        print(f"\n[{qid}] Q: {question}")
        # 取回 top-k 文档(text + metadata.doc_id 拿不到时退回 page_content 检索词匹配)
        t0 = time.time()
        try:
            retrieved = retriever.invoke(question)
            retrieved_docs = [d.page_content for d in retrieved]
            ans = chain.invoke(question)
        except Exception as e:
            ans = f"（生成失败：{e}）"
            retrieved_docs = []
        latency_ms = round((time.time() - t0) * 1000, 1)
        abstained = any(kw in ans for kw in ("无法在知识库中找到", "找不到相关信息", "我不确定", "没有足够信息"))
        print(f"A: {ans[:200]}")
        log.append({
            "id": qid,
            "question": question,
            "must_abstain": must_abstain,
            "retrieved_docs": retrieved_docs,
            "answer": ans,
            "abstained": abstained,
            "latency_ms": latency_ms,
        })

    raw_path = ART / "l04_raw.jsonl"
    raw_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in log) + "\n",
        encoding="utf-8",
    )
    print(f"\n✓ 写 l04_raw.jsonl ({len(log)} 题) → {raw_path}")

    # 5. 报告
    (ART / "report.md").write_text(
        "# L04 — LangChain 复刻报告\n\n"
        "## 与 L03（手写）对比\n\n"
        "| 维度 | L03 手写 | L04 LangChain |\n"
        "|---|---|---|\n"
        "| 代码行数 | ~120 | ~60 |\n"
        "| 检索 | 自定义 cosine | `Chroma.as_retriever` |\n"
        "| 提示 | 字符串拼接 | `ChatPromptTemplate` |\n"
        "| Chain | 显式调用 | LCEL `|` 算子 |\n"
        "| 切换 LLM | 改 subprocess | 改 `Ollama(model=...)` |\n"
        "| 流式输出 | 自己写 | `.stream()` |\n"
        "| Token 计数 | 自己 | `CallbackHandler` |\n"
        "| 依赖 | Ollama + mmx | + langchain 17 个子包 |\n\n"
        "## 学到什么\n"
        "- LangChain 解决的不是'能不能跑'，而是'组件拼装 + 可观测性 + 可替换性'\n"
        "- 框架不神秘——L03 你已见过每一步\n"
        "- 生产中常：LangChain 起手 + 关键模块手写（如 rerank、retrieval）\n",
        encoding="utf-8",
    )
    print(f"\n✓ L04 完成。报告 → {ART / 'report.md'}")


if __name__ == "__main__":
    main()
