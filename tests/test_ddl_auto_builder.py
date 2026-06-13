"""Tests for src.warehouse.ddl_auto_builder -- automated DDL generation engine."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from src.core.schemas import ColumnDataType, ColumnInfo, TableSchema
from src.warehouse.ddl_auto_builder import (
    ColumnMapping,
    DDLAutoBuilder,
    DDLPipelineConfig,
    DDLPipelineResult,
    GeneratedTable,
    _infer_domain,
    _normalise_layer_name,
    _snake_case,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ods_config() -> DDLPipelineConfig:
    """Minimal config targeting ODS layer with DuckDB engine."""
    return DDLPipelineConfig(
        source_connection_id="conn-test-001",
        source_tables=["orders"],
        target_layer="ODS",
        target_db_type="duckdb",
        local_verify=False,
    )


@pytest.fixture()
def ods_builder(ods_config) -> DDLAutoBuilder:
    """DDLAutoBuilder configured for ODS / DuckDB."""
    return DDLAutoBuilder(ods_config)


@pytest.fixture()
def sample_columns() -> list[ColumnInfo]:
    """A small set of source columns resembling an orders table."""
    return [
        ColumnInfo(
            name="id",
            data_type="BIGINT",
            is_primary_key=True,
            nullable=False,
            comment="Primary key",
        ),
        ColumnInfo(
            name="user_id",
            data_type="INT",
            nullable=False,
            comment="FK to users",
        ),
        ColumnInfo(
            name="amount",
            data_type="DECIMAL(10,2)",
            nullable=True,
            comment="Order amount",
        ),
        ColumnInfo(
            name="created_at",
            data_type="TIMESTAMP",
            logical_type=ColumnDataType.TIMESTAMP,
            nullable=False,
            comment="Creation timestamp",
        ),
    ]


# ======================================================================== #
# Helper functions
# ======================================================================== #


class TestSnakeCase:
    """Conversion of arbitrary strings to snake_case."""

    def test_camel_case(self):
        assert _snake_case("OrderDetails") == "order_details"

    def test_hyphenated(self):
        assert _snake_case("user-login-log") == "user_login_log"

    def test_whitespace(self):
        assert _snake_case("  Some Table  ") == "some_table"

    def test_already_snake(self):
        assert _snake_case("already_snake") == "already_snake"

    def test_mixed_case_with_digits(self):
        assert _snake_case("Order2Details") == "order2_details"

    def test_empty_string(self):
        assert _snake_case("") == ""


class TestInferDomain:
    """Heuristic domain inference from table names."""

    def test_order_domain(self):
        assert _infer_domain("orders") == "trade"

    def test_payment_domain(self):
        assert _infer_domain("payment_log") == "trade"

    def test_user_domain(self):
        assert _infer_domain("user_profile") == "user"

    def test_product_domain(self):
        assert _infer_domain("product_catalog") == "product"

    def test_log_domain(self):
        assert _infer_domain("event_log") == "log"

    def test_unknown_falls_to_common(self):
        assert _infer_domain("misc_data") == "common"


class TestNormaliseLayerName:
    """Layer name normalisation."""

    def test_uppercase(self):
        assert _normalise_layer_name("ods") == "ODS"

    def test_already_upper(self):
        assert _normalise_layer_name("DWD") == "DWD"

    def test_with_whitespace(self):
        assert _normalise_layer_name("  dws  ") == "DWS"


# ======================================================================== #
# DDLPipelineConfig
# ======================================================================== #


class TestDDLPipelineConfig:
    """Default values and validation on the config model."""

    def test_default_values(self):
        config = DDLPipelineConfig(source_connection_id="conn-001")
        assert config.target_layer == "ODS"
        assert config.target_db_type == "clickhouse"
        assert config.source_tables == []
        assert config.convention_path is None
        assert config.naming_overrides == {}
        assert config.include_computation_sql is True
        assert config.local_verify is True
        assert config.sample_rows_for_verify == 100
        assert config.enable_ai is False

    def test_custom_values(self):
        config = DDLPipelineConfig(
            source_connection_id="conn-002",
            source_tables=["orders", "users"],
            target_layer="DWD",
            target_db_type="hive",
            local_verify=False,
            enable_ai=True,
            sample_rows_for_verify=500,
        )
        assert config.target_layer == "DWD"
        assert config.target_db_type == "hive"
        assert config.local_verify is False
        assert config.enable_ai is True
        assert config.sample_rows_for_verify == 500

    def test_sample_rows_lower_bound(self):
        with pytest.raises(ValidationError):
            DDLPipelineConfig(
                source_connection_id="conn-x",
                sample_rows_for_verify=0,
            )

    def test_sample_rows_upper_bound(self):
        with pytest.raises(ValidationError):
            DDLPipelineConfig(
                source_connection_id="conn-x",
                sample_rows_for_verify=99_999,
            )

    def test_missing_source_connection_id(self):
        with pytest.raises(ValidationError):
            DDLPipelineConfig()  # source_connection_id is required


# ======================================================================== #
# DDLAutoBuilder initialization
# ======================================================================== #


class TestDDLAutoBuilderInit:
    """Builder construction and internal state."""

    def test_basic_init(self, ods_config):
        builder = DDLAutoBuilder(ods_config)
        assert builder.config is ods_config
        assert builder._target_layer == "ODS"
        assert builder._target_engine == "duckdb"

    def test_init_normalises_layer(self):
        config = DDLPipelineConfig(
            source_connection_id="conn-001",
            target_layer="dwd",
        )
        builder = DDLAutoBuilder(config)
        assert builder._target_layer == "DWD"

    def test_init_normalises_engine(self):
        config = DDLPipelineConfig(
            source_connection_id="conn-001",
            target_db_type="ClickHouse",
        )
        builder = DDLAutoBuilder(config)
        assert builder._target_engine == "clickhouse"


# ======================================================================== #
# Naming convention application
# ======================================================================== #


class TestApplyNaming:
    """Target table name generation from source name + layer."""

    def test_ods_naming(self, ods_builder):
        name = ods_builder.apply_naming("orders", "ODS")
        # Built-in: {layer}_{domain}_{body}{suffix}
        # layer=ods, domain=trade, body=orders, suffix=""
        assert name == "ods_trade_orders"

    def test_dwd_naming(self, ods_builder):
        name = ods_builder.apply_naming("orders", "DWD")
        # layer=dwd, domain=trade, body=orders, suffix="_di"
        assert name == "dwd_trade_orders_di"

    def test_dws_naming(self, ods_builder):
        name = ods_builder.apply_naming("orders", "DWS")
        assert name == "dws_trade_orders_1d"

    def test_user_domain(self, ods_builder):
        name = ods_builder.apply_naming("user_profile", "ODS")
        assert name == "ods_user_user_profile"

    def test_naming_override(self):
        config = DDLPipelineConfig(
            source_connection_id="conn-001",
            target_layer="ODS",
            naming_overrides={"orders": "custom_orders_table"},
            local_verify=False,
        )
        builder = DDLAutoBuilder(config)
        name = builder.apply_naming("orders", "ODS")
        assert name == "custom_orders_table"

    def test_unknown_domain_falls_to_common(self, ods_builder):
        name = ods_builder.apply_naming("misc_data", "ODS")
        assert name == "ods_common_misc_data"


# ======================================================================== #
# Column mapping
# ======================================================================== #


class TestMapColumns:
    """Source-to-target column mapping with type conversion."""

    def test_basic_mapping(self, ods_builder, sample_columns):
        mappings = ods_builder.map_columns(sample_columns, "duckdb", "ODS")

        # Should have original columns + etl_time + dt (ODS extras)
        target_names = [m.target_column for m in mappings]
        assert "id" in target_names
        assert "user_id" in target_names
        assert "amount" in target_names
        assert "created_at" in target_names
        assert "etl_time" in target_names  # ODS-specific
        assert "dt" in target_names  # ODS partition key

    def test_duckdb_types(self, ods_builder, sample_columns):
        mappings = ods_builder.map_columns(sample_columns, "duckdb", "ODS")
        type_map = {m.target_column: m.target_type for m in mappings}
        assert type_map["id"] == "BIGINT"
        assert type_map["user_id"] == "INTEGER"
        assert type_map["amount"] == "DECIMAL(18,2)"
        assert type_map["created_at"] == "TIMESTAMP"

    def test_clickhouse_types(self, ods_builder, sample_columns):
        mappings = ods_builder.map_columns(sample_columns, "clickhouse", "ODS")
        type_map = {m.target_column: m.target_type for m in mappings}
        assert type_map["id"] == "Int64"
        assert type_map["user_id"] == "Int32"
        assert type_map["amount"] == "Decimal(18, 2)"

    def test_dwd_adds_surrogate_key(self, ods_builder, sample_columns):
        mappings = ods_builder.map_columns(sample_columns, "duckdb", "DWD")
        target_names = [m.target_column for m in mappings]
        assert "row_key" in target_names  # DWD surrogate key
        # row_key should be first
        assert target_names[0] == "row_key"

    def test_primary_key_preserved(self, ods_builder, sample_columns):
        mappings = ods_builder.map_columns(sample_columns, "duckdb", "ODS")
        id_mapping = next(m for m in mappings if m.target_column == "id")
        assert id_mapping.is_primary_key is True


# ======================================================================== #
# DDL generation
# ======================================================================== #


class TestGenerateDDL:
    """CREATE TABLE DDL output for different engines."""

    def _make_mappings(self, builder, columns, engine, layer):
        return builder.map_columns(columns, engine, layer)

    def test_duckdb_ddl_has_create_table(self, ods_builder, sample_columns):
        mappings = self._make_mappings(
            ods_builder, sample_columns, "duckdb", "ODS"
        )
        ddl = ods_builder.generate_ddl(
            "ods_trade_orders", mappings, "ODS", "duckdb"
        )
        assert "CREATE TABLE IF NOT EXISTS ods_trade_orders" in ddl
        assert ddl.rstrip().endswith(";")

    def test_duckdb_ddl_has_columns(self, ods_builder, sample_columns):
        mappings = self._make_mappings(
            ods_builder, sample_columns, "duckdb", "ODS"
        )
        ddl = ods_builder.generate_ddl(
            "ods_trade_orders", mappings, "ODS", "duckdb"
        )
        assert "id" in ddl
        assert "BIGINT" in ddl
        assert "user_id" in ddl
        assert "INTEGER" in ddl

    def test_duckdb_ddl_has_primary_key(self, ods_builder, sample_columns):
        mappings = self._make_mappings(
            ods_builder, sample_columns, "duckdb", "ODS"
        )
        ddl = ods_builder.generate_ddl(
            "ods_trade_orders", mappings, "ODS", "duckdb"
        )
        assert "PRIMARY KEY" in ddl

    def test_clickhouse_ddl_has_engine(self, ods_builder, sample_columns):
        mappings = self._make_mappings(
            ods_builder, sample_columns, "clickhouse", "ODS"
        )
        ddl = ods_builder.generate_ddl(
            "ods_trade_orders", mappings, "ODS", "clickhouse"
        )
        assert "ENGINE = MergeTree()" in ddl
        assert "ORDER BY" in ddl

    def test_hive_ddl_has_stored_as(self, ods_builder, sample_columns):
        mappings = self._make_mappings(
            ods_builder, sample_columns, "hive", "ODS"
        )
        ddl = ods_builder.generate_ddl(
            "ods_trade_orders", mappings, "ODS", "hive"
        )
        assert "STORED AS" in ddl

    def test_mysql_ddl_has_engine_innodb(self, ods_builder, sample_columns):
        mappings = self._make_mappings(
            ods_builder, sample_columns, "mysql", "ODS"
        )
        ddl = ods_builder.generate_ddl(
            "ods_trade_orders", mappings, "ODS", "mysql"
        )
        assert "ENGINE=InnoDB" in ddl
        assert "PRIMARY KEY" in ddl

    def test_ddl_partition_columns_separated(self, ods_builder, sample_columns):
        mappings = self._make_mappings(
            ods_builder, sample_columns, "hive", "ODS"
        )
        ddl = ods_builder.generate_ddl(
            "ods_trade_orders", mappings, "ODS", "hive"
        )
        # 'dt' is the partition column -- should appear in PARTITIONED BY, not in main body
        assert "PARTITIONED BY" in ddl
        assert "dt" in ddl


# ======================================================================== #
# Computation SQL generation
# ======================================================================== #


class TestGenerateComputationSQL:
    """INSERT INTO ... SELECT statement generation."""

    def test_ods_computation_sql(self, ods_builder, sample_columns):
        mappings = ods_builder.map_columns(sample_columns, "duckdb", "ODS")
        sql = ods_builder.generate_computation_sql(
            source_table="orders",
            target_table="ods_trade_orders",
            column_mappings=mappings,
            layer="ODS",
        )
        assert "INSERT INTO ods_trade_orders" in sql
        assert "FROM orders" in sql

    def test_dwd_computation_sql_has_row_number(self, ods_builder, sample_columns):
        mappings = ods_builder.map_columns(sample_columns, "duckdb", "DWD")
        sql = ods_builder.generate_computation_sql(
            source_table="ods_trade_orders",
            target_table="dwd_trade_orders_di",
            column_mappings=mappings,
            layer="DWD",
        )
        assert "INSERT INTO dwd_trade_orders_di" in sql
        assert "ROW_NUMBER()" in sql


# ======================================================================== #
# DDLPipelineResult / GeneratedTable models
# ======================================================================== #


class TestResultModels:
    """Pydantic result models used by the pipeline."""

    def test_generated_table_minimal(self):
        gt = GeneratedTable(
            source_table="orders",
            target_table="ods_trade_orders",
            target_layer="ODS",
            ddl="CREATE TABLE ods_trade_orders (id INT);",
        )
        assert gt.source_table == "orders"
        assert gt.computation_sql is None
        assert gt.convention_violations == []

    def test_pipeline_result_defaults(self):
        config = DDLPipelineConfig(source_connection_id="conn-001")
        result = DDLPipelineResult(config=config)
        assert result.total_tables == 0
        assert result.succeeded == 0
        assert result.failed == 0
        assert result.tables == []
        assert result.errors == []

    def test_column_mapping_model(self):
        cm = ColumnMapping(
            source_column="user_id",
            source_type="INT",
            target_column="user_id",
            target_type="INTEGER",
        )
        assert cm.transformation is None
        assert cm.is_partition_key is False
        assert cm.is_primary_key is False


# ======================================================================== #
# Full pipeline with TableSchema input (async build)
# ======================================================================== #


class TestBuildPipeline:
    """End-to-end pipeline execution with simple schemas."""

    @pytest.fixture()
    def simple_schema(self) -> TableSchema:
        return TableSchema(
            database_name="test_db",
            table_name="orders",
            columns=[
                ColumnInfo(name="id", data_type="INT", is_primary_key=True),
                ColumnInfo(name="user_id", data_type="INT"),
                ColumnInfo(name="amount", data_type="DECIMAL(10,2)"),
                ColumnInfo(name="created_at", data_type="TIMESTAMP"),
            ],
        )

    @pytest.mark.asyncio
    async def test_build_produces_ddl(self, simple_schema):
        config = DDLPipelineConfig(
            source_connection_id="conn-test",
            source_tables=["orders"],
            target_layer="ODS",
            target_db_type="duckdb",
            local_verify=False,
            include_computation_sql=True,
        )
        builder = DDLAutoBuilder(config)
        result = await builder.build([simple_schema])

        assert isinstance(result, DDLPipelineResult)
        assert result.total_tables == 1
        assert result.succeeded == 1
        assert result.failed == 0
        assert len(result.tables) == 1

        table = result.tables[0]
        assert table.source_table == "orders"
        assert "CREATE TABLE" in table.ddl

    @pytest.mark.asyncio
    async def test_build_empty_columns_raises(self):
        empty_schema = TableSchema(
            database_name="test_db",
            table_name="empty_table",
            columns=[],
        )
        config = DDLPipelineConfig(
            source_connection_id="conn-test",
            target_layer="ODS",
            target_db_type="duckdb",
            local_verify=False,
        )
        builder = DDLAutoBuilder(config)
        result = await builder.build([empty_schema])
        assert result.failed == 1
        assert len(result.errors) == 1
        assert "no columns" in result.errors[0].lower()

    @pytest.mark.asyncio
    async def test_build_multiple_tables(self, simple_schema):
        users_schema = TableSchema(
            database_name="test_db",
            table_name="users",
            columns=[
                ColumnInfo(name="id", data_type="INT", is_primary_key=True),
                ColumnInfo(name="name", data_type="VARCHAR(255)"),
                ColumnInfo(name="email", data_type="VARCHAR(255)"),
            ],
        )
        config = DDLPipelineConfig(
            source_connection_id="conn-test",
            target_layer="ODS",
            target_db_type="duckdb",
            local_verify=False,
        )
        builder = DDLAutoBuilder(config)
        result = await builder.build([simple_schema, users_schema])

        assert result.total_tables == 2
        assert result.succeeded == 2
        source_names = {t.source_table for t in result.tables}
        assert "orders" in source_names
        assert "users" in source_names
