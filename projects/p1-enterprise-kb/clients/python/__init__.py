"""rag_program SDK（Python）。

暴露 RagClient、RagAPIError、RagConnectionError、QueryResult、Citation。
"""

from .rag_client import (
    Citation,
    QueryResult,
    RagAPIError,
    RagClient,
    RagConnectionError,
)

__all__ = ["RagClient", "RagAPIError", "RagConnectionError", "QueryResult", "Citation"]
__version__ = "1.0.0"
