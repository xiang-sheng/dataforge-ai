"""
DataForge AI - Schema Embedding for Table Similarity Detection.

Converts table schemas into text representations, encodes them with
sentence-transformers, and computes cosine similarity to find
potentially redundant table pairs.

Used as a pre-filtering step before LLM-based deep analysis.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from itertools import combinations

import duckdb
import numpy as np

logger = logging.getLogger(__name__)

# Default embedding model — small, fast, good enough for schema matching
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


@dataclass
class TableSchema:
    """Lightweight schema representation for embedding."""
    table_name: str
    columns: list[tuple[str, str]]  # [(col_name, data_type), ...]
    row_count: int = 0
    table_comment: str = ""


@dataclass
class CandidatePair:
    """A pair of potentially redundant tables."""
    table_a: str
    table_b: str
    similarity: float
    columns_a: int
    columns_b: int
    rows_a: int
    rows_b: int

    @property
    def verdict(self) -> str:
        if self.similarity >= 0.8:
            return "⚠ 高度冗余"
        elif self.similarity >= 0.6:
            return "△ 部分重叠"
        else:
            return "○ 低相似度"


class SchemaEmbedder:
    """Encodes table schemas into vectors and finds similar pairs.

    Uses sentence-transformers with a configurable model.
    The model is loaded lazily on first use and cached.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL):
        self._model_name = model_name
        self._model = None

    def _load_model(self):
        """Lazy-load the embedding model."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading embedding model: %s", self._model_name)
            self._model = SentenceTransformer(self._model_name)
        return self._model

    @staticmethod
    def schema_to_text(schema: TableSchema) -> str:
        """Convert a table schema into a single text representation.

        Format: "table_name | col1 TYPE, col2 TYPE, ... | N rows | comment"
        This format gives the embedding model enough semantic signal
        to distinguish between different table structures.
        """
        col_str = ", ".join(
            f"{name} {dtype}" for name, dtype in schema.columns
        )
        parts = [schema.table_name, col_str, f"{schema.row_count} rows"]
        if schema.table_comment:
            parts.append(schema.table_comment)
        return " | ".join(parts)

    def encode(self, schemas: list[TableSchema]) -> np.ndarray:
        """Encode a list of table schemas into an (N, dim) matrix."""
        model = self._load_model()
        texts = [self.schema_to_text(s) for s in schemas]
        embeddings = model.encode(texts, show_progress_bar=False)
        return np.array(embeddings)

    def find_candidates(
        self,
        schemas: list[TableSchema],
        threshold: float = 0.5,
        top_k: int = 20,
    ) -> list[CandidatePair]:
        """Find potentially redundant table pairs.

        Args:
            schemas: List of table schemas to compare.
            threshold: Minimum cosine similarity (0-1).
            top_k: Maximum number of candidate pairs to return.

        Returns:
            List of CandidatePair sorted by similarity descending.
        """
        if len(schemas) < 2:
            return []

        embeddings = self.encode(schemas)

        # Normalize for cosine similarity
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)  # avoid division by zero
        normalized = embeddings / norms

        # Cosine similarity matrix
        sim_matrix = normalized @ normalized.T

        # Extract upper triangle pairs, filter by threshold
        candidates: list[CandidatePair] = []
        for i, j in combinations(range(len(schemas)), 2):
            sim = float(sim_matrix[i][j])
            if sim >= threshold:
                sa, sb = schemas[i], schemas[j]
                candidates.append(CandidatePair(
                    table_a=sa.table_name,
                    table_b=sb.table_name,
                    similarity=sim,
                    columns_a=len(sa.columns),
                    columns_b=len(sb.columns),
                    rows_a=sa.row_count,
                    rows_b=sb.row_count,
                ))

        # Sort by similarity descending
        candidates.sort(key=lambda c: -c.similarity)
        return candidates[:top_k]


def extract_schemas_from_db(conn: duckdb.DuckDBPyConnection) -> list[TableSchema]:
    """Extract all table schemas from a DuckDB connection.

    Args:
        conn: A DuckDB connection object.

    Returns:
        List of TableSchema for every table in the main schema.
    """
    # Get all tables
    tables = conn.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'main'
        ORDER BY table_name
    """).fetchall()

    schemas: list[TableSchema] = []
    for (table_name,) in tables:
        # Get columns
        cols = conn.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = ? AND table_schema = 'main'
            ORDER BY ordinal_position
        """, [table_name]).fetchall()

        # Get row count — table_name comes from information_schema so is safe,
        # but we still quote it to handle unusual names.
        try:
            cnt = conn.execute(
                f'SELECT COUNT(*) FROM "{table_name}"'
            ).fetchone()
            row_count = cnt[0] if cnt else 0
        except duckdb.Error:
            row_count = 0

        schemas.append(TableSchema(
            table_name=table_name,
            columns=cols,
            row_count=row_count,
        ))

    return schemas
