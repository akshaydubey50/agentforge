from celery import Celery
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import OperationalError

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


@celery_app.task(
    name="agentsys.run_agent_task",
    bind=True,
    autoretry_for=(OperationalError, RedisConnectionError),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def run_agent_task(self, task_id: str) -> str:
    """Runs (or resumes) a task's graph to completion or the next escalation.

    Two different failure modes are handled deliberately differently: a
    transient Postgres/Redis blip (OperationalError/ConnectionError) is
    infrastructure hiccuping, not the agent being wrong -- it's re-raised
    immediately so Celery's autoretry_for above actually gets a chance to
    retry it (a broad except here would swallow it first and Celery would
    never see it). Anything else is treated as a real failure: the task is
    marked FAILED with the error message rather than left stuck in RUNNING
    forever, and it is NOT retried, since silently re-running broken plan/
    tool logic three times before surfacing it would hide the problem, not
    fix it."""
    from agentsys.db.models import Task, TaskStatus
    from agentsys.db.session import get_session, init_db
    from agentsys.graph.runner import run_task

    init_db()
    try:
        run_task(task_id)
    except (OperationalError, RedisConnectionError):
        raise
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
