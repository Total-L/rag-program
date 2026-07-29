"""L03 / L04 共用 8 篇闭域语料(同 topic、同内容、稳定的 doc_id)。

设计:
- doc_id 用 `lab-*` 前缀,避开 enterprise fixtures 的 `hr-*` / `it-*` / `product-*` 命名空间
  (那些 fixture 已 seed=42 漂移,golden_v1 跟实际内容对不上)
- topic 跟 L03 / L04 现有 CORPUS 一字不差,保留 demo 的连贯性
- 给 L03 run.py / L04 run.py / L03 run_eval.py 三处共用,避免 L03 改了 CORPUS
  但 L04 没改导致对照实验跑偏

来源:L03-minimal-rag/run.py 第 100-109 行的 CORPUS(直接抽出来,字面照搬)。
"""
from __future__ import annotations

CORPUS: list[dict] = [
    {"doc_id": "lab-hr-01", "topic": "年假",
     "text": "员工年假政策:司龄 1 年以下 5 天,1-3 年 10 天,3-5 年 15 天,5+ 年 20 天。"},
    {"doc_id": "lab-hr-02", "topic": "病假",
     "text": "病假需在 OA 提交申请,附医院证明;3 天以内部门审批,3 天以上 HRBP 审批。"},
    {"doc_id": "lab-hr-03", "topic": "差旅住宿",
     "text": "差旅住宿标准:一线城市 800 元/晚,二线 600 元,三线 400 元。"},
    {"doc_id": "lab-it-01", "topic": "VPN",
     "text": "VPN 接入:vpn.corp.example.com,首次登录需绑定手机令牌。"},
    {"doc_id": "lab-hr-04", "topic": "股权",
     "text": "限制性股票分 4 年 vest,cliff 1 年。"},
    {"doc_id": "lab-inc-01", "topic": "P0 事故",
     "text": "P0 事故 30 分钟内 mitigation,24 小时内 RCA。"},
    {"doc_id": "lab-rel-01", "topic": "发版",
     "text": "发版流程:周二封板、周三灰度 10%、周五 100%。"},
    {"doc_id": "lab-cs-01", "topic": "用户反馈",
     "text": "用户反馈 P0 1 小时响应。"},
]

# 索引视图(给 LCEL 链 / 老 L03 用)
DOCS_TEXT: list[str] = [d["text"] for d in CORPUS]
DOCS_BY_ID: dict[str, str] = {d["doc_id"]: d["text"] for d in CORPUS}
DOC_IDS: list[str] = [d["doc_id"] for d in CORPUS]

SYSTEM_PROMPT: str = (
    "你是公司知识库助手。仅根据提供的上下文回答用户问题。"
    "如果上下文不包含答案,请回答\"我无法在知识库中找到相关信息\"。"
    "\"编号引用\"格式:[1] [2] ..."
)