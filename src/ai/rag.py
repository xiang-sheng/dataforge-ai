# -*- coding: utf-8 -*-
"""
DataForge AI - RAG (Retrieval Augmented Generation) context retrieval.

Provides schema-aware context building for SQL generation by combining
vector-similarity search over stored schema documents with table statistics,
sample queries, and warehouse-layer metadata.  Uses FAISS as the vector store
backend and supports both HuggingFace and OpenAI embedding models.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class SchemaDocument(BaseModel):
    """A single database schema document eligible for embedding and retrieval.

    Each ``SchemaDocument`` captures the full definition of one table -- its
    DDL, column descriptions, sample queries, and arbitrary metadata -- so
    that the RAG pipeline can surface the most relevant tables for a given
    natural-language question.

    Attributes:
        table_name: The table name (optionally schema-qualified).
        database: The database / catalog the table belongs to.
        ddl: The full ``CREATE TABLE`` DDL statement.
        column_descriptions: Mapping of column name to its description.
        sample_queries: Representative queries commonly run against this table.
        metadata: Arbitrary key-value metadata (e.g. warehouse layer, owner,
            row count).
    """

    table_name: str = Field(description="The table name (optionally schema-qualified).")
    database: str = Field(default="", description="The database / catalog the table belongs to.")
    ddl: str = Field(default="", description="The full CREATE TABLE DDL statement.")
    column_descriptions: Dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of column name to its description.",
    )
    sample_queries: List[str] = Field(
        default_factory=list,
        description="Representative queries commonly run against this table.",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary key-value metadata (warehouse layer, owner, row count, etc.).",
    )

    @property
    def unique_id(self) -> str:
        """Return a deterministic unique identifier for this document.

        The ID is a SHA-256 hex digest derived from the combination of
        ``database`` and ``table_name``, ensuring consistent IDs across
        re-indexing runs.
        """
        raw = f"{self.database}.{self.table_name}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def to_embedding_text(self) -> str:
        """Serialize the document into a text representation suitable for embedding.

        The output combines the DDL, column descriptions, sample queries, and
        metadata into a single string that captures the semantic meaning of
        the table schema.

        Returns:
            A multi-line text block ready for embedding.
        """
        parts: List[str] = []

        # Header
        if self.database:
            parts.append(f"Table: {self.database}.{self.table_name}")
        else:
            parts.append(f"Table: {self.table_name}")

        # DDL
        if self.ddl:
            parts.append(f"DDL:\n{self.ddl}")

        # Column descriptions
        if self.column_descriptions:
            col_lines = [
                f"  {col}: {desc}" for col, desc in self.column_descriptions.items()
            ]
            parts.append("Columns:\n" + "\n".join(col_lines))

        # Sample queries
        if self.sample_queries:
            query_lines = [f"  - {q}" for q in self.sample_queries[:5]]
            parts.append("Sample Queries:\n" + "\n".join(query_lines))

        # Metadata
        if self.metadata:
            meta_lines = [
                f"  {key}: {value}" for key, value in self.metadata.items()
            ]
            parts.append("Metadata:\n" + "\n".join(meta_lines))

        return "\n\n".join(parts)


class RAGContext(BaseModel):
    """Aggregated context assembled for a single SQL generation request.

    Combines relevant schema documents, table statistics, sample queries,
    and warehouse-layer information into a single object that the prompt
    builder can format and inject into the LLM request.

    Attributes:
        relevant_schemas: The schema documents most relevant to the query.
        table_stats: Per-table statistics (row count, size, column stats).
        sample_queries: Curated example queries related to the question.
        warehouse_layer_info: Optional description of the warehouse layer
            context (ODS / DWD / DWS / ADS conventions).
        formatted_context: Pre-formatted text ready for direct injection
            into the LLM prompt.
    """

    relevant_schemas: List[SchemaDocument] = Field(
        default_factory=list,
        description="The schema documents most relevant to the query.",
    )
    table_stats: Dict[str, Any] = Field(
        default_factory=dict,
        description="Per-table statistics (row count, size, column stats).",
    )
    sample_queries: List[str] = Field(
        default_factory=list,
        description="Curated example queries related to the question.",
    )
    warehouse_layer_info: Optional[str] = Field(
        default=None,
        description="Optional description of the warehouse layer context.",
    )
    formatted_context: str = Field(
        default="",
        description="Pre-formatted text ready for direct injection into the LLM prompt.",
    )


# ---------------------------------------------------------------------------
# Schema vector store (FAISS-backed)
# ---------------------------------------------------------------------------


class SchemaVectorStore:
    """FAISS-backed vector store for schema documents.

    Embeds schema documents using a configurable embedding model and stores
    them in a FAISS index for fast semantic similarity search.  Supports
    lazy initialization of the embedding model, persistence to disk, and
    incremental additions.

    Args:
        embedding_model: Name of the embedding model to use.  Defaults to
            ``sentence-transformers/all-MiniLM-L6-v2`` which provides a good
            balance of speed and quality for code/schema embeddings.
        embedding_provider: Either ``huggingface`` or ``openai``.  Determines
            which embedding backend is initialized.
        embedding_dimension: Dimensionality of the embedding vectors.
            Must match the chosen model's output dimension.

    Usage::

        store = SchemaVectorStore()
        store.add_schemas([schema_doc_1, schema_doc_2])
        results = store.search("Show me total revenue by month", top_k=3)
        for doc in results:
            print(doc.table_name)
    """

    def __init__(
        self,
        embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        embedding_provider: str = "huggingface",
        embedding_dimension: int = 384,
    ) -> None:
        self._embedding_model_name = embedding_model
        self._embedding_provider = embedding_provider
        self._embedding_dimension = embedding_dimension

        # Lazy-initialized components
        self._embeddings: Optional[Any] = None
        self._faiss_index: Optional[Any] = None  # faiss.IndexFlatL2
        self._documents: List[SchemaDocument] = []
        self._id_to_index: Dict[str, int] = {}

    # -- Properties ---------------------------------------------------------

    @property
    def document_count(self) -> int:
        """Return the number of documents currently stored."""
        return len(self._documents)

    @property
    def is_initialized(self) -> bool:
        """Return ``True`` if both the embedding model and FAISS index are ready."""
        return self._embeddings is not None and self._faiss_index is not None

    # -- Embedding model initialization (lazy) --------------------------------

    def _ensure_embeddings(self) -> Any:
        """Lazily initialize the embedding model on first use.

        Returns:
            The initialized embeddings model instance.

        Raises:
            ImportError: If the required embedding package is not installed.
        """
        if self._embeddings is not None:
            return self._embeddings

        if self._embedding_provider == "openai":
            try:
                from langchain_openai import OpenAIEmbeddings
            except ImportError:
                try:
                    from langchain_community.embeddings import OpenAIEmbeddings
                except ImportError as exc:
                    raise ImportError(
                        "The 'langchain-openai' or 'langchain-community' package is "
                        "required for OpenAI embeddings.  Install with:  "
                        "pip install langchain-openai"
                    ) from exc
            self._embeddings = OpenAIEmbeddings(model=self._embedding_model_name)
            logger.info(
                "Initialized OpenAI embeddings: %s",
                self._embedding_model_name,
            )
        else:
            # Default: HuggingFace / sentence-transformers
            try:
                from langchain_community.embeddings import HuggingFaceEmbeddings
            except ImportError:
                try:
                    from langchain_huggingface import HuggingFaceEmbeddings
                except ImportError as exc:
                    raise ImportError(
                        "The 'langchain-community' or 'langchain-huggingface' package "
                        "is required for HuggingFace embeddings.  Install with:  "
                        "pip install langchain-community sentence-transformers"
                    ) from exc
            self._embeddings = HuggingFaceEmbeddings(
                model_name=self._embedding_model_name,
            )
            logger.info(
                "Initialized HuggingFace embeddings: %s",
                self._embedding_model_name,
            )

        return self._embeddings

    def _ensure_faiss_index(self) -> Any:
        """Lazily initialize the FAISS index on first use.

        Returns:
            The initialized FAISS index instance.

        Raises:
            ImportError: If the ``faiss-cpu`` package is not installed.
        """
        if self._faiss_index is not None:
            return self._faiss_index

        try:
            import faiss
        except ImportError as exc:
            raise ImportError(
                "The 'faiss-cpu' package is required for SchemaVectorStore.  "
                "Install it with:  pip install faiss-cpu"
            ) from exc

        self._faiss_index = faiss.IndexFlatL2(self._embedding_dimension)
        logger.info(
            "Initialized FAISS IndexFlatL2 with dimension %d",
            self._embedding_dimension,
        )
        return self._faiss_index

    # -- Embedding helper ---------------------------------------------------

    def _embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Embed a list of text strings using the configured embedding model.

        Args:
            texts: The text strings to embed.

        Returns:
            A list of embedding vectors, one per input text.
        """
        embeddings = self._ensure_embeddings()

        # Both LangChain HuggingFaceEmbeddings and OpenAIEmbeddings expose
        # ``embed_documents`` for batch embedding.
        if hasattr(embeddings, "embed_documents"):
            return embeddings.embed_documents(texts)

        # Fallback for raw sentence-transformers models
        if hasattr(embeddings, "encode"):
            vectors = embeddings.encode(texts, show_progress_bar=False)
            return [v.tolist() for v in vectors]

        raise RuntimeError(
            "The configured embedding model does not expose 'embed_documents' "
            "or 'encode'.  Please use a LangChain-compatible embedding class."
        )

    def _embed_query(self, text: str) -> List[float]:
        """Embed a single query text.

        Args:
            text: The query string to embed.

        Returns:
            The embedding vector for the query.
        """
        embeddings = self._ensure_embeddings()

        if hasattr(embeddings, "embed_query"):
            return embeddings.embed_query(text)

        # Fallback: use embed_documents with a single element
        vectors = self._embed_texts([text])
        return vectors[0]

    # -- Public API ---------------------------------------------------------

    def add_schemas(self, schemas: List[SchemaDocument]) -> None:
        """Embed and store a batch of schema documents.

        Duplicate documents (identified by ``unique_id``) are silently
        skipped to support idempotent re-indexing.

        Args:
            schemas: The schema documents to add.
        """
        if not schemas:
            return

        # Filter out duplicates
        new_schemas: List[SchemaDocument] = []
        for doc in schemas:
            if doc.unique_id not in self._id_to_index:
                new_schemas.append(doc)

        if not new_schemas:
            logger.debug("All %d schemas already exist in the store; skipping.", len(schemas))
            return

        # Generate embedding text for each new document
        texts = [doc.to_embedding_text() for doc in new_schemas]

        # Embed all texts at once
        vectors = self._embed_texts(texts)

        # Add to FAISS index
        import numpy as np

        index = self._ensure_faiss_index()
        vectors_np = np.array(vectors, dtype="float32")
        index.add(vectors_np)

        # Track documents
        for doc in new_schemas:
            idx = len(self._documents)
            self._documents.append(doc)
            self._id_to_index[doc.unique_id] = idx

        logger.info(
            "Added %d schema documents to vector store (total: %d).",
            len(new_schemas),
            len(self._documents),
        )

    def search(self, query: str, top_k: int = 5) -> List[SchemaDocument]:
        """Search for the most relevant schema documents given a query.

        Uses L2 (Euclidean) distance in the FAISS index to find the nearest
        neighbours of the query embedding.

        Args:
            query: The natural-language query to search for.
            top_k: Maximum number of results to return.

        Returns:
            A list of ``SchemaDocument`` objects ordered by relevance
            (most relevant first).  May contain fewer than ``top_k``
            results if the store has fewer documents.
        """
        if not self._documents:
            logger.debug("Vector store is empty; returning no results.")
            return []

        # Embed the query
        query_vector = self._embed_query(query)

        import numpy as np

        index = self._ensure_faiss_index()
        query_np = np.array([query_vector], dtype="float32")

        actual_k = min(top_k, len(self._documents))
        distances, indices = index.search(query_np, actual_k)

        results: List[SchemaDocument] = []
        for idx in indices[0]:
            if 0 <= idx < len(self._documents):
                results.append(self._documents[idx])

        logger.debug(
            "Search for '%s' returned %d results (top_k=%d).",
            query[:60],
            len(results),
            top_k,
        )
        return results

    def get_relevant_context(
        self,
        question: str,
        db_type: str = "",
        top_k: int = 5,
    ) -> str:
        """Retrieve and format relevant schema context for a question.

        This is a convenience method that combines ``search`` with text
        formatting to produce a prompt-ready context string.

        Args:
            question: The natural-language question.
            db_type: Optional database type filter (e.g. ``mysql``,
                ``postgresql``).  When provided, only documents whose
                ``metadata["db_type"]`` matches (or that have no db_type
                metadata) are included.
            top_k: Maximum number of schema documents to retrieve.

        Returns:
            A formatted string containing DDL and descriptions of the
            most relevant tables.
        """
        candidates = self.search(question, top_k=top_k * 2)

        # Apply db_type filter if specified
        if db_type:
            filtered: List[SchemaDocument] = []
            for doc in candidates:
                doc_db_type = doc.metadata.get("db_type", "")
                if not doc_db_type or doc_db_type.lower() == db_type.lower():
                    filtered.append(doc)
                if len(filtered) >= top_k:
                    break
            candidates = filtered
        else:
            candidates = candidates[:top_k]

        if not candidates:
            return "(No relevant schema context found.)"

        # Format context
        sections: List[str] = []
        for doc in candidates:
            section_parts: List[str] = []
            if doc.database:
                section_parts.append(f"-- Table: {doc.database}.{doc.table_name}")
            else:
                section_parts.append(f"-- Table: {doc.table_name}")

            if doc.ddl:
                section_parts.append(doc.ddl)

            if doc.column_descriptions:
                col_lines = [
                    f"--   {col}: {desc}"
                    for col, desc in doc.column_descriptions.items()
                ]
                section_parts.append("-- Column Descriptions:\n" + "\n".join(col_lines))

            if doc.sample_queries:
                query_lines = [f"--   {q}" for q in doc.sample_queries[:3]]
                section_parts.append("-- Sample Queries:\n" + "\n".join(query_lines))

            sections.append("\n".join(section_parts))

        return "\n\n".join(sections)

    def clear(self) -> None:
        """Remove all documents and reset the FAISS index.

        The embedding model is kept initialized for reuse.
        """
        self._documents.clear()
        self._id_to_index.clear()

        # Re-create the FAISS index to discard all vectors
        if self._faiss_index is not None:
            try:
                import faiss
                self._faiss_index = faiss.IndexFlatL2(self._embedding_dimension)
            except ImportError:
                self._faiss_index = None

        logger.info("Cleared all documents from vector store.")

    def save(self, path: str | Path) -> None:
        """Persist the vector store to disk.

        Saves the FAISS index, document metadata, and the ID mapping to
        the specified directory.  The embedding model is *not* serialized;
        it will be re-initialized on ``load``.

        Args:
            path: Directory path to save the store to.  Will be created
                if it does not exist.

        Raises:
            RuntimeError: If the store has not been initialized.
        """
        if not self.is_initialized:
            raise RuntimeError(
                "Cannot save an uninitialized vector store.  "
                "Add documents or load an existing store first."
            )

        save_dir = Path(path)
        save_dir.mkdir(parents=True, exist_ok=True)

        import faiss

        # Save FAISS index
        index_path = str(save_dir / "faiss.index")
        faiss.write_index(self._faiss_index, index_path)

        # Save documents as JSON
        docs_path = save_dir / "documents.json"
        docs_data = [doc.model_dump() for doc in self._documents]
        with open(docs_path, "w", encoding="utf-8") as fh:
            json.dump(docs_data, fh, indent=2, ensure_ascii=False)

        # Save ID mapping and config
        config = {
            "embedding_model": self._embedding_model_name,
            "embedding_provider": self._embedding_provider,
            "embedding_dimension": self._embedding_dimension,
            "id_to_index": self._id_to_index,
        }
        config_path = save_dir / "config.json"
        with open(config_path, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2, ensure_ascii=False)

        logger.info(
            "Saved vector store to %s (%d documents).",
            save_dir,
            len(self._documents),
        )

    @classmethod
    def load(cls, path: str | Path) -> "SchemaVectorStore":
        """Load a previously saved vector store from disk.

        Re-initializes the embedding model and restores the FAISS index,
        documents, and ID mapping from the saved directory.

        Args:
            path: Directory path where the store was previously saved.

        Returns:
            A ``SchemaVectorStore`` instance with all data restored.

        Raises:
            FileNotFoundError: If the expected files are not found in the
                directory.
        """
        save_dir = Path(path)
        config_path = save_dir / "config.json"
        docs_path = save_dir / "documents.json"
        index_path = str(save_dir / "faiss.index")

        if not config_path.exists():
            raise FileNotFoundError(
                f"Config file not found at {config_path}.  "
                f"Ensure the path points to a valid saved vector store."
            )
        if not docs_path.exists():
            raise FileNotFoundError(
                f"Documents file not found at {docs_path}."
            )

        # Load config
        with open(config_path, "r", encoding="utf-8") as fh:
            config = json.load(fh)

        store = cls(
            embedding_model=config["embedding_model"],
            embedding_provider=config["embedding_provider"],
            embedding_dimension=config["embedding_dimension"],
        )
        store._id_to_index = config["id_to_index"]

        # Load documents
        with open(docs_path, "r", encoding="utf-8") as fh:
            docs_data = json.load(fh)
        store._documents = [SchemaDocument.model_validate(d) for d in docs_data]

        # Load FAISS index
        try:
            import faiss
            store._faiss_index = faiss.read_index(index_path)
        except ImportError as exc:
            raise ImportError(
                "The 'faiss-cpu' package is required to load a saved "
                "vector store.  Install it with:  pip install faiss-cpu"
            ) from exc

        logger.info(
            "Loaded vector store from %s (%d documents).",
            save_dir,
            len(store._documents),
        )
        return store


