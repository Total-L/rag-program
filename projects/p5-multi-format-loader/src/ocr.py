"""P5 — OCR 模块：PaddleOCR 3.x 后端（生产级）。

为什么选 PaddleOCR 而不是 Tesseract / EasyOCR：
- 中文 / 复杂版面准确率远超 Tesseract（百度基于 PP-OCRv4）
- PaddleOCR 3.7 输出含 bbox + score，可信度过滤
- 同时支持 80+ 语言（lang='ch' / 'en' / 'japan' 等）
- 默认 CPU 推理，arm64 Mac 兼容（paddlepaddle 3.3+ 已修 M1 wheel）

设计原则（生产级）：
- 单例 lazy load：`get_ocr()` 第一次调时才实例化（构造函数会下载 ~30MB 模型）
- 缓存：同张图 sha256 → 二次 OCR 走缓存
- 错误降级：PaddleOCR 失败 / 不可用 → 返回空文本
- 后端可换：未来换 EasyOCR 只需替换 `_predict` 实现
- 沙盒兼容：临时文件写到项目内 .ocr_cache/
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_CACHE: dict[str, str] = {}
_LOCK = threading.Lock()

_OCR = None
_OCR_LOCK = threading.Lock()
_OCR_FAILED = False


def get_ocr(lang: str = "en"):
    """懒加载 PaddleOCR reader。"""
    global _OCR, _OCR_FAILED
    if _OCR is not None:
        return _OCR
    if _OCR_FAILED:
        return None
    with _OCR_LOCK:
        if _OCR is not None:
            return _OCR
        if _OCR_FAILED:
            return None
        try:
            from paddleocr import PaddleOCR  # type: ignore[import-not-found]

            log.info("PaddleOCR init: lang=%s", lang)
            _OCR = PaddleOCR(lang=lang, device="cpu", enable_mkldnn=False)
            return _OCR
        except Exception as e:  # noqa: BLE001
            log.warning("PaddleOCR init failed: %s", e)
            _OCR_FAILED = True
            return None


def _check_ready() -> bool:
    return get_ocr() is not None


def _predict(img_bytes_or_path, lang: str = "en") -> str:
    """调 PaddleOCR。接受文件路径或 PNG 字节流。

    返回合并后的纯文本（按识别顺序，行用 \\n 分，块按 score 过滤）。
    """
    ocr = get_ocr(lang)
    if ocr is None:
        return ""

    cleanup_path: Path | None = None
    try:
        if isinstance(img_bytes_or_path, bytes):
            cache_dir = Path(__file__).resolve().parents[1] / ".ocr_cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            cleanup_path = cache_dir / f"_ppocr_{os.getpid()}_{len(img_bytes_or_path)}.png"
            cleanup_path.write_bytes(img_bytes_or_path)
            target: str = str(cleanup_path)
        else:
            target = img_bytes_or_path

        # paddleocr 3.x 返回 list[OCRResult]，每个含 rec_texts/rec_scores
        results = ocr.predict(target)
        if not results:
            return ""

        # 取第一个 result 的 rec_texts（多页情况一般只有一条）
        result = results[0]
        rec_texts = result.get("rec_texts", []) if hasattr(result, "get") else []
        rec_scores = result.get("rec_scores", []) if hasattr(result, "get") else []

        # 过滤低分（< 0.5 通常是噪声）
        lines = []
        for i, txt in enumerate(rec_texts):
            score = float(rec_scores[i]) if i < len(rec_scores) else 0.0
            if score < 0.5:
                continue
            t = str(txt).strip()
            if t and len(t) > 1:  # 拒绝单字符噪声
                lines.append(t)
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        log.warning("PaddleOCR predict failed: %s", e)
        return ""
    finally:
        if cleanup_path and cleanup_path.exists():
            try:
                cleanup_path.unlink()
            except OSError:  # noqa: BLE001
                pass


def ocr_image(image_path: str | Path) -> str:
    """对图片做 OCR。返回清洗后的多行文本。"""
    p = Path(image_path)
    if not p.exists():
        return ""
    sig = hashlib.sha256(str(p.resolve()).encode()).hexdigest()[:16]
    with _LOCK:
        if sig in _CACHE:
            return _CACHE[sig]
    if not _check_ready():
        return ""
    text = _predict(str(p))
    with _LOCK:
        _CACHE[sig] = text
    return text


def ocr_pdf_region(image_bytes: bytes, hint: str = "") -> str:
    """给 PDF loader 专用：PNG 字节流 → OCR。

    hint 在 OCR 真出活时才拼前缀，避免"假阳性"。
    """
    text = _predict(image_bytes)
    if text:
        return f"{hint} | {text}" if hint else text
    return ""


def reset_cache() -> None:
    """测试用：清掉 OCR 缓存（不重置 reader）。"""
    with _LOCK:
        _CACHE.clear()


def reset_reader() -> None:
    """测试用：连 reader 一起重置。"""
    global _OCR, _OCR_FAILED
    with _OCR_LOCK:
        _OCR = None
        _OCR_FAILED = False
