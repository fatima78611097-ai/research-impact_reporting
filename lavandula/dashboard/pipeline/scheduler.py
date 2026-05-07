"""
Resource-aware job scheduler for the pipeline control plane.

Scores job-host pairings using hard rules (gates) and soft preferences (weights).
The highest-scoring valid pairing wins. Greedy assignment prevents double-booking.
"""
from __future__ import annotations

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from .models import Job, Worker
from .stages import STAGE_REGISTRY
from .scheduler_config import SchedulerConfig, load_config


def score_placement(job: Job, worker: Worker, config: SchedulerConfig) -> float | None:
    """Score a job-host pairing. Returns None if a hard rule blocks placement."""
    stage = STAGE_REGISTRY.get(job.phase)
    if stage is None:
        return None

    stage_config = config.stage_weights.get(job.phase, {})
    now = timezone.now()

    # === HARD RULES (gates) ===

    if worker.cpu_pct is not None and worker.cpu_pct > config.cpu_ceiling_pct:
        return None

    if worker.mem_pct is not None and worker.mem_pct > config.memory_ceiling_pct:
        return None

    if stage.resource_class == "heavy":
        heavy_stages = [s.name for s in STAGE_REGISTRY.values() if s.resource_class == "heavy"]
        running_heavy = Job.objects.filter(
            host=worker.hostname, status="running", phase__in=heavy_stages
        ).count()
        if running_heavy >= config.max_concurrent_heavy:
            return None

    if stage_config.get("gpu_preferred") and not worker.has_gpu:
        return None

    if config.cool_down_after_failure_s:
        cutoff = now - timedelta(seconds=config.cool_down_after_failure_s)
        if Job.objects.filter(
            host=worker.hostname, status="failed", finished_at__gte=cutoff
        ).exists():
            return None

    # === SOFT SCORING ===
    score = 1.0

    if worker.hostname in stage_config.get("prefer_hosts", []):
        score += 0.3

    if worker.mem_pct is not None:
        score += (100 - worker.mem_pct) / 100 * 0.5

    wait_minutes = (now - job.created_at).total_seconds() / 60
    if wait_minutes > config.starvation_boost_after_minutes:
        score += 0.5

    if config.eta_lookahead:
        running_on_host = Job.objects.filter(host=worker.hostname, status="running")
        for rj in running_on_host:
            if rj.progress_total and rj.progress_current:
                pct_done = rj.progress_current / rj.progress_total
                if pct_done > 0.9:
                    score += 0.2
                    break

    return score


def select_next_jobs(
    pending_jobs,
    workers,
    config: SchedulerConfig | None = None,
) -> list[tuple[Job, Worker]]:
    """Score all pending x worker combinations, return best pairings.

    Greedy assignment: pick highest-scored pair, mark both assigned, repeat.
    Each worker gets at most one new job per scheduling round.
    """
    if config is None:
        config = load_config()

    candidates = []
    for job in pending_jobs:
        for worker in workers:
            s = score_placement(job, worker, config)
            if s is not None:
                candidates.append((s, job.pk, worker.hostname, job, worker))

    candidates.sort(key=lambda x: -x[0])

    assigned_workers: set[str] = set()
    assigned_jobs: set[int] = set()
    result = []

    for _score, job_pk, hostname, job, worker in candidates:
        if hostname not in assigned_workers and job_pk not in assigned_jobs:
            result.append((job, worker))
            assigned_workers.add(hostname)
            assigned_jobs.add(job_pk)

    return result


def is_dependency_satisfied(job: Job) -> bool:
    """Check if a job's dependency is met, following retry chains."""
    dep = job.depends_on
    if dep is None:
        return True
    current = dep
    while current:
        if current.status == "completed":
            return True
        current = current.retries.order_by("-attempt_number").first()
    return False
