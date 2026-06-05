# -*- coding: utf-8 -*-
"""
DataForge AI - Data warehouse layer definitions.

Defines the standard data warehouse layers (ODS, DWD, DWS, ADS) following the
OneData / Alibaba methodology, along with naming conventions, storage strategies,
transition rules, and validation logic for each layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# Layer enum with Chinese descriptions
# ---------------------------------------------------------------------------

class WarehouseLayer(str, Enum):
    """Standard data warehouse layers with Chinese descriptions.

    Each layer serves a specific role in the data processing pipeline,
    following the OneData methodology widely adopted in enterprise data
    warehouse architectures.
    """

    ODS = "ods"
    DWD = "dwd"
    DWS = "dws"
    ADS = "ads"
    DIM = "dim"
    TMP = "tmp"

    @property
    def chinese_name(self) -> str:
        """Return the Chinese name for this layer."""
        _names = {
            "ods": "贴源层 (Operational Data Store)",
            "dwd": "明细层 (Data Warehouse Detail)",
            "dws": "汇总层 (Data Warehouse Summary)",
            "ads": "应用层 (Application Data Store)",
            "dim": "维度层 (Dimension)",
            "tmp": "临时层 (Temporary)",
        }
        return _names.get(self.value, self.value)

    @property
    def description(self) -> str:
        """Return a detailed description of this layer's purpose."""
        _descriptions = {
            "ods": (
                "The Operational Data Store layer is the entry point for raw data.  "
                "It mirrors source systems with minimal transformation, preserving "
                "data fidelity.  Typically partitioned by ingestion date."
            ),
            "dwd": (
                "The Detail layer cleanses, standardizes, and enriches raw data.  "
                "It normalizes data types, handles NULLs, applies business rules, "
                "and produces row-level fact tables at the finest grain."
            ),
            "dws": (
                "The Summary layer aggregates detail data along common dimensions.  "
                "It pre-computes frequently needed metrics to accelerate downstream "
                "queries and reports."
            ),
            "ads": (
                "The Application layer provides data tailored for specific "
                "applications, dashboards, and reports.  Tables are designed to "
                "match the exact access patterns of end-user tools."
            ),
            "dim": (
                "The Dimension layer stores shared dimension tables (date, customer, "
                "product, etc.) used across multiple fact tables."
            ),
            "tmp": (
                "Temporary tables used for intermediate computation within ETL "
                "pipelines.  These are not intended for end-user consumption."
            ),
        }
        return _descriptions.get(self.value, "")


# ---------------------------------------------------------------------------
# Layer configuration
# ---------------------------------------------------------------------------

@dataclass
class LayerConfig:
    """Configuration for a single warehouse layer.

    Defines naming conventions, storage strategy, and behavioral settings
    for tables belonging to a specific layer.

    Attributes:
        layer: The warehouse layer this configuration applies to.
        name_prefix: Required prefix for table names in this layer.
        name_pattern: Regex pattern that valid table names must match.
        partition_strategy: Default partitioning strategy description.
        storage_format: Preferred storage format (e.g. ORC, Parquet).
        compression: Compression codec (e.g. snappy, zstd, lz4).
        retention_days: Default data retention period in days (``0`` = forever).
        requires_partition: Whether tables in this layer must be partitioned.
        allowed_dml: Set of allowed DML operations (INSERT, UPDATE, MERGE, etc.).
        naming_examples: Example table names for documentation.
    """

    layer: WarehouseLayer
    name_prefix: str
    name_pattern: str
    partition_strategy: str = "daily"
    storage_format: str = "Parquet"
    compression: str = "snappy"
    retention_days: int = 365
    requires_partition: bool = True
    allowed_dml: Set[str] = field(default_factory=lambda: {"INSERT", "INSERT OVERWRITE"})
    naming_examples: List[str] = field(default_factory=list)

    def validate_table_name(self, table_name: str) -> bool:
        """Check whether a table name conforms to this layer's naming convention.

        Args:
            table_name: The table name to validate.

        Returns:
            ``True`` if the name matches the pattern, ``False`` otherwise.
        """
        return bool(re.match(self.name_pattern, table_name))


