"""
DataForge AI - ETL pipeline design and orchestration.

Provides a builder-pattern API for constructing ETL pipelines with extract,
transform, and load steps, along with code generation for popular orchestration
tools (Apache Airflow and DolphinScheduler).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from src.warehouse.lineage import LineageTracker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class StepType(StrEnum):
    """Types of pipeline steps."""

    EXTRACT = "extract"
    TRANSFORM = "transform"
    LOAD = "load"
    VALIDATE = "validate"
    NOTIFY = "notify"


class ExtractStrategy(StrEnum):
    """Data extraction strategies."""

    FULL = "full"
    INCREMENTAL = "incremental"
    CDC = "cdc"
    SNAPSHOT = "snapshot"


class TransformType(StrEnum):
    """Types of data transformations."""

    SQL = "sql"
    PYTHON = "python"
    SPARK = "spark"
    SHELL = "shell"
    DATA_QUALITY = "data_quality"
    DEDUPLICATION = "deduplication"
    TYPE_CASTING = "type_casting"
    ENRICHMENT = "enrichment"
    AGGREGATION = "aggregation"
    PIVOT = "pivot"
    UNPIVOT = "unpivot"


class LoadStrategy(StrEnum):
    """Data loading strategies."""

    INSERT = "insert"
    INSERT_OVERWRITE = "insert_overwrite"
    UPSERT = "upsert"
    MERGE = "merge"
    APPEND = "append"
    REPLACE = "replace"


class ScheduleFrequency(StrEnum):
    """Common scheduling frequencies."""

    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    ON_DEMAND = "on_demand"


class PipelineStatus(StrEnum):
    """Pipeline lifecycle statuses."""

    DRAFT = "draft"
    VALIDATED = "validated"
    DEPLOYED = "deployed"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class SourceConfig:
    """Configuration for an extract step's data source.

    Attributes:
        connection_id: Reference to a registered database connection.
        source_type: Type of the source system (mysql, postgresql, kafka, etc.).
        source_table: The table, topic, or file to extract from.
        query: Optional custom SQL query for extraction.
        incremental_column: Column used for incremental extraction watermark.
        extract_strategy: How to extract the data.
        batch_size: Number of rows per batch for chunked extraction.
        filters: Optional WHERE clause filters to apply during extraction.
        columns: Specific columns to extract (empty = all).
    """

    connection_id: str = ""
    source_type: str = "mysql"
    source_table: str = ""
    query: str | None = None
    incremental_column: str | None = None
    extract_strategy: ExtractStrategy = ExtractStrategy.FULL
    batch_size: int = 100_000
    filters: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)


@dataclass
class TransformRule:
    """A single transformation rule applied in a transform step.

    Attributes:
        rule_id: Unique identifier for the rule.
        name: Human-readable name.
        transform_type: The type of transformation.
        expression: SQL expression, Python code, or script implementing the rule.
        input_columns: Columns consumed by this transformation.
        output_columns: Columns produced by this transformation.
        description: Business description of what the rule does.
        order: Execution order when multiple rules are chained.
        parameters: Additional configuration parameters.
    """

    rule_id: str = ""
    name: str = ""
    transform_type: TransformType = TransformType.SQL
    expression: str = ""
    input_columns: list[str] = field(default_factory=list)
    output_columns: list[str] = field(default_factory=list)
    description: str = ""
    order: int = 0
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.rule_id:
            self.rule_id = f"rule_{uuid.uuid4().hex[:8]}"


@dataclass
class TargetConfig:
    """Configuration for a load step's target destination.

    Attributes:
        connection_id: Reference to a registered database connection.
        target_type: Type of the target system.
        target_table: The destination table.
        load_strategy: How to load the data.
        partition_column: Column used for partition management.
        partition_value: Expression to compute the partition value.
        merge_keys: Columns used as merge/upsert keys.
        pre_load_sql: SQL to execute before loading (e.g. cleanup).
        post_load_sql: SQL to execute after loading (e.g. index rebuild).
        idempotent: Whether the load is safe to re-run.
    """

    connection_id: str = ""
    target_type: str = "hive"
    target_table: str = ""
    load_strategy: LoadStrategy = LoadStrategy.INSERT_OVERWRITE
    partition_column: str | None = None
    partition_value: str | None = None
    merge_keys: list[str] = field(default_factory=list)
    pre_load_sql: str | None = None
    post_load_sql: str | None = None
    idempotent: bool = True


@dataclass
class ScheduleConfig:
    """Scheduling configuration for a pipeline.

    Attributes:
        frequency: How often the pipeline runs.
        cron_expression: Cron expression for fine-grained scheduling.
        start_date: When the schedule becomes active.
        end_date: When the schedule expires (``None`` = indefinite).
        catchup: Whether to backfill missed runs.
        max_active_runs: Maximum concurrent runs.
        retry_count: Number of retries on failure.
        retry_delay: Delay between retries in seconds.
        timeout: Overall pipeline timeout in seconds.
        sla_minutes: SLA target for pipeline completion.
    """

    frequency: ScheduleFrequency = ScheduleFrequency.DAILY
    cron_expression: str = "0 2 * * *"  # Default: 2 AM daily
    start_date: datetime | None = None
    end_date: datetime | None = None
    catchup: bool = False
    max_active_runs: int = 1
    retry_count: int = 3
    retry_delay: int = 300  # 5 minutes
    timeout: int = 7200  # 2 hours
    sla_minutes: int = 120


@dataclass
class DataQualityCheck:
    """A data quality check applied at a pipeline step boundary.

    Attributes:
        check_id: Unique identifier.
        check_type: Type of check (null_check, range_check, uniqueness, etc.).
        table: Table to check.
        column: Column to check (``None`` = table-level).
        expression: SQL expression that should evaluate to ``True``.
        threshold: Acceptable failure rate (0.0 = zero tolerance).
        action_on_failure: What to do when the check fails.
        description: Human-readable description.
    """

    check_id: str = ""
    check_type: str = "null_check"
    table: str = ""
    column: str | None = None
    expression: str = ""
    threshold: float = 0.0
    action_on_failure: str = "fail"  # "fail", "warn", "skip"
    description: str = ""

    def __post_init__(self) -> None:
        if not self.check_id:
            self.check_id = f"dq_{uuid.uuid4().hex[:8]}"


@dataclass
class PipelineStep:
    """A single step within an ETL pipeline.

    Attributes:
        step_id: Unique step identifier.
        name: Human-readable step name.
        step_type: The type of step (extract, transform, load, etc.).
        source_config: Configuration for extract steps.
        transform_rules: Rules for transform steps.
        target_config: Configuration for load steps.
        quality_checks: Data quality checks applied at this step.
        depends_on: IDs of steps that must complete before this one starts.
        parameters: Additional step-specific parameters.
        timeout: Step-level timeout in seconds.
        retry_count: Step-level retry count.
    """

    step_id: str = ""
    name: str = ""
    step_type: StepType = StepType.TRANSFORM
    source_config: SourceConfig | None = None
    transform_rules: list[TransformRule] = field(default_factory=list)
    target_config: TargetConfig | None = None
    quality_checks: list[DataQualityCheck] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    timeout: int = 3600
    retry_count: int = 3

    def __post_init__(self) -> None:
        if not self.step_id:
            self.step_id = f"step_{uuid.uuid4().hex[:8]}"
        if not self.name:
            self.name = f"{self.step_type.value}_{self.step_id}"


@dataclass
class Pipeline:
    """A complete ETL pipeline composed of multiple steps.

    Attributes:
        pipeline_id: Unique pipeline identifier.
        name: Human-readable pipeline name.
        description: Business description of the pipeline.
        version: Pipeline version for tracking changes.
        steps: Ordered list of pipeline steps.
        schedule: Scheduling configuration.
        tags: Metadata tags for organization and filtering.
        owner: The team or person responsible for this pipeline.
        status: Current lifecycle status.
        created_at: Timestamp of creation.
        updated_at: Timestamp of last modification.
    """

    pipeline_id: str = ""
    name: str = ""
    description: str = ""
    version: str = "1.0.0"
    steps: list[PipelineStep] = field(default_factory=list)
    schedule: ScheduleConfig | None = None
    tags: list[str] = field(default_factory=list)
    owner: str = ""
    status: PipelineStatus = PipelineStatus.DRAFT
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.pipeline_id:
            self.pipeline_id = f"pipeline_{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = datetime.utcnow()
        self.updated_at = self.created_at


@dataclass
class ValidationResult:
    """Result of validating a pipeline.

    Attributes:
        is_valid: Whether the pipeline passed all validation checks.
        errors: List of error messages.
        warnings: List of warning messages.
        suggestions: List of improvement suggestions.
    """

    is_valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# PipelineBuilder
# ---------------------------------------------------------------------------

class PipelineBuilder:
    """Builder-pattern API for constructing ETL pipelines.

    Provides a fluent interface for creating, configuring, and validating
    ETL pipelines, along with code generation for orchestration tools.

    Usage::

        builder = PipelineBuilder()
        pipeline = (
            builder
            .create_pipeline(name="daily_order_etl")
            .add_extract_step(SourceConfig(
                connection_id="mysql_prod",
                source_table="orders",
                extract_strategy=ExtractStrategy.INCREMENTAL,
                incremental_column="updated_at",
            ))
            .add_transform_step([
                TransformRule(
                    name="dedup_orders",
                    transform_type=TransformType.DEDUPLICATION,
                    expression="ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY updated_at DESC)",
                ),
                TransformRule(
                    name="cast_amounts",
                    transform_type=TransformType.TYPE_CASTING,
                    expression="CAST(amount AS DECIMAL(18,2))",
                ),
            ])
            .add_load_step(TargetConfig(
                connection_id="hive_dw",
                target_table="dwd_trade_order_di",
                load_strategy=LoadStrategy.INSERT_OVERWRITE,
                partition_column="dt",
            ))
            .build()
        )
    """

    def __init__(self) -> None:
        self._pipeline: Pipeline | None = None

    # -- Builder methods ----------------------------------------------------

    def create_pipeline(
        self,
        name: str,
        description: str = "",
        schedule: ScheduleConfig | None = None,
        owner: str = "",
        tags: list[str] | None = None,
    ) -> PipelineBuilder:
        """Initialize a new pipeline.

        Args:
            name: Pipeline name.
            description: Business description.
            schedule: Optional scheduling configuration.
            owner: Pipeline owner.
            tags: Metadata tags.

        Returns:
            ``self`` for method chaining.
        """
        self._pipeline = Pipeline(
            name=name,
            description=description,
            schedule=schedule,
            owner=owner,
            tags=tags or [],
        )
        return self

    def add_extract_step(
        self,
        source_config: SourceConfig,
        name: str | None = None,
        depends_on: list[str] | None = None,
        quality_checks: list[DataQualityCheck] | None = None,
    ) -> PipelineBuilder:
        """Add an extract step to the pipeline.

        Args:
            source_config: Configuration for the data source.
            name: Optional custom step name.
            depends_on: Step IDs this step depends on.
            quality_checks: Data quality checks after extraction.

        Returns:
            ``self`` for method chaining.

        Raises:
            RuntimeError: If ``create_pipeline`` was not called first.
        """
        self._ensure_pipeline()
        step = PipelineStep(
            name=name or f"extract_{source_config.source_table}",
            step_type=StepType.EXTRACT,
            source_config=source_config,
            depends_on=depends_on or [],
            quality_checks=quality_checks or [],
        )
        self._pipeline.steps.append(step)  # type: ignore[union-attr]
        return self

    def add_transform_step(
        self,
        transform_rules: TransformRule | list[TransformRule],
        name: str | None = None,
        depends_on: list[str] | None = None,
        quality_checks: list[DataQualityCheck] | None = None,
    ) -> PipelineBuilder:
        """Add a transform step to the pipeline.

        Args:
            transform_rules: One or more transformation rules.
            name: Optional custom step name.
            depends_on: Step IDs this step depends on.  Defaults to depending
                on the previously added step.
            quality_checks: Data quality checks after transformation.

        Returns:
            ``self`` for method chaining.
        """
        self._ensure_pipeline()

        rules = transform_rules if isinstance(transform_rules, list) else [transform_rules]
        # Sort by order field
        rules.sort(key=lambda r: r.order)

        # Default: depend on the last step added
        if depends_on is None and self._pipeline.steps:  # type: ignore[union-attr]
            last_step = self._pipeline.steps[-1]  # type: ignore[union-attr]
            depends_on = [last_step.step_id]

        step = PipelineStep(
            name=name or f"transform_{len(rules)}rules",
            step_type=StepType.TRANSFORM,
            transform_rules=rules,
            depends_on=depends_on or [],
            quality_checks=quality_checks or [],
        )
        self._pipeline.steps.append(step)  # type: ignore[union-attr]
        return self

    def add_load_step(
        self,
        target_config: TargetConfig,
        name: str | None = None,
        depends_on: list[str] | None = None,
        quality_checks: list[DataQualityCheck] | None = None,
    ) -> PipelineBuilder:
        """Add a load step to the pipeline.

        Args:
            target_config: Configuration for the target destination.
            name: Optional custom step name.
            depends_on: Step IDs this step depends on.
            quality_checks: Post-load data quality checks.

        Returns:
            ``self`` for method chaining.
        """
        self._ensure_pipeline()

        if depends_on is None and self._pipeline.steps:  # type: ignore[union-attr]
            last_step = self._pipeline.steps[-1]  # type: ignore[union-attr]
            depends_on = [last_step.step_id]

        step = PipelineStep(
            name=name or f"load_{target_config.target_table}",
            step_type=StepType.LOAD,
            target_config=target_config,
            depends_on=depends_on or [],
            quality_checks=quality_checks or [],
        )
        self._pipeline.steps.append(step)  # type: ignore[union-attr]
        return self

    def add_validation_step(
        self,
        quality_checks: list[DataQualityCheck],
        name: str | None = None,
        depends_on: list[str] | None = None,
    ) -> PipelineBuilder:
        """Add a dedicated validation / data quality step.

        Args:
            quality_checks: The data quality checks to run.
            name: Optional custom step name.
            depends_on: Step IDs this step depends on.

        Returns:
            ``self`` for method chaining.
        """
        self._ensure_pipeline()

        if depends_on is None and self._pipeline.steps:  # type: ignore[union-attr]
            last_step = self._pipeline.steps[-1]  # type: ignore[union-attr]
            depends_on = [last_step.step_id]

        step = PipelineStep(
            name=name or "data_quality_check",
            step_type=StepType.VALIDATE,
            quality_checks=quality_checks,
            depends_on=depends_on or [],
        )
        self._pipeline.steps.append(step)  # type: ignore[union-attr]
        return self

    def set_schedule(self, schedule: ScheduleConfig) -> PipelineBuilder:
        """Set or override the pipeline's schedule configuration.

        Args:
            schedule: The scheduling configuration.

        Returns:
            ``self`` for method chaining.
        """
        self._ensure_pipeline()
        self._pipeline.schedule = schedule  # type: ignore[union-attr]
        return self

    def build(self) -> Pipeline:
        """Finalize and return the constructed pipeline.

        Returns:
            The completed ``Pipeline`` object.

        Raises:
            RuntimeError: If no pipeline was created.
        """
        self._ensure_pipeline()
        self._pipeline.updated_at = datetime.utcnow()  # type: ignore[union-attr]
        return self._pipeline  # type: ignore[return-value]

    # -- Validation ---------------------------------------------------------

    def validate_pipeline(self, pipeline: Pipeline | None = None) -> ValidationResult:
        """Validate a pipeline for completeness and correctness.

        Checks for:
        - At least one extract step and one load step.
        - Valid dependency graph (no dangling references, no cycles).
        - Extract steps have valid source configs.
        - Load steps have valid target configs.
        - Transform rules have non-empty expressions.
        - Schedule configuration is valid.

        Args:
            pipeline: The pipeline to validate.  Defaults to the builder's
                current pipeline.

        Returns:
            A ``ValidationResult`` with errors, warnings, and suggestions.
        """
        p = pipeline or self._pipeline
        if p is None:
            return ValidationResult(
                is_valid=False,
                errors=["No pipeline to validate."],
            )

        result = ValidationResult()

        # Check for extract and load steps
        step_types = [s.step_type for s in p.steps]
        if StepType.EXTRACT not in step_types:
            result.errors.append("Pipeline must have at least one EXTRACT step.")
            result.is_valid = False
        if StepType.LOAD not in step_types:
            result.errors.append("Pipeline must have at least one LOAD step.")
            result.is_valid = False

        # Validate step dependencies
        step_ids = {s.step_id for s in p.steps}
        for step in p.steps:
            for dep in step.depends_on:
                if dep not in step_ids:
                    result.errors.append(
                        f"Step '{step.name}' depends on unknown step '{dep}'."
                    )
                    result.is_valid = False

        # Check for cycles in dependencies
        if self._has_dependency_cycles(p):
            result.errors.append("Circular dependency detected in pipeline steps.")
            result.is_valid = False

        # Validate extract steps
        for step in p.steps:
            if step.step_type == StepType.EXTRACT:
                if not step.source_config:
                    result.errors.append(
                        f"Extract step '{step.name}' is missing source_config."
                    )
                    result.is_valid = False
                elif not step.source_config.source_table and not step.source_config.query:
                    result.errors.append(
                        f"Extract step '{step.name}' must specify source_table or query."
                    )
                    result.is_valid = False
                elif (
                    step.source_config.extract_strategy == ExtractStrategy.INCREMENTAL
                    and not step.source_config.incremental_column
                ):
                    result.warnings.append(
                        f"Extract step '{step.name}' uses incremental strategy "
                        f"but has no incremental_column defined."
                    )

            elif step.step_type == StepType.TRANSFORM:
                if not step.transform_rules:
                    result.errors.append(
                        f"Transform step '{step.name}' has no transform rules."
                    )
                    result.is_valid = False
                for rule in step.transform_rules:
                    if not rule.expression:
                        result.errors.append(
                            f"Transform rule '{rule.name}' in step '{step.name}' "
                            f"has an empty expression."
                        )
                        result.is_valid = False

            elif step.step_type == StepType.LOAD:
                if not step.target_config:
                    result.errors.append(
                        f"Load step '{step.name}' is missing target_config."
                    )
                    result.is_valid = False
                elif not step.target_config.target_table:
                    result.errors.append(
                        f"Load step '{step.name}' must specify target_table."
                    )
                    result.is_valid = False

        # Suggestions
        if not any(s.step_type == StepType.VALIDATE for s in p.steps):
            result.suggestions.append(
                "Consider adding a VALIDATE step with data quality checks."
            )
        if p.schedule and p.schedule.catchup:
            result.suggestions.append(
                "Catchup is enabled.  Ensure the pipeline is idempotent to handle "
                "backfill correctly."
            )
        if not p.tags:
            result.suggestions.append(
                "Consider adding tags for better organization and discoverability."
            )

        return result

    # -- Code generation: Airflow -------------------------------------------

    def generate_airflow_dag(
        self,
        pipeline: Pipeline | None = None,
        dag_id: str | None = None,
        default_args: dict[str, Any] | None = None,
    ) -> str:
        """Generate an Apache Airflow DAG Python file from a pipeline.

        Args:
            pipeline: The pipeline to convert.  Defaults to the builder's pipeline.
            dag_id: Custom DAG ID.  Defaults to the pipeline name.
            default_args: Custom default_args dict for the DAG.

        Returns:
            A string containing the complete Airflow DAG Python code.
        """
        p = pipeline or self._pipeline
        if p is None:
            raise RuntimeError("No pipeline to generate DAG from.")

        dag_id = dag_id or p.name.replace(" ", "_").lower()
        schedule = p.schedule or ScheduleConfig()

        default_args_dict = default_args or {
            "owner": p.owner or "dataforge",
            "depends_on_past": False,
            "email_on_failure": True,
            "email_on_retry": False,
            "retries": schedule.retry_count,
            "retry_delay": f"timedelta(seconds={schedule.retry_delay})",
            "execution_timeout": f"timedelta(seconds={schedule.timeout})",
        }

        lines: list[str] = [
            "# -*- coding: utf-8 -*-",
            '"""',
            f"Auto-generated Airflow DAG for pipeline: {p.name}",
            f"Description: {p.description}",
            f"Generated by DataForge AI on {datetime.utcnow().isoformat()}",
            '"""',
            "",
            "from datetime import datetime, timedelta",
            "",
            "from airflow import DAG",
            "from airflow.operators.python import PythonOperator",
            "from airflow.operators.sql import SQLExecuteQueryOperator",
            "from airflow.providers.apache.hive.operators.hive import HiveOperator",
            "",
            "",
            f"default_args = {self._format_dict(default_args_dict)}",
            "",
            "",
            'with DAG(',
            f'    dag_id="{dag_id}",',
            "    default_args=default_args,",
            f'    description="{p.description}",',
            f'    schedule_interval="{schedule.cron_expression}",',
            f'    start_date=datetime({(schedule.start_date or datetime(2024, 1, 1)).year}, '
            f'{(schedule.start_date or datetime(2024, 1, 1)).month}, '
            f'{(schedule.start_date or datetime(2024, 1, 1)).day}),',
            f"    catchup={schedule.catchup},",
            f"    max_active_runs={schedule.max_active_runs},",
            f"    tags={p.tags},",
            ") as dag:",
            "",
        ]

        # Generate tasks
        task_var_names: dict[str, str] = {}
        for step in p.steps:
            var_name = f"task_{step.step_id}"
            task_var_names[step.step_id] = var_name

            if step.step_type == StepType.EXTRACT:
                lines.extend(self._gen_airflow_extract_task(step, var_name))
            elif step.step_type == StepType.TRANSFORM:
                lines.extend(self._gen_airflow_transform_task(step, var_name))
            elif step.step_type == StepType.LOAD:
                lines.extend(self._gen_airflow_load_task(step, var_name))
            elif step.step_type == StepType.VALIDATE:
                lines.extend(self._gen_airflow_validate_task(step, var_name))

            lines.append("")

        # Set dependencies
        lines.append("    # Task dependencies")
        for step in p.steps:
            if step.depends_on:
                task_var = task_var_names[step.step_id]
                dep_vars = [task_var_names[d] for d in step.depends_on if d in task_var_names]
                if len(dep_vars) == 1:
                    lines.append(f"    {dep_vars[0]} >> {task_var}")
                elif dep_vars:
                    lines.append(f"    [{', '.join(dep_vars)}] >> {task_var}")

        lines.append("")
        return "\n".join(lines)

    # -- Code generation: DolphinScheduler ----------------------------------

    def generate_dolphin_scheduler_yaml(
        self,
        pipeline: Pipeline | None = None,
        project_name: str = "dataforge",
    ) -> str:
        """Generate a DolphinScheduler workflow definition in YAML format.

        Args:
            pipeline: The pipeline to convert.  Defaults to the builder's pipeline.
            project_name: DolphinScheduler project name.

        Returns:
            A YAML string defining the workflow.
        """
        p = pipeline or self._pipeline
        if p is None:
            raise RuntimeError("No pipeline to generate YAML from.")

        schedule = p.schedule or ScheduleConfig()

        yaml_lines: list[str] = [
            f"# DolphinScheduler workflow for pipeline: {p.name}",
            f"# Generated by DataForge AI on {datetime.utcnow().isoformat()}",
            "---",
            "project:",
            f"  name: {project_name}",
            "",
            "workflow:",
            f"  name: {p.name.replace(' ', '_')}",
            f"  description: \"{p.description}\"",
            f"  version: \"{p.version}\"",
            "  schedule:",
            f"    cron: \"{schedule.cron_expression}\"",
            f"    start_time: \"{(schedule.start_date or datetime(2024, 1, 1)).isoformat()}\"",
            "    failure_strategy: \"continue\"",
            "    warning_type: \"all\"",
            "    worker_group: \"default\"",
            f"    timeout: {schedule.timeout // 60}  # minutes",
            "  tasks:",
        ]

        for step in p.steps:
            yaml_lines.extend(self._gen_ds_task(step))

        # Dependencies
        yaml_lines.append("")
        yaml_lines.append("  task_dependencies:")
        for step in p.steps:
            if step.depends_on:
                dep_names = []
                for dep_id in step.depends_on:
                    dep_step = next((s for s in p.steps if s.step_id == dep_id), None)
                    if dep_step:
                        dep_names.append(dep_step.name)
                if dep_names:
                    yaml_lines.append(f"    {step.name}: [{', '.join(dep_names)}]")

        yaml_lines.append("")
        return "\n".join(yaml_lines)

    # -- Internal helpers ---------------------------------------------------

    def _ensure_pipeline(self) -> None:
        """Raise if no pipeline has been created."""
        if self._pipeline is None:
            raise RuntimeError(
                "No pipeline initialized.  Call create_pipeline() first."
            )

    @staticmethod
    def _has_dependency_cycles(pipeline: Pipeline) -> bool:
        """Check if the pipeline's step dependency graph has cycles."""
        adj: dict[str, list[str]] = {}
        for step in pipeline.steps:
            adj[step.step_id] = step.depends_on

        visited: set = set()
        rec_stack: set = set()

        def _dfs(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)
            for dep in adj.get(node, []):
                if dep not in visited:
                    if _dfs(dep):
                        return True
                elif dep in rec_stack:
                    return True
            rec_stack.discard(node)
            return False

        return any(step_id not in visited and _dfs(step_id) for step_id in adj)

    @staticmethod
    def _format_dict(d: dict[str, Any]) -> str:
        """Format a dictionary as a Python dict literal string."""
        items = []
        for k, v in d.items():
            if isinstance(v, str) and not v.startswith(("datetime", "timedelta")):
                items.append(f'    "{k}": "{v}"')
            else:
                items.append(f"    \"{k}\": {v}")
        return "{\n" + ",\n".join(items) + ",\n}"

    def _gen_airflow_extract_task(self, step: PipelineStep, var_name: str) -> list[str]:
        """Generate Airflow task code for an extract step."""
        cfg = step.source_config
        lines = [
            f"    {var_name} = PythonOperator(",
            f'        task_id="{step.name}",',
            "        python_callable=extract_data,",
            "        op_kwargs={",
            f'            "connection_id": "{cfg.connection_id if cfg else ""}",',
            f'            "source_table": "{cfg.source_table if cfg else ""}",',
            f'            "strategy": "{cfg.extract_strategy.value if cfg else "full"}",',
            "        },",
            "    )",
        ]
        return lines

    def _gen_airflow_transform_task(self, step: PipelineStep, var_name: str) -> list[str]:
        """Generate Airflow task code for a transform step."""
        # Use SQL if all rules are SQL type, otherwise Python
        all_sql = all(r.transform_type == TransformType.SQL for r in step.transform_rules)

        if all_sql and step.transform_rules:
            sql_expr = ";\n".join(r.expression for r in step.transform_rules)
            lines = [
                f"    {var_name} = HiveOperator(",
                f'        task_id="{step.name}",',
                '        hql="""',
                f"        {sql_expr}",
                '        """,',
                "    )",
            ]
        else:
            lines = [
                f"    {var_name} = PythonOperator(",
                f'        task_id="{step.name}",',
                "        python_callable=transform_data,",
                "        op_kwargs={",
                f'            "rules": {len(step.transform_rules)},',
                "        },",
                "    )",
            ]
        return lines

    def _gen_airflow_load_task(self, step: PipelineStep, var_name: str) -> list[str]:
        """Generate Airflow task code for a load step."""
        cfg = step.target_config
        lines = [
            f"    {var_name} = PythonOperator(",
            f'        task_id="{step.name}",',
            "        python_callable=load_data,",
            "        op_kwargs={",
            f'            "connection_id": "{cfg.connection_id if cfg else ""}",',
            f'            "target_table": "{cfg.target_table if cfg else ""}",',
            f'            "strategy": "{cfg.load_strategy.value if cfg else "insert"}",',
            "        },",
            "    )",
        ]
        return lines

    def _gen_airflow_validate_task(self, step: PipelineStep, var_name: str) -> list[str]:
        """Generate Airflow task code for a validation step."""
        lines = [
            f"    {var_name} = PythonOperator(",
            f'        task_id="{step.name}",',
            "        python_callable=run_quality_checks,",
            "        op_kwargs={",
            f'            "checks_count": {len(step.quality_checks)},',
            "        },",
            "    )",
        ]
        return lines

    def _gen_ds_task(self, step: PipelineStep) -> list[str]:
        """Generate DolphinScheduler YAML task definition."""
        lines = [
            f"    - name: {step.name}",
            f"      type: {self._ds_task_type(step.step_type)}",
            f"      description: \"{step.name} ({step.step_type.value})\"",
        ]

        if step.step_type == StepType.EXTRACT and step.source_config:
            cfg = step.source_config
            lines.extend([
                "      params:",
                f"        connection_id: \"{cfg.connection_id}\"",
                f"        source_table: \"{cfg.source_table}\"",
                f"        strategy: \"{cfg.extract_strategy.value}\"",
            ])
        elif step.step_type == StepType.TRANSFORM:
            lines.extend([
                "      params:",
                f"        rules_count: {len(step.transform_rules)}",
            ])
            if step.transform_rules:
                lines.append("        sql: |")
                for rule in step.transform_rules:
                    if rule.transform_type == TransformType.SQL:
                        for sql_line in rule.expression.splitlines():
                            lines.append(f"          {sql_line}")
        elif step.step_type == StepType.LOAD and step.target_config:
            cfg = step.target_config
            lines.extend([
                "      params:",
                f"        connection_id: \"{cfg.connection_id}\"",
                f"        target_table: \"{cfg.target_table}\"",
                f"        strategy: \"{cfg.load_strategy.value}\"",
            ])

        lines.append(f"      timeout: {step.timeout}")
        lines.append(f"      retry_count: {step.retry_count}")

        return lines

    @staticmethod
    def _ds_task_type(step_type: StepType) -> str:
        """Map pipeline step type to DolphinScheduler task type."""
        mapping = {
            StepType.EXTRACT: "SQL",
            StepType.TRANSFORM: "SQL",
            StepType.LOAD: "SQL",
            StepType.VALIDATE: "PYTHON",
            StepType.NOTIFY: "HTTP",
        }
        return mapping.get(step_type, "PYTHON")


