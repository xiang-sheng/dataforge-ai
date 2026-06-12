"""Tests for src.warehouse.embedding — schema embedding & similarity."""

from unittest.mock import MagicMock

import duckdb
import numpy as np
from src.warehouse.embedding import (
    CandidatePair,
    SchemaEmbedder,
    TableSchema,
    extract_schemas_from_db,
)

# --- TableSchema ---


class TestTableSchema:
    def test_basic(self):
        s = TableSchema(table_name="orders", columns=[("id", "INTEGER"), ("amount", "DECIMAL")])
        assert s.table_name == "orders"
        assert len(s.columns) == 2
        assert s.row_count == 0

    def test_with_comment(self):
        s = TableSchema(
            table_name="users",
            columns=[("id", "INTEGER")],
            row_count=100,
            table_comment="用户主表",
        )
        assert s.row_count == 100
        assert s.table_comment == "用户主表"


# --- SchemaEmbedder.schema_to_text ---


class TestSchemaToText:
    def test_basic_format(self):
        s = TableSchema(
            table_name="orders",
            columns=[("order_id", "BIGINT"), ("amount", "DECIMAL(18,2)")],
            row_count=1200,
        )
        text = SchemaEmbedder.schema_to_text(s)
        assert "orders" in text
        assert "order_id BIGINT" in text
        assert "amount DECIMAL(18,2)" in text
        assert "1200 rows" in text

    def test_with_comment(self):
        s = TableSchema(
            table_name="users",
            columns=[("id", "INTEGER")],
            row_count=500,
            table_comment="用户主表",
        )
        text = SchemaEmbedder.schema_to_text(s)
        assert "用户主表" in text


# --- CandidatePair ---


class TestCandidatePair:
    def test_high_redundancy_verdict(self):
        c = CandidatePair("a", "b", 0.92, 5, 5, 100, 100)
        assert "高度冗余" in c.verdict

    def test_partial_verdict(self):
        c = CandidatePair("a", "b", 0.65, 5, 5, 100, 100)
        assert "部分重叠" in c.verdict

    def test_low_verdict(self):
        c = CandidatePair("a", "b", 0.45, 5, 5, 100, 100)
        assert "低" in c.verdict


# --- SchemaEmbedder (with mocked model) ---


def _make_mock_model(texts_to_vectors: dict[str, list[float]]):
    """Create a mock SentenceTransformer that returns predefined vectors."""
    mock_model = MagicMock()

    def fake_encode(texts, **kwargs):
        vectors = []
        for text in texts:
            # Find the best matching key
            for key, vec in texts_to_vectors.items():
                if key in text:
                    vectors.append(vec)
                    break
            else:
                # Default: zero vector
                vectors.append([0.0] * 3)
        return np.array(vectors)

    mock_model.encode = MagicMock(side_effect=fake_encode)
    return mock_model


class TestSchemaEmbedder:
    def test_encode_returns_matrix(self):
        """Encoding should return an (N, dim) numpy array."""
        mock_model = _make_mock_model({
            "orders": [1.0, 0.0, 0.0],
            "users": [0.0, 1.0, 0.0],
        })
        embedder = SchemaEmbedder()
        embedder._model = mock_model

        schemas = [
            TableSchema("orders", [("id", "BIGINT"), ("amount", "DECIMAL")], 1000),
            TableSchema("users", [("user_id", "BIGINT"), ("name", "VARCHAR")], 500),
        ]
        result = embedder.encode(schemas)
        assert result.shape == (2, 3)

    def test_identical_schemas_high_similarity(self):
        """Two identical schemas should have similarity close to 1.0."""
        mock_model = _make_mock_model({
            "orders": [1.0, 0.5, 0.3],
            "orders_bak": [1.0, 0.5, 0.3],  # same vector
        })
        embedder = SchemaEmbedder()
        embedder._model = mock_model

        schemas = [
            TableSchema("orders", [("id", "BIGINT"), ("amount", "DECIMAL")], 1000),
            TableSchema("orders_bak", [("id", "BIGINT"), ("amount", "DECIMAL")], 1000),
        ]
        candidates = embedder.find_candidates(schemas, threshold=0.5)
        assert len(candidates) == 1
        assert candidates[0].similarity > 0.99

    def test_different_schemas_low_similarity(self):
        """Orthogonal schemas should have low similarity."""
        mock_model = _make_mock_model({
            "orders": [1.0, 0.0, 0.0],
            "logs": [0.0, 0.0, 1.0],  # orthogonal
        })
        embedder = SchemaEmbedder()
        embedder._model = mock_model

        schemas = [
            TableSchema("orders", [("order_id", "BIGINT"), ("total", "DECIMAL")], 1000),
            TableSchema("logs", [("log_id", "BIGINT"), ("message", "TEXT")], 50000),
        ]
        candidates = embedder.find_candidates(schemas, threshold=0.8)
        assert len(candidates) == 0

    def test_threshold_filtering(self):
        """Higher threshold should return fewer candidates."""
        mock_model = _make_mock_model({
            "table_0": [1.0, 0.0, 0.0],
            "table_1": [0.9, 0.1, 0.0],  # similar to table_0
            "table_2": [0.0, 0.0, 1.0],  # different
        })
        embedder = SchemaEmbedder()
        embedder._model = mock_model

        schemas = [
            TableSchema("table_0", [("id", "BIGINT")], 100),
            TableSchema("table_1", [("id", "BIGINT")], 100),
            TableSchema("table_2", [("pid", "INTEGER")], 500),
        ]
        high = embedder.find_candidates(schemas, threshold=0.9)
        low = embedder.find_candidates(schemas, threshold=0.1)
        assert len(high) <= len(low)

    def test_top_k_limit(self):
        """top_k should limit the number of returned candidates."""
        # All identical vectors → all pairs are candidates
        mock_model = MagicMock()
        mock_model.encode = MagicMock(
            return_value=np.array([[1.0, 0.0, 0.0]] * 5)
        )
        embedder = SchemaEmbedder()
        embedder._model = mock_model

        schemas = [
            TableSchema(f"table_{i}", [("id", "BIGINT")], 100)
            for i in range(5)
        ]
        candidates = embedder.find_candidates(schemas, threshold=0.3, top_k=3)
        assert len(candidates) <= 3

    def test_single_table_returns_empty(self):
        """A single table should return no candidates."""
        embedder = SchemaEmbedder()
        schemas = [TableSchema("only_one", [("id", "INTEGER")], 10)]
        candidates = embedder.find_candidates(schemas)
        assert candidates == []


# --- extract_schemas_from_db ---


class TestExtractSchemas:
    def test_extracts_tables(self):
        conn = duckdb.connect(":memory:")
        conn.execute("CREATE TABLE t1 (id INTEGER, name VARCHAR)")
        conn.execute("CREATE TABLE t2 (pid INTEGER, title VARCHAR, price DOUBLE)")
        conn.execute("INSERT INTO t1 VALUES (1, 'Alice')")

        schemas = extract_schemas_from_db(conn)
        assert len(schemas) == 2
        names = {s.table_name for s in schemas}
        assert "t1" in names
        assert "t2" in names

        t1 = next(s for s in schemas if s.table_name == "t1")
        assert len(t1.columns) == 2
        assert t1.row_count == 1

    def test_empty_db(self):
        conn = duckdb.connect(":memory:")
        schemas = extract_schemas_from_db(conn)
        assert schemas == []