# Default layer configurations following OneData naming conventions
DEFAULT_LAYER_CONFIGS: Dict[WarehouseLayer, LayerConfig] = {
    WarehouseLayer.ODS: LayerConfig(
        layer=WarehouseLayer.ODS,
        name_prefix="ods_",
        name_pattern=r"^ods_[a-z][a-z0-9_]*$",
        partition_strategy="daily",
        storage_format="ORC",
        compression="snappy",
        retention_days=90,
        requires_partition=True,
        naming_examples=["ods_trade_order", "ods_user_info", "ods_product_detail"],
    ),
    WarehouseLayer.DWD: LayerConfig(
        layer=WarehouseLayer.DWD,
        name_prefix="dwd_",
        name_pattern=r"^dwd_[a-z][a-z0-9_]*$",
        partition_strategy="daily",
        storage_format="Parquet",
        compression="snappy",
        retention_days=365,
        requires_partition=True,
        naming_examples=["dwd_trade_order_di", "dwd_user_login_di", "dwd_payment_detail_di"],
    ),
    WarehouseLayer.DWS: LayerConfig(
        layer=WarehouseLayer.DWS,
        name_prefix="dws_",
        name_pattern=r"^dws_[a-z][a-z0-9_]*(?:_\d+[hdwmy])?$",
        partition_strategy="daily",
        storage_format="Parquet",
        compression="snappy",
        retention_days=730,
        requires_partition=True,
        naming_examples=["dws_trade_seller_1d", "dws_user_active_1d", "dws_trade_item_7d"],
    ),
    WarehouseLayer.ADS: LayerConfig(
        layer=WarehouseLayer.ADS,
        name_prefix="ads_",
        name_pattern=r"^ads_[a-z][a-z0-9_]*$",
        partition_strategy="daily or none",
        storage_format="Parquet",
        compression="snappy",
        retention_days=0,  # Permanent
        requires_partition=False,
        naming_examples=["ads_gmv_report", "ads_user_profile", "ads_funnel_analysis"],
    ),
    WarehouseLayer.DIM: LayerConfig(
        layer=WarehouseLayer.DIM,
        name_prefix="dim_",
        name_pattern=r"^dim_[a-z][a-z0-9_]*$",
        partition_strategy="snapshot",
        storage_format="Parquet",
        compression="snappy",
        retention_days=0,
        requires_partition=False,
        naming_examples=["dim_date", "dim_user", "dim_product", "dim_store"],
    ),
    WarehouseLayer.TMP: LayerConfig(
        layer=WarehouseLayer.TMP,
        name_prefix="tmp_",
        name_pattern=r"^tmp_[a-z][a-z0-9_]*$",
        partition_strategy="none",
        storage_format="Parquet",
        compression="snappy",
        retention_days=7,
        requires_partition=False,
        naming_examples=["tmp_order_dedup", "tmp_join_intermediate"],
    ),
}


# ---------------------------------------------------------------------------
# Layer transition rules
# ---------------------------------------------------------------------------

@dataclass
class LayerTransition:
    """Defines a valid data flow transition between warehouse layers.

    Encodes the rules for how data is allowed to flow from one layer to
    another, including what transformations are expected.

    Attributes:
        source_layer: The layer data flows from.
        target_layer: The layer data flows into.
        allowed: Whether this transition is permitted.
        transformation_type: The expected type of transformation.
        description: Human-readable description of the transition.
        validation_rules: Additional validation rules that must pass.
    """

    source_layer: WarehouseLayer
    target_layer: WarehouseLayer
    allowed: bool = True
    transformation_type: str = ""
    description: str = ""
    validation_rules: List[str] = field(default_factory=list)