# ---------------------------------------------------------------------------
# PipelineExecutor
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    """Result of executing a single pipeline step.

    Attributes:
        step_id: The ID of the step that was executed.
        status: Outcome status (``"SUCCESS"`` or ``"FAILED"``).
        error: Optional error message when the step fails.
    """

    step_id: str
    status: str
    error: str | None = None


class PipelineExecutor:
    """Execute ETL pipelines and automatically collect data lineage.

    Runs each step of a :class:`Pipeline` in dependency order.  After a
    step completes successfully, lineage is extracted from its SQL
    (``source_query`` parameter or ``SourceConfig.query`` / transform
    expressions) and persisted via :class:`LineageTracker`.

    Lineage collection is a **best-effort** operation: any failure during
    parsing or persistence is logged but never blocks or fails the ETL
    pipeline.

    Args:
        session_factory: An ``async_sessionmaker`` used by the lineage
            tracker to persist lineage records.  When ``None``, lineage
            collection is silently skipped.
        dialect: SQL dialect hint passed to the lineage parser.

    Usage::

        executor = PipelineExecutor(session_factory, dialect="hive")
        results = await executor.execute_pipeline(pipeline)
    """

    def __init__(
        self,
        session_factory: Any | None = None,
        dialect: str = "mysql",
    ) -> None:
        self.session_factory = session_factory
        self.dialect = dialect
        self._background_tasks: set[asyncio.Task[Any]] = set()

    # -- Public API ---------------------------------------------------------

    async def execute_pipeline(self, pipeline: Pipeline) -> list[StepResult]:
        """Execute all steps of a pipeline in dependency order.

        Steps whose dependencies have not succeeded are skipped with a
        ``FAILED`` result.  After each successful step, lineage collection
        is attempted as a non-blocking best-effort operation.

        Args:
            pipeline: The pipeline to execute.

        Returns:
            A list of :class:`StepResult` objects, one per step.
        """
        results: dict[str, StepResult] = {}

        for step in pipeline.steps:
            # Wait until all dependencies have succeeded
            deps_ok = all(
                results.get(dep_id) is not None
                and results[dep_id].status == "SUCCESS"
                for dep_id in step.depends_on
            )

            if not deps_ok and step.depends_on:
                results[step.step_id] = StepResult(
                    step_id=step.step_id,
                    status="FAILED",
                    error="Dependency not met",
                )
                logger.warning(
                    "Step '%s' skipped: dependency not met", step.name,
                )
                continue

            try:
                await self._execute_step(step)
                result = StepResult(step_id=step.step_id, status="SUCCESS")
                results[step.step_id] = result
                logger.info("Step '%s' executed successfully", step.name)

                # Best-effort lineage collection after successful execution
                if self.session_factory is not None:
                    task = asyncio.create_task(
                        self._collect_lineage(step, self.session_factory)
                    )
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)

            except Exception as exc:
                results[step.step_id] = StepResult(
                    step_id=step.step_id,
                    status="FAILED",
                    error=str(exc),
                )
                logger.error("Step '%s' failed: %s", step.name, exc)

        return list(results.values())

    # -- Internal helpers ---------------------------------------------------

    async def _execute_step(self, step: PipelineStep) -> None:
        """Execute a single pipeline step.

        Dispatches to the appropriate handler based on :class:`StepType`.
        Subclasses or future versions can override this to plug in real
        database or compute engine calls.

        Args:
            step: The pipeline step to execute.

        Raises:
            NotImplementedError: If the step type is not supported.
        """
        if step.step_type == StepType.EXTRACT:
            await self._run_extract(step)
        elif step.step_type == StepType.TRANSFORM:
            await self._run_transform(step)
        elif step.step_type == StepType.LOAD:
            await self._run_load(step)
        elif step.step_type == StepType.VALIDATE:
            await self._run_validate(step)
        else:
            logger.debug("Skipping step '%s' of type %s", step.name, step.step_type)

    async def _run_extract(self, step: PipelineStep) -> None:
        """Execute an extract step."""
        cfg = step.source_config
        if cfg is None:
            raise ValueError(f"Extract step '{step.name}' has no source_config")
        logger.debug(
            "Extracting from %s (strategy=%s)",
            cfg.source_table or cfg.query,
            cfg.extract_strategy.value,
        )
        # Actual extraction would be performed by an external engine.

    async def _run_transform(self, step: PipelineStep) -> None:
        """Execute a transform step."""
        if not step.transform_rules:
            raise ValueError(f"Transform step '{step.name}' has no transform_rules")
        logger.debug(
            "Running %d transform rule(s) for step '%s'",
            len(step.transform_rules),
            step.name,
        )

    async def _run_load(self, step: PipelineStep) -> None:
        """Execute a load step."""
        cfg = step.target_config
        if cfg is None:
            raise ValueError(f"Load step '{step.name}' has no target_config")
        logger.debug(
            "Loading into %s (strategy=%s)",
            cfg.target_table,
            cfg.load_strategy.value,
        )

    async def _run_validate(self, step: PipelineStep) -> None:
        """Execute a validation step."""
        logger.debug(
            "Running %d quality check(s) for step '%s'",
            len(step.quality_checks),
            step.name,
        )

    async def _collect_lineage(
        self,
        step: PipelineStep,
        session_factory: Any,
    ) -> None:
        """Collect and persist lineage for a successfully executed step.

        Extracts SQL from the step's ``source_query`` parameter (preferred)
        or from its :class:`SourceConfig` / :class:`TransformRule`
        expressions, parses it via :class:`LineageTracker`, and persists
        the resulting :class:`LineageGraph`.

        This is a best-effort operation -- any exception is caught and
        logged so that lineage failures never break the ETL pipeline.

        Args:
            step: The pipeline step that was just executed.
            session_factory: An ``async_sessionmaker`` for the internal DB.
        """
        try:
            sql = self._extract_sql_from_step(step)
            if not sql:
                logger.debug(
                    "No SQL found for step '%s'; skipping lineage collection",
                    step.name,
                )
                return

            tracker = LineageTracker()
            graph = tracker.parse_sql_lineage(sql, dialect=self.dialect)

            if not graph.table_edges:
                logger.debug(
                    "No lineage edges parsed for step '%s'; skipping persist",
                    step.name,
                )
                return

            await tracker.persist_lineage(graph, session_factory)
            logger.info(
                "Lineage collected for step '%s': %d table edge(s), "
                "%d column edge(s)",
                step.name,
                len(graph.table_edges),
                len(graph.column_edges),
            )
        except Exception as exc:
            logger.warning(
                "Failed to collect lineage for step '%s': %s",
                step.name,
                exc,
            )

    @staticmethod
    def _extract_sql_from_step(step: PipelineStep) -> str | None:
        """Extract the most relevant SQL string from a pipeline step.

        Checks (in order):
        1. ``step.parameters["source_query"]``
        2. ``step.source_config.query``  (for extract steps)
        3. SQL-type transform rule expressions joined by semicolons
           (for transform steps)

        Args:
            step: The pipeline step to inspect.

        Returns:
            A SQL string, or ``None`` if no SQL could be found.
        """
        # 1. Explicit source_query in parameters
        source_query = step.parameters.get("source_query")
        if source_query:
            return str(source_query)

        # 2. SourceConfig.query on extract steps
        if step.source_config and step.source_config.query:
            return step.source_config.query

        # 3. SQL transform rules on transform steps
        if step.transform_rules:
            sql_parts = [
                rule.expression
                for rule in step.transform_rules
                if rule.transform_type == TransformType.SQL and rule.expression
            ]
            if sql_parts:
                return ";\n".join(sql_parts)

        return None
