import logging
import threading

log = logging.getLogger(__name__)


def merge_stats(job_id, stats_dict):
    from pipeline.models import Job
    job = Job.objects.get(pk=job_id)
    cfg = job.config_json or {}
    cfg["stats"] = dict(stats_dict)
    Job.objects.filter(pk=job_id).update(config_json=cfg)


def start_stats_flusher(job_id, stats_dict, interval=10):
    if job_id is None:
        return threading.Event()

    stop = threading.Event()

    def _flusher():
        while not stop.wait(interval):
            try:
                merge_stats(job_id, stats_dict)
            except Exception:
                log.exception("Stats flush failed for job %d", job_id)

    t = threading.Thread(target=_flusher, daemon=True)
    t.start()
    return stop