# Standard valid transitions in a layered warehouse
STANDARD_TRANSITIONS: List[LayerTransition] = [
    LayerTransition(
        source_layer=WarehouseLayer.ODS,
        target_layer=WarehouseLayer.DWD,
        transformation_type="cleansing_and_standardization",
        description=(
            "ODS -> DWD: Data cleansing, type standardization, NULL handling, "
            "deduplication, and business rule application."
        ),
        validation_rules=[
            "All columns must have defined NOT NULL / DEFAULT handling",
            "Data types must be standardized across sources",
            "Surrogate keys must be generated for dimension references",
        ],
    ),
    LayerTransition(
        source_layer=WarehouseLayer.DWD,
        target_layer=WarehouseLayer.DWS,
        transformation_type="aggregation",
        description=(
            "DWD -> DWS: Aggregation of detail data along common dimensions "
            "at predefined granularity levels (daily, weekly, monthly)."
        ),
        validation_rules=[
            "Aggregation grain must be explicitly declared",
            "All dimension FKs must reference valid dim tables",
            "Metrics must use additive or semi-additive measures",
        ],
    ),
    LayerTransition(
        source_layer=WarehouseLayer.DWS,
        target_layer=WarehouseLayer.ADS,
        transformation_type="application_specific",
        description=(
            "DWS -> ADS: Final transformation tailored for specific applications, "
            "reports, or dashboards.  May involve pivoting, ranking, or "
            "cross-domain joins."
        ),
        validation_rules=[
            "Output must match the target application's access pattern",
            "SLA freshness requirements must be documented",
        ],
    ),
    LayerTransition(
        source_layer=WarehouseLayer.DWD,
        target_layer=WarehouseLayer.ADS,
        transformation_type="direct_application",
        description=(
            "DWD -> ADS: Direct use of detail data for applications that need "
            "row-level access (e.g. real-time dashboards, detail drill-downs)."
        ),
        validation_rules=[
            "Query performance must be acceptable at detail level",
            "Partition pruning must be leveraged",
        ],
    ),
    LayerTransition(
        source_layer=WarehouseLayer.DWD,
        target_layer=WarehouseLayer.DIM,
        transformation_type="dimension_consolidation",
        description=(
            "DWD -> DIM: Consolidation of dimension attributes from detail "
            "tables into shared dimension tables with SCD handling."
        ),
        validation_rules=[
            "SCD type must be specified (Type 1, 2, or 3)",
            "Natural keys must be unique within the dimension",
        ],
    ),
    # Temporary layer transitions
    LayerTransition(
        source_layer=WarehouseLayer.ODS,
        target_layer=WarehouseLayer.TMP,
        transformation_type="intermediate",
        description="ODS -> TMP: Intermediate staging during complex ETL flows.",
    ),
    LayerTransition(
        source_layer=WarehouseLayer.DWD,
        target_layer=WarehouseLayer.TMP,
        transformation_type="intermediate",
        description="DWD -> TMP: Intermediate computation results.",
    ),
]


# ---------------------------------------------------------------------------
# Layer validator
# ---------------------------------------------------------------------------

