def pipeline_config(request):
    from pipeline.models import PipelineConfig
    try:
        config = PipelineConfig.get()
        return {
            "queue_paused": config.queue_paused,
            "paused_by": config.paused_by,
            "paused_at": config.paused_at,
        }
    except Exception:
        return {"queue_paused": False}
