# -*- coding: utf-8 -*-
"""
DataForge AI - Prompt templates for AI-assisted data warehouse operations.

Contains carefully engineered prompt templates for SQL generation, data modeling,
query optimization, DDL generation, warehouse layer design, and ETL pipeline
planning.  Templates use Python f-string interpolation and can be further
customized at runtime via keyword overrides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Template container
# ---------------------------------------------------------------------------

@dataclass
class PromptTemplate:
    """A structured prompt template with metadata.

    Attributes:
        name: A short identifier for the template (e.g. ``sql_generation``).
        description: Human-readable description of the template's purpose.
        system_prompt: The system-level instruction that sets the AI's persona
            and constraints.
        user_template: The user-level prompt body with ``{placeholder}`` slots
            that will be filled at render time.
        examples: Optional few-shot examples, each a dict with ``input`` and
            ``output`` keys, used to improve generation quality.
        variables: List of expected variable names for documentation purposes.
    """

    name: str
    description: str
    system_prompt: str
    user_template: str
    examples: List[Dict[str, str]] = field(default_factory=list)
    variables: List[str] = field(default_factory=list)

    def render(self, **kwargs: Any) -> Dict[str, str]:
        """Render the template with the supplied variables.

        Args:
            **kwargs: Values for every ``{placeholder}`` in ``user_template``.

        Returns:
            A dict with ``system`` and ``user`` keys containing the rendered
            text.

        Raises:
            KeyError: If a required variable is missing.
        """
        rendered_user = self.user_template.format(**kwargs)

        # Append few-shot examples if present
        if self.examples:
            example_block = "\n\n--- Few-shot Examples ---\n"
            for idx, ex in enumerate(self.examples, start=1):
                example_block += f"\nExample {idx}:\n"
                example_block += f"Input: {ex['input']}\n"
                example_block += f"Output: {ex['output']}\n"
            rendered_user += example_block

        return {
            "system": self.system_prompt,
            "user": rendered_user,
        }


# ---------------------------------------------------------------------------
# System prompts (shared across templates)
# ---------------------------------------------------------------------------

SYSTEM_DATA_ENGINEER = (
    "You are an expert data engineer and data warehouse architect with deep "
    "knowledge of dimensional modeling, ETL/ELT pipelines, SQL optimization, "
    "and modern data stack best practices.  You are precise, thorough, and "
    "always explain your reasoning.  When generating SQL you follow the "
    "requested dialect strictly and include inline comments for complex logic."
)

SYSTEM_SQL_EXPERT = (
    "You are a senior SQL specialist fluent in PostgreSQL, MySQL, Hive, "
    "Spark SQL, ClickHouse, StarRocks, Doris, Oracle, SQL Server, and "
    "BigQuery dialects.  You produce clean, performant, and well-commented "
    "SQL.  You always consider NULL handling, data-type edge cases, and "
    "query-plan implications."
)

SYSTEM_MODEL_ADVISOR = (
    "You are a data warehouse modeling consultant with extensive experience "
    "in Kimball dimensional modeling, Data Vault 2.0, and OneData methodology. "
    "You provide actionable recommendations with clear trade-off analysis."
)


# ===========================================================================
# 1. SQL Generation from Natural Language
# ===========================================================================

SQL_GENERATION_TEMPLATE = PromptTemplate(
    name="sql_generation",
    description="Generate SQL from a natural-language question using the provided schema context.",
    system_prompt=SYSTEM_SQL_EXPERT,
    user_template=(
        "Given the following database schema, write a {dialect} SQL query that "
        "answers the user's question.\n\n"
        "## Database Schema\n"
        "```sql\n{schema}\n```\n\n"
        "## User Question\n"
        "{question}\n\n"
        "## Requirements\n"
        "- Generate only the SQL query, wrapped in a ```sql code block.\n"
        "- Use the {dialect} dialect.\n"
        "- Include inline comments for non-obvious logic.\n"
        "- Handle NULL values appropriately.\n"
        "- Use meaningful table aliases.\n"
        "- If the question is ambiguous, state your assumptions before the query.\n"
        "{extra_instructions}"
    ),
    variables=["dialect", "schema", "question", "extra_instructions"],
    examples=[
        {
            "input": (
                "Schema: CREATE TABLE orders (id INT, user_id INT, amount DECIMAL(10,2), "
                "created_at TIMESTAMP);\n"
                "Question: What is the total revenue per month for 2024?"
            ),
            "output": (
                "```sql\n"
                "SELECT\n"
                "    DATE_TRUNC('month', created_at) AS order_month,\n"
                "    SUM(amount) AS total_revenue\n"
                "FROM orders\n"
                "WHERE created_at >= '2024-01-01'\n"
                "  AND created_at <  '2025-01-01'\n"
                "GROUP BY DATE_TRUNC('month', created_at)\n"
                "ORDER BY order_month;\n"
                "```"
            ),
        },
    ],
)


# ===========================================================================
# 2. SQL Explanation
# ===========================================================================

SQL_EXPLANATION_TEMPLATE = PromptTemplate(
    name="sql_explanation",
    description="Explain what a SQL query does in plain language.",
    system_prompt=SYSTEM_SQL_EXPERT,
    user_template=(
        "Explain the following {dialect} SQL query step by step in plain language.\n\n"
        "```sql\n{sql}\n```\n\n"
        "Please cover:\n"
        "1. What each clause does (FROM, WHERE, JOIN, GROUP BY, etc.)\n"
        "2. The overall business logic the query implements.\n"
        "3. Any potential issues or edge cases (NULL handling, Cartesian products, etc.).\n"
        "4. An estimate of query complexity (simple / moderate / complex).\n"
    ),
    variables=["dialect", "sql"],
)


# ===========================================================================
# 3. SQL Translation between Dialects
# ===========================================================================

SQL_TRANSLATION_TEMPLATE = PromptTemplate(
    name="sql_translation",
    description="Translate a SQL query from one dialect to another.",
    system_prompt=SYSTEM_SQL_EXPERT,
    user_template=(
        "Translate the following SQL query from **{source_dialect}** to "
        "**{target_dialect}**.\n\n"
        "## Original Query ({source_dialect})\n"
        "```sql\n{sql}\n```\n\n"
        "## Requirements\n"
        "- Preserve the exact same business logic and output.\n"
        "- Replace dialect-specific functions with their equivalents.\n"
        "- Note any behavioral differences between the two dialects.\n"
        "- If an exact translation is not possible, provide the closest "
        "alternative and explain the gap.\n"
        "- Wrap the result in a ```sql code block.\n"
    ),
    variables=["source_dialect", "target_dialect", "sql"],
)


# ===========================================================================
# 4. Data Modeling Suggestions (Dimensional Modeling)
# ===========================================================================

DATA_MODELING_TEMPLATE = PromptTemplate(
    name="data_modeling",
    description="Suggest dimensional-model designs (star / snowflake schema) for given requirements.",
    system_prompt=SYSTEM_MODEL_ADVISOR,
    user_template=(
        "Design a dimensional data model for the following business requirements.\n\n"
        "## Business Process\n"
        "{business_process}\n\n"
        "## Key Entities / Source Tables\n"
        "{entities}\n\n"
        "## Existing Tables (if any)\n"
        "{existing_tables}\n\n"
        "## Design Goals\n"
        "- Target schema type: {schema_type} (star / snowflake / data-vault)\n"
        "- Primary query patterns: {query_patterns}\n\n"
        "## Deliverables\n"
        "1. List of **fact tables** with grain, measures, and foreign keys.\n"
        "2. List of **dimension tables** with key attributes and hierarchies.\n"
        "3. For each table, provide:\n"
        "   - Table name following `{naming_convention}` naming convention.\n"
        "   - Column definitions with data types.\n"
        "   - Primary key and foreign key constraints.\n"
        "   - Suggested partitioning and indexing strategy.\n"
        "4. A brief rationale for each design decision.\n"
        "{extra_instructions}"
    ),
    variables=[
        "business_process",
        "entities",
        "existing_tables",
        "schema_type",
        "query_patterns",
        "naming_convention",
        "extra_instructions",
    ],
    examples=[
        {
            "input": (
                "Business process: E-commerce order fulfillment\n"
                "Entities: orders, products, customers, warehouses\n"
                "Schema type: star"
            ),
            "output": (
                "## Fact Table: fact_order_fulfillment\n"
                "- Grain: One row per order line item\n"
                "- Measures: quantity, unit_price, discount_amount, "
                "shipping_cost, fulfillment_days\n"
                "- FKs: dim_date, dim_customer, dim_product, dim_warehouse\n\n"
                "## Dimension: dim_customer\n"
                "- Attributes: customer_key (SK), customer_id (BK), "
                "name, email, city, state, country, registration_date, "
                "customer_segment\n\n"
                "## Dimension: dim_product\n"
                "- Attributes: product_key (SK), product_id (BK), "
                "name, category, subcategory, brand, unit_cost, "
                "is_active, effective_date, expiry_date\n\n"
                "## Dimension: dim_warehouse\n"
                "- Attributes: warehouse_key (SK), warehouse_id (BK), "
                "name, city, region, capacity_sqft\n\n"
                "## Dimension: dim_date\n"
                "- Attributes: date_key, full_date, year, quarter, "
                "month, week, day_of_week, is_holiday"
            ),
        },
    ],
)


# ===========================================================================
# 5. SQL Optimization and Rewrite
# ===========================================================================

SQL_OPTIMIZATION_TEMPLATE = PromptTemplate(
    name="sql_optimization",
    description="Analyze a SQL query and suggest performance optimizations.",
    system_prompt=SYSTEM_SQL_EXPERT,
    user_template=(
        "Analyze the following {dialect} SQL query and suggest optimizations "
        "for better performance.\n\n"
        "## Query\n"
        "```sql\n{sql}\n```\n\n"
        "## Execution Plan (if available)\n"
        "```\n{execution_plan}\n```\n\n"
        "## Table Statistics\n"
        "{table_stats}\n\n"
        "## Optimization Goals\n"
        "{optimization_goals}\n\n"
        "## Deliverables\n"
        "1. **Complexity Analysis**: Rate the query (simple / moderate / complex) "
        "and explain why.\n"
        "2. **Bottleneck Identification**: Point out expensive operations "
        "(full scans, large joins, suboptimal predicates).\n"
        "3. **Rewrite Suggestions**: Provide optimized versions of the query "
        "with explanations.\n"
        "4. **Index Recommendations**: Suggest indexes that would help.\n"
        "5. **Partitioning Recommendations**: If applicable, suggest "
        "partitioning strategies.\n"
        "Wrap rewritten queries in ```sql code blocks.\n"
    ),
    variables=[
        "dialect",
        "sql",
        "execution_plan",
        "table_stats",
        "optimization_goals",
    ],
)


# ===========================================================================
# 6. DDL Generation from Requirements
# ===========================================================================

DDL_GENERATION_TEMPLATE = PromptTemplate(
    name="ddl_generation",
    description="Generate DDL statements from natural-language table requirements.",
    system_prompt=SYSTEM_SQL_EXPERT,
    user_template=(
        "Generate {dialect} DDL statements based on the following table "
        "requirements.\n\n"
        "## Requirements\n"
        "{requirements}\n\n"
        "## Context\n"
        "- Target database: {dialect}\n"
        "- Naming convention: {naming_convention}\n"
        "- Warehouse layer: {warehouse_layer}\n\n"
        "## Deliverables\n"
        "1. ``CREATE TABLE`` statement(s) with:\n"
        "   - Appropriate data types for {dialect}.\n"
        "   - NOT NULL / DEFAULT constraints where appropriate.\n"
        "   - PRIMARY KEY and FOREIGN KEY constraints.\n"
        "   - COMMENT on every table and column.\n"
        "2. Partitioning clause if applicable.\n"
        "3. Index creation statements.\n"
        "4. A brief explanation of design choices.\n"
        "Wrap all SQL in ```sql code blocks.\n"
    ),
    variables=["dialect", "requirements", "naming_convention", "warehouse_layer"],
    examples=[
        {
            "input": (
                "Requirements: A fact table to track daily sales aggregations "
                "by product, store, and promotion.  Metrics: total quantity, "
                "total amount, transaction count.\n"
                "Dialect: Hive\n"
                "Layer: DWS"
            ),
            "output": (
                "```sql\n"
                "CREATE TABLE dws_sales_daily_agg (\n"
                "    stat_date       STRING      COMMENT 'Statistics date (yyyy-MM-dd)',\n"
                "    product_key     BIGINT      COMMENT 'Product surrogate key',\n"
                "    store_key       BIGINT      COMMENT 'Store surrogate key',\n"
                "    promotion_key   BIGINT      COMMENT 'Promotion surrogate key',\n"
                "    total_quantity   INT        COMMENT 'Sum of units sold',\n"
                "    total_amount    DECIMAL(18,2) COMMENT 'Sum of sales amount',\n"
                "    txn_count       BIGINT      COMMENT 'Number of transactions'\n"
                ")\n"
                "COMMENT 'Daily sales aggregation fact (DWS layer)'\n"
                "PARTITIONED BY (dt STRING COMMENT 'Partition column')\n"
                "STORED AS ORC;\n"
                "```"
            ),
        },
    ],
)


# ===========================================================================
# 7. Warehouse Layer Design Recommendations
# ===========================================================================

WAREHOUSE_LAYER_DESIGN_TEMPLATE = PromptTemplate(
    name="warehouse_layer_design",
    description="Recommend data warehouse layer architecture (ODS/DWD/DWS/ADS).",
    system_prompt=SYSTEM_MODEL_ADVISOR,
    user_template=(
        "Design the data warehouse layer architecture for the following "
        "scenario.\n\n"
        "## Business Domain\n"
        "{business_domain}\n\n"
        "## Data Sources\n"
        "{data_sources}\n\n"
        "## Reporting Requirements\n"
        "{reporting_requirements}\n\n"
        "## Constraints\n"
        "- Layers to design: {layers} (ODS, DWD, DWS, ADS)\n"
        "- Naming convention: {naming_convention}\n"
        "- Storage engine: {storage_engine}\n\n"
        "## Deliverables\n"
        "For each layer, provide:\n"
        "1. **Table list** with names, descriptions, and grain.\n"
        "2. **Column mapping** showing how data flows from source to each layer.\n"
        "3. **Transformation rules** applied at each transition.\n"
        "4. **Partitioning and storage strategy**.\n"
        "5. **Data quality checks** to implement at each layer.\n"
        "{extra_instructions}"
    ),
    variables=[
        "business_domain",
        "data_sources",
        "reporting_requirements",
        "layers",
        "naming_convention",
        "storage_engine",
        "extra_instructions",
    ],
)


# ===========================================================================
# 8. ETL Pipeline Design
# ===========================================================================

ETL_PIPELINE_DESIGN_TEMPLATE = PromptTemplate(
    name="etl_pipeline_design",
    description="Design an ETL pipeline including extraction, transformation, and loading steps.",
    system_prompt=SYSTEM_DATA_ENGINEER,
    user_template=(
        "Design an ETL pipeline for the following data integration scenario.\n\n"
        "## Source Systems\n"
        "{source_systems}\n\n"
        "## Target Warehouse Layer\n"
        "{target_layer}\n\n"
        "## Transformation Requirements\n"
        "{transformation_requirements}\n\n"
        "## Scheduling & SLA\n"
        "- Frequency: {schedule_frequency}\n"
        "- Data freshness SLA: {freshness_sla}\n"
        "- Orchestration tool: {orchestration_tool}\n\n"
        "## Deliverables\n"
        "1. **Pipeline DAG**: Describe the directed acyclic graph of tasks.\n"
        "2. **Extract steps**: Source connection details, incremental strategy, "
        "and data volume estimation.\n"
        "3. **Transform steps**: Cleansing rules, deduplication logic, "
        "type casting, business rule application.\n"
        "4. **Load steps**: Insert/merge/upsert strategy, partition management, "
        "idempotency guarantees.\n"
        "5. **Monitoring & alerting**: Key metrics, failure handling, retry logic.\n"
        "6. **Code skeleton**: Provide Python / SQL pseudocode for the main "
        "transformation steps.\n"
        "{extra_instructions}"
    ),
    variables=[
        "source_systems",
        "target_layer",
        "transformation_requirements",
        "schedule_frequency",
        "freshness_sla",
        "orchestration_tool",
        "extra_instructions",
    ],
)


# ===========================================================================
# 9. Schema Review
# ===========================================================================

SCHEMA_REVIEW_TEMPLATE = PromptTemplate(
    name="schema_review",
    description="Review an existing data model and provide improvement suggestions.",
    system_prompt=SYSTEM_MODEL_ADVISOR,
    user_template=(
        "Review the following data warehouse schema and provide detailed "
        "feedback.\n\n"
        "## Schema\n"
        "```sql\n{schema_ddl}\n```\n\n"
        "## Review Checklist\n"
        "Please evaluate:\n"
        "1. **Naming conventions**: Are table and column names consistent and "
        "descriptive?\n"
        "2. **Data types**: Are the chosen types appropriate and storage-efficient?\n"
        "3. **Normalization / denormalization**: Is the right balance struck?\n"
        "4. **Constraints**: Are PK, FK, NOT NULL, UNIQUE constraints correct?\n"
        "5. **Partitioning**: Is the partitioning strategy aligned with query "
        "patterns?\n"
        "6. **Indexing**: Are there missing or redundant indexes?\n"
        "7. **Scalability**: Will this design handle 10x data growth?\n"
        "8. **Data quality**: Are there missing validations or cleansing steps?\n"
        "9. **Lineage clarity**: Is the purpose and data flow of each table clear?\n\n"
        "Provide specific, actionable recommendations with before/after examples "
        "where applicable.\n"
    ),
    variables=["schema_ddl"],
)


# ===========================================================================
# 10. Partitioning Strategy
# ===========================================================================

PARTITIONING_STRATEGY_TEMPLATE = PromptTemplate(
    name="partitioning_strategy",
    description="Recommend table partitioning strategies based on query patterns.",
    system_prompt=SYSTEM_SQL_EXPERT,
    user_template=(
        "Recommend a partitioning strategy for the following table.\n\n"
        "## Table Schema\n"
        "```sql\n{table_schema}\n```\n\n"
        "## Common Query Patterns\n"
        "{query_patterns}\n\n"
        "## Data Characteristics\n"
        "- Estimated total rows: {estimated_rows}\n"
        "- Daily ingestion volume: {daily_volume}\n"
        "- Retention policy: {retention_policy}\n"
        "- Target engine: {dialect}\n\n"
        "## Deliverables\n"
        "1. Recommended partition column(s) with rationale.\n"
        "2. Partition granularity (hourly / daily / monthly / yearly).\n"
        "3. Sub-partitioning recommendations if applicable.\n"
        "4. Impact analysis on common query patterns.\n"
        "5. ALTER TABLE or CREATE TABLE DDL to implement the strategy.\n"
    ),
    variables=[
        "table_schema",
        "query_patterns",
        "estimated_rows",
        "daily_volume",
        "retention_policy",
        "dialect",
    ],
)


# ===========================================================================
# Template registry
# ===========================================================================

class PromptRegistry:
    """Central registry for all prompt templates.

    Allows looking up, listing, and overriding templates at runtime.

    Usage::

        registry = PromptRegistry()
        tpl = registry.get("sql_generation")
        rendered = tpl.render(dialect="PostgreSQL", schema="...", question="...", extra_instructions="")
    """

    def __init__(self) -> None:
        self._templates: Dict[str, PromptTemplate] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        """Load the built-in template collection."""
        builtins = [
            SQL_GENERATION_TEMPLATE,
            SQL_EXPLANATION_TEMPLATE,
            SQL_TRANSLATION_TEMPLATE,
            DATA_MODELING_TEMPLATE,
            SQL_OPTIMIZATION_TEMPLATE,
            DDL_GENERATION_TEMPLATE,
            WAREHOUSE_LAYER_DESIGN_TEMPLATE,
            ETL_PIPELINE_DESIGN_TEMPLATE,
            SCHEMA_REVIEW_TEMPLATE,
            PARTITIONING_STRATEGY_TEMPLATE,
        ]
        for tpl in builtins:
            self._templates[tpl.name] = tpl

    def get(self, name: str) -> PromptTemplate:
        """Retrieve a template by name.

        Args:
            name: The template identifier.

        Returns:
            The matching ``PromptTemplate``.

        Raises:
            KeyError: If no template with that name is registered.
        """
        if name not in self._templates:
            raise KeyError(
                f"Prompt template '{name}' not found.  "
                f"Available: {list(self._templates.keys())}"
            )
        return self._templates[name]

    def register(self, template: PromptTemplate) -> None:
        """Register or override a template.

        Args:
            template: The ``PromptTemplate`` to register.
        """
        self._templates[template.name] = template

    def list_templates(self) -> List[str]:
        """Return a sorted list of all registered template names."""
        return sorted(self._templates.keys())

    def render(self, name: str, **kwargs: Any) -> Dict[str, str]:
        """Convenience method: look up a template and render it in one call.

        Args:
            name: Template identifier.
            **kwargs: Variables for rendering.

        Returns:
            Dict with ``system`` and ``user`` keys.
        """
        return self.get(name).render(**kwargs)


# Module-level singleton for convenience
default_registry = PromptRegistry()