class LayerValidator:
    """Validates table placement and data flow between warehouse layers.

    Ensures that tables are correctly assigned to layers, follow naming
    conventions, and that data flows only through permitted transitions.

    Args:
        layer_configs: Optional custom layer configurations.  Falls back to
            ``DEFAULT_LAYER_CONFIGS`` when not provided.
        transitions: Optional custom transition rules.  Falls back to
            ``STANDARD_TRANSITIONS`` when not provided.

    Usage::

        validator = LayerValidator()
        errors = validator.validate_table_placement("ods_trade_order", WarehouseLayer.ODS)
        assert len(errors) == 0

        is_valid = validator.is_transition_allowed(WarehouseLayer.ODS, WarehouseLayer.ADS)
        assert not is_valid  # Direct ODS -> ADS is not a standard transition
    """

    def __init__(
        self,
        layer_configs: Optional[Dict[WarehouseLayer, LayerConfig]] = None,
        transitions: Optional[List[LayerTransition]] = None,
    ) -> None:
        self._configs = layer_configs or DEFAULT_LAYER_CONFIGS
        self._transitions = transitions or STANDARD_TRANSITIONS
        self._transition_map: Dict[tuple[WarehouseLayer, WarehouseLayer], LayerTransition] = {}
        for t in self._transitions:
            self._transition_map[(t.source_layer, t.target_layer)] = t

    def validate_table_placement(
        self,
        table_name: str,
        layer: WarehouseLayer,
    ) -> List[str]:
        """Validate that a table name is appropriate for its assigned layer.

        Args:
            table_name: The table name to validate.
            layer: The warehouse layer the table is assigned to.

        Returns:
            A list of validation error messages.  An empty list means the
            placement is valid.
        """
        errors: List[str] = []
        config = self._configs.get(layer)

        if config is None:
            errors.append(f"No configuration defined for layer '{layer.value}'.")
            return errors

        # Check prefix
        if not table_name.startswith(config.name_prefix):
            errors.append(
                f"Table '{table_name}' does not start with the required prefix "
                f"'{config.name_prefix}' for layer '{layer.chinese_name}'."
            )

        # Check pattern
        if not config.validate_table_name(table_name):
            errors.append(
                f"Table '{table_name}' does not match the naming pattern "
                f"'{config.name_pattern}' for layer '{layer.chinese_name}'."
            )

        # Check for cross-layer prefix confusion
        for other_layer, other_config in self._configs.items():
            if other_layer != layer and table_name.startswith(other_config.name_prefix):
                errors.append(
                    f"Table '{table_name}' has prefix '{other_config.name_prefix}' "
                    f"which belongs to layer '{other_layer.chinese_name}', "
                    f"but is placed in '{layer.chinese_name}'."
                )

        return errors

    def is_transition_allowed(
        self,
        source: WarehouseLayer,
        target: WarehouseLayer,
    ) -> bool:
        """Check whether data flow from *source* to *target* is permitted.

        Args:
            source: The source layer.
            target: The target layer.

        Returns:
            ``True`` if the transition is allowed.
        """
        transition = self._transition_map.get((source, target))
        return transition is not None and transition.allowed

    def get_transition(
        self,
        source: WarehouseLayer,
        target: WarehouseLayer,
    ) -> Optional[LayerTransition]:
        """Return the transition rule for a source-target pair, if any.

        Args:
            source: The source layer.
            target: The target layer.

        Returns:
            The ``LayerTransition`` object, or ``None`` if no rule exists.
        """
        return self._transition_map.get((source, target))

    def get_allowed_targets(self, source: WarehouseLayer) -> List[WarehouseLayer]:
        """Return all layers that *source* is allowed to feed into.

        Args:
            source: The source layer.

        Returns:
            A list of allowed target layers.
        """
        return [
            t.target_layer
            for t in self._transitions
            if t.source_layer == source and t.allowed
        ]

    def get_allowed_sources(self, target: WarehouseLayer) -> List[WarehouseLayer]:
        """Return all layers that are allowed to feed into *target*.

        Args:
            target: The target layer.

        Returns:
            A list of allowed source layers.
        """
        return [
            t.source_layer
            for t in self._transitions
            if t.target_layer == target and t.allowed
        ]

    def validate_data_flow(
        self,
        flow_graph: Dict[str, List[str]],
        table_layers: Dict[str, WarehouseLayer],
    ) -> List[str]:
        """Validate an entire data flow graph for layer compliance.

        Args:
            flow_graph: A dict mapping each table to its upstream dependencies.
                Example: ``{"dwd_order": ["ods_order"], "ads_report": ["dws_daily"]}``
            table_layers: A dict mapping each table name to its assigned layer.

        Returns:
            A list of validation error messages.
        """
        errors: List[str] = []

        for table, upstream_tables in flow_graph.items():
            target_layer = table_layers.get(table)
            if target_layer is None:
                errors.append(f"Table '{table}' has no assigned layer.")
                continue

            # Validate table naming
            naming_errors = self.validate_table_placement(table, target_layer)
            errors.extend(naming_errors)

            # Validate each upstream dependency
            for upstream in upstream_tables:
                source_layer = table_layers.get(upstream)
                if source_layer is None:
                    errors.append(
                        f"Upstream table '{upstream}' (dependency of '{table}') "
                        f"has no assigned layer."
                    )
                    continue

                if not self.is_transition_allowed(source_layer, target_layer):
                    errors.append(
                        f"Invalid data flow: '{upstream}' ({source_layer.chinese_name}) "
                        f"-> '{table}' ({target_layer.chinese_name}).  "
                        f"This transition is not permitted."
                    )

        return errors

    def get_layer_summary(self) -> Dict[WarehouseLayer, Dict[str, Any]]:
        """Return a summary of all layer configurations and their rules.

        Returns:
            A dict keyed by ``WarehouseLayer`` with configuration details.
        """
        summary: Dict[WarehouseLayer, Dict[str, Any]] = {}
        for layer, config in self._configs.items():
            summary[layer] = {
                "chinese_name": layer.chinese_name,
                "description": layer.description,
                "prefix": config.name_prefix,
                "pattern": config.name_pattern,
                "partition_strategy": config.partition_strategy,
                "storage_format": config.storage_format,
                "retention_days": config.retention_days,
                "examples": config.naming_examples,
                "allowed_targets": [
                    t.value for t in self.get_allowed_targets(layer)
                ],
            }
        return summary
