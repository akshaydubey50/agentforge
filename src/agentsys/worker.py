from celery import Celery

from agentsys.config import settings

celery_app = Celery("agentsys", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(task_track_started=True, task_serializer="json", result_serializer="json")


@celery_app.task(name="agentsys.ping_task")
def ping_task() -> str:
    from agentsys.db.models import Task, TaskStatus
    from agentsys.db.session import get_session, init_db

    init_db()
    with get_session() as session:
        task = Task(request_text="ping", status=TaskStatus.COMPLETED, final_output="pong")
        session.add(task)
        session.commit()
        session.refresh(task)
        return task.id


@celery_app.task(name="agentsys.run_agent_task", bind=True, max_retries=0)
def run_agent_task(self, task_id: str) -> str:
    """Runs (or resumes) a task's graph to completion or the next escalation.
    Any unhandled exception marks the task FAILED rather than leaving it
    stuck in RUNNING forever — a crash shouldn't be invisible."""
    from agentsys.db.models import Task, TaskStatus
    from agentsys.db.session import get_session, init_db
    from agentsys.graph.runner import run_task

    init_db()
    try:
        run_task(task_id)
    except Exception as exc:
        with get_session() as session:
            task = session.get(Task, task_id)
            if task:
                task.status = TaskStatus.FAILED
                task.final_output = f"Unhandled error: {exc}"
                session.add(task)
                session.commit()
        raise
    return task_id
