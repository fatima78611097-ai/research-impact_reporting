"""
Stage registry for the pipeline control plane.

Each pipeline stage is declared here as a StageDefinition dataclass.
The orchestrator, dashboard views, and job creation forms all read from STAGE_REGISTRY.
Adding a new stage = adding an entry here + the actual stage code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class ParamSpec:
    required: bool = False
    type: str = "string"  # state_code, string, integer, float, boolean, choice
    choices: list[str] | None = None
    cli_flag: str = ""
    min_value: int | None = None
    max_value: int | None = None
    pattern: str | None = None  # regex for string type


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 1
    auto_retry: bool = False
    backoff_seconds: int = 60
    retryable_exit_codes: tuple[int, ...] = (1, 3)

    def __post_init__(self):
        if self.max_attempts > 3:
            object.__setattr__(self, "max_attempts", 3)
        if self.max_attempts < 1:
            object.__setattr__(self, "max_attempts", 1)


@dataclass(frozen=True)
class StageDefinition:
    name: str
    display_name: str
    command: list[str]
    parameters: dict[str, ParamSpec]
    predecessors: list[str]
    conflict_group: str | None  # "per-state", "global", "990-family"
    provenance_column: str | None
    retry_policy: RetryPolicy
    resource_class: str  # "heavy" | "medium" | "light"
    progress_estimator: Callable | None = None
    protocol_version: int = 0  # 0 = legacy, 1 = fd 3
    provenance_query: Callable | None = None


STAGE_REGISTRY: dict[str, StageDefinition] = {
    "seed": StageDefinition(
        name="seed",
        display_name="Seed Enumerator",
        command=["python3", "-m", "lavandula.nonprofits.tools.seed_enumerate"],
        parameters={
            "states": ParamSpec(required=True, type="state_code", cli_flag="--states"),
            "target": ParamSpec(type="integer", cli_flag="--target", min_value=1, max_value=999999),
            "ntee_majors": ParamSpec(type="string", cli_flag="--ntee-majors", pattern=r"^[A-Z,]+$"),
            "revenue_min": ParamSpec(type="integer", cli_flag="--revenue-min", min_value=0),
            "revenue_max": ParamSpec(type="integer", cli_flag="--revenue-max", min_value=0),
        },
        predecessors=[],
        conflict_group="per-state",
        provenance_column="seed_status",
        retry_policy=RetryPolicy(max_attempts=1),
        resource_class="light",
    ),
    "resolve": StageDefinition(
        name="resolve",
        display_name="URL Resolver",
        command=["python3", "-m", "lavandula.nonprofits.tools.pipeline_resolve"],
        parameters={
            "state": ParamSpec(required=True, type="state_code", cli_flag="--state"),
            "llm_url": ParamSpec(type="string", cli_flag="--llm-url", pattern=r"^https?://"),
            "llm_model": ParamSpec(type="string", cli_flag="--llm-model"),
            "llm_api_key_ssm": ParamSpec(type="string", cli_flag="--llm-api-key-ssm"),
            "brave_qps": ParamSpec(type="float", cli_flag="--search-qps", min_value=0, max_value=50),
            "search_engines": ParamSpec(type="string", cli_flag="--search-engines", pattern=r"^[a-z,_]+$"),
            "search_parallelism": ParamSpec(type="integer", cli_flag="--search-parallelism", min_value=1, max_value=32),
            "consumer_threads": ParamSpec(type="integer", cli_flag="--consumer-threads", min_value=1, max_value=16),
            "limit": ParamSpec(type="integer", cli_flag="--limit", min_value=0, max_value=999999),
            "fresh_only": ParamSpec(type="boolean", cli_flag="--fresh-only"),
        },
        predecessors=["seed"],
        conflict_group="per-state",
        provenance_column="resolve_status",
        retry_policy=RetryPolicy(max_attempts=2, auto_retry=True, retryable_exit_codes=(1, 3)),
        resource_class="light",
    ),
    "crawl": StageDefinition(
        name="crawl",
        display_name="Site Crawler",
        command=["python3", "-m", "lavandula.reports.crawler"],
        parameters={
            "archive": ParamSpec(type="string", cli_flag="--archive", pattern=r"^s3://[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9](/[a-zA-Z0-9._-]+)*$"),
            "async": ParamSpec(type="boolean", cli_flag="--async"),
            "limit": ParamSpec(type="integer", cli_flag="--limit", min_value=0, max_value=999999),
            "max_concurrent_orgs": ParamSpec(type="integer", cli_flag="--max-concurrent-orgs", min_value=1, max_value=500),
            "max_download_workers": ParamSpec(type="integer", cli_flag="--max-download-workers", min_value=1, max_value=100),
"state": ParamSpec(type="state_code", cli_flag="--state"),
            "classifier_backend": ParamSpec(type="string", cli_flag="--classifier-backend", pattern=r"^[a-z]+$"),
        },
        predecessors=["resolve"],
        conflict_group="per-state",
        provenance_column="crawl_status",
        retry_policy=RetryPolicy(max_attempts=2, auto_retry=True, retryable_exit_codes=(1, 3)),
        resource_class="heavy",
    ),
    "classify": StageDefinition(
        name="classify",
        display_name="Document Classifier",
        command=["python3", "-m", "lavandula.nonprofits.tools.pipeline_classify"],
        parameters={
            "llm_url": ParamSpec(type="string", cli_flag="--llm-url", pattern=r"^https?://"),
            "llm_model": ParamSpec(type="string", cli_flag="--llm-model"),
            "llm_api_key_ssm": ParamSpec(type="string", cli_flag="--llm-api-key-ssm"),
            "limit": ParamSpec(type="integer", cli_flag="--limit", min_value=0, max_value=999999),
            "state": ParamSpec(type="state_code", cli_flag="--state"),
            "re_classify": ParamSpec(type="boolean", cli_flag="--re-classify"),
            "definition": ParamSpec(type="string", cli_flag="--definition", pattern=r"^[a-z][a-z0-9_]*$"),
            "re_classify_definition": ParamSpec(type="string", cli_flag="--re-classify-definition", pattern=r"^[a-z][a-z0-9_]*:v\d+$"),
        },
        predecessors=["crawl"],
        conflict_group="per-state",
        provenance_column="classify_status",
        retry_policy=RetryPolicy(max_attempts=2, auto_retry=True, retryable_exit_codes=(1, 3)),
        resource_class="medium",
    ),
    "990-index": StageDefinition(
        name="990-index",
        display_name="990 Index Loader",
        command=["python3", "lavandula/dashboard/manage.py", "load_990_index"],
        parameters={
            "ein": ParamSpec(type="string", cli_flag="--ein", pattern=r"^\d{9}$"),
            "years": ParamSpec(type="string", cli_flag="--years", pattern=r"^\d{4}(\s*,\s*\d{4})*$"),
            "current_year": ParamSpec(type="boolean", cli_flag="--current-year"),
        },
        predecessors=[],
        conflict_group="990-family",
        provenance_column="filing_990_status",
        retry_policy=RetryPolicy(max_attempts=1),
        resource_class="light",
    ),
    "990-parse": StageDefinition(
        name="990-parse",
        display_name="990 Parser",
        command=["python3", "lavandula/dashboard/manage.py", "process_990_auto"],
        parameters={
            "ein": ParamSpec(type="string", cli_flag="--ein", pattern=r"^\d{9}$"),
            "reparse": ParamSpec(type="boolean", cli_flag="--reparse"),
            "backfill": ParamSpec(type="boolean", cli_flag="--backfill"),
        },
        predecessors=["990-index"],
        conflict_group="990-family",
        provenance_column=None,
        retry_policy=RetryPolicy(max_attempts=2, auto_retry=True, retryable_exit_codes=(1, 3)),
        resource_class="medium",
    ),
    "enrich-phone": StageDefinition(
        name="enrich-phone",
        display_name="Phone Enrichment",
        command=["python3", "-m", "lavandula.nonprofits.tools.pipeline_enrich_phone"],
        parameters={
            "state": ParamSpec(type="state_code", cli_flag="--state"),
            "limit": ParamSpec(type="integer", cli_flag="--limit", min_value=0, max_value=999999),
            "search_engines": ParamSpec(type="string", cli_flag="--search-engines", pattern=r"^[a-z,_]+$"),
        },
        predecessors=["resolve"],
        conflict_group="per-state",
        provenance_column=None,
        retry_policy=RetryPolicy(max_attempts=2, auto_retry=True, retryable_exit_codes=(1, 3)),
        resource_class="light",
    ),
}


class RegistryValidationError(Exception):
    pass


def validate_registry(registry: dict[str, StageDefinition] | None = None) -> list[str]:
    """Validate the stage registry. Returns list of error messages (empty = valid)."""
    if registry is None:
        registry = STAGE_REGISTRY

    errors = []

    seen_columns = {}
    for name, stage in registry.items():
        if stage.name != name:
            errors.append(f"Stage key '{name}' doesn't match stage.name '{stage.name}'")

        for pred in stage.predecessors:
            if pred not in registry:
                errors.append(f"Stage '{name}': predecessor '{pred}' not in registry")

        if stage.provenance_column:
            if stage.provenance_column in seen_columns:
                errors.append(
                    f"Stage '{name}': provenance_column '{stage.provenance_column}' "
                    f"already used by '{seen_columns[stage.provenance_column]}'"
                )
            seen_columns[stage.provenance_column] = name

        if stage.conflict_group and stage.conflict_group not in ("per-state", "global", "990-family"):
            errors.append(f"Stage '{name}': invalid conflict_group '{stage.conflict_group}'")

        if stage.resource_class not in ("heavy", "medium", "light"):
            errors.append(f"Stage '{name}': invalid resource_class '{stage.resource_class}'")

        if stage.retry_policy.max_attempts > 3:
            errors.append(f"Stage '{name}': retry max_attempts > 3")

    # Check for circular predecessors via DFS
    def _has_cycle(start: str, visited: set, path: set) -> bool:
        if start in path:
            return True
        if start in visited:
            return False
        visited.add(start)
        path.add(start)
        if start in registry:
            for pred in registry[start].predecessors:
                if _has_cycle(pred, visited, path):
                    return True
        path.remove(start)
        return False

    visited: set[str] = set()
    for name in registry:
        if _has_cycle(name, visited, set()):
            errors.append(f"Circular predecessor dependency detected involving '{name}'")
            break

    return errors