# ---------------------------------------------------------------------------
# RAG context builder
# ---------------------------------------------------------------------------


class RAGContextBuilder:
    """Builds rich context for SQL generation by combining multiple sources.

    Orchestrates schema retrieval from the vector store, table statistics
    lookup, sample query collection, and warehouse-layer information to
    produce a comprehensive ``RAGContext`` for prompt injection.

    Args:
        vector_store: An initialized ``SchemaVectorStore`` to search for
            relevant schemas.
        table_stats_provider: Optional callable that, given a connection ID
            and table name, returns a dictionary of table statistics.
        warehouse_layer_provider: Optional callable that, given a connection
            ID, returns a string describing the warehouse layer conventions.

    Usage::

        builder = RAGContextBuilder(vector_store)
        context = builder.build_context(
            question="Show me top 10 customers by revenue",
            connection_id="conn_abc123",
        )
        prompt_text = builder.format_for_prompt(context)
    """

    def __init__(
        self,
        vector_store: SchemaVectorStore,
        table_stats_provider: Optional[Any] = None,
        warehouse_layer_provider: Optional[Any] = None,
    ) -> None:
        self._vector_store = vector_store
        self._table_stats_provider = table_stats_provider
        self._warehouse_layer_provider = warehouse_layer_provider

    # -- Public API ---------------------------------------------------------

    def build_context(
        self,
        question: str,
        connection_id: str = "",
        db_type: str = "",
        top_k: int = 5,
        include_stats: bool = True,
        include_sample_queries: bool = True,
        include_warehouse_info: bool = True,
        extra_tables: Optional[List[str]] = None,
    ) -> RAGContext:
        """Build a comprehensive context object for SQL generation.

        Retrieves relevant schema documents from the vector store, optionally
        enriches them with table statistics, sample queries, and warehouse
        layer information, and assembles everything into a ``RAGContext``.

        Args:
            question: The natural-language question the user wants answered.
            connection_id: Identifier for the database connection, used to
                look up table statistics and warehouse metadata.
            db_type: Optional database type filter for schema search
                (e.g. ``mysql``, ``postgresql``).
            top_k: Maximum number of schema documents to retrieve.
            include_stats: Whether to include table statistics in the context.
            include_sample_queries: Whether to include sample queries from
                the schema documents.
            include_warehouse_info: Whether to include warehouse layer
                information.
            extra_tables: Additional table names to include in the context
                regardless of search relevance.

        Returns:
            A ``RAGContext`` containing all assembled information.
        """
        # Step 1: Retrieve relevant schemas from vector store
        relevant_schemas = self._vector_store.search(question, top_k=top_k)

        # Step 2: Inject extra tables if requested
        if extra_tables:
            existing_names = {doc.table_name.lower() for doc in relevant_schemas}
            extra_results = self._vector_store.search(
                " ".join(extra_tables), top_k=top_k * 2
            )
            for doc in extra_results:
                if doc.table_name.lower() in [t.lower() for t in extra_tables]:
                    if doc.table_name.lower() not in existing_names:
                        relevant_schemas.append(doc)
                        existing_names.add(doc.table_name.lower())

        # Step 3: Collect table statistics
        table_stats: Dict[str, Any] = {}
        if include_stats and self._table_stats_provider is not None:
            for doc in relevant_schemas:
                try:
                    stats = self._table_stats_provider(connection_id, doc.table_name)
                    if stats:
                        table_stats[doc.table_name] = stats
                except Exception as exc:
                    logger.debug(
                        "Failed to fetch stats for table '%s': %s",
                        doc.table_name,
                        exc,
                    )

        # Step 4: Collect sample queries
        sample_queries: List[str] = []
        if include_sample_queries:
            for doc in relevant_schemas:
                sample_queries.extend(doc.sample_queries[:3])
            # Deduplicate while preserving order
            seen: set = set()
            unique_queries: List[str] = []
            for q in sample_queries:
                q_lower = q.lower().strip()
                if q_lower not in seen:
                    seen.add(q_lower)
                    unique_queries.append(q)
            sample_queries = unique_queries[:10]

        # Step 5: Fetch warehouse layer information
        warehouse_info: Optional[str] = None
        if include_warehouse_info and self._warehouse_layer_provider is not None:
            try:
                warehouse_info = self._warehouse_layer_provider(connection_id)
            except Exception as exc:
                logger.debug("Failed to fetch warehouse layer info: %s", exc)

        # Step 6: Build formatted context
        context = RAGContext(
            relevant_schemas=relevant_schemas,
            table_stats=table_stats,
            sample_queries=sample_queries,
            warehouse_layer_info=warehouse_info,
        )
        context.formatted_context = self.format_for_prompt(context)

        return context

    def format_for_prompt(self, context: RAGContext) -> str:
        """Format a ``RAGContext`` into a prompt-ready text string.

        Produces a structured, human-readable text block that can be
        directly injected into the system or user prompt for the LLM.

        Args:
            context: The ``RAGContext`` to format.

        Returns:
            A multi-section formatted string with schema definitions,
            statistics, sample queries, and warehouse info.
        """
        sections: List[str] = []

        # --- Relevant Schema ---
        if context.relevant_schemas:
            schema_lines: List[str] = ["## Relevant Database Schema\n"]
            for doc in context.relevant_schemas:
                header = f"### {doc.table_name}"
                if doc.database:
                    header = f"### {doc.database}.{doc.table_name}"
                schema_lines.append(header)

                if doc.ddl:
                    schema_lines.append(f"```sql\n{doc.ddl}\n```")

                if doc.column_descriptions:
                    schema_lines.append("\n**Column Descriptions:**")
                    for col, desc in doc.column_descriptions.items():
                        schema_lines.append(f"- `{col}`: {desc}")

                schema_lines.append("")  # blank separator

            sections.append("\n".join(schema_lines))

        # --- Table Statistics ---
        if context.table_stats:
            stats_lines: List[str] = ["## Table Statistics\n"]
            for table_name, stats in context.table_stats.items():
                stats_lines.append(f"### {table_name}")
                if isinstance(stats, dict):
                    for key, value in stats.items():
                        stats_lines.append(f"- **{key}**: {value}")
                else:
                    stats_lines.append(f"- {stats}")
                stats_lines.append("")
            sections.append("\n".join(stats_lines))

        # --- Sample Queries ---
        if context.sample_queries:
            query_lines: List[str] = ["## Related Query Examples\n"]
            for i, query in enumerate(context.sample_queries, 1):
                query_lines.append(f"{i}. `{query}`")
            sections.append("\n".join(query_lines))

        # --- Warehouse Layer Info ---
        if context.warehouse_layer_info:
            sections.append(
                f"## Data Warehouse Context\n\n{context.warehouse_layer_info}"
            )

        if not sections:
            return "(No relevant context available.)"

        return "\n\n---\n\n".join(sections)
