import logging

from celery import Celery
from celery.exceptions import SoftTimeLimitExceeded
from celery.signals import worker_ready
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import OperationalError

from agentsys.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery("agentsys", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    # Fault tolerance: ack a task only AFTER it finishes, so a
    # SIGKILL/OOM/container-restart mid-task doesn't silently drop the
    # message. prefetch_multiplier=1 keeps a worker from reserving extra
    # messages it would strand if it died.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # False, despite acks_late, specifically BECAUSE of the time limits
    # below. With reject_on_worker_lost=True, a task killed by the hard
    # limit is indistinguishable from a crashed worker, so it gets
    # redelivered -- and a task that hangs once hangs again on the next
    # worker, and the next, billing the whole way. That's the expensive
    # default: a job that ran 25 minutes and produced nothing is not a
    # transient failure worth retrying. The "message was genuinely lost"
    # case this used to cover is still covered, independently, by
    # recovery.recover_stranded_tasks() sweeping stale RUNNING rows on
    # worker boot (see recovery.py) -- Celery's redelivery was never the
    # only backstop.
    task_reject_on_worker_lost=False,
    # Wall-clock backstops. max_task_steps bounds how many steps a task
    # takes; nothing bounds how long a single step can hang (a provider
    # holding a socket, a tool waiting on a connection), and a stalled loop
    # doesn't error -- it just bills. Soft raises inside the task so
    # run_agent_task can record a readable cause; hard SIGKILLs the child
    # if a broad `except` swallowed the soft one. The hard kill only
    # actually works on the prefork pool -- see docker-compose.yml's worker
    # command for why the pool choice and these limits are one decision.
    task_soft_time_limit=settings.task_soft_time_limit_seconds,
    task_time_limit=settings.task_time_limit_seconds,
)


@worker_ready.connect
def _recover_stranded_on_boot(**_kwargs) -> None:
    """Backstop for the rarer case where a crashed worker's message was lost
    rather than redelivered: on boot, re-enqueue Tasks left stale in RUNNING
    (see recovery.recover_stranded_tasks). Best-effort -- a failure here must
    never stop the worker from starting."""
    try:
        from agentsys.db.session import init_db
        from agentsys.recovery import recover_stranded_tasks

        init_db()
        recovered = recover_stranded_tasks()
        if recovered:
            logger.warning("recovered %d stranded task(s) on boot: %s", len(recovered), recovered)
    except Exception as exc:  # noqa: BLE001 -- boot recovery must not crash the worker
        logger.warning("stranded-task recovery on boot failed (continuing anyway): %s", exc)


@celery_app.task(name="agentsys.ping_task")
def ping_task() -> str:
    from agentsys.auth import get_or_create_system_user
    from agentsys.db.models import Task, TaskStatus
    from agentsys.db.session import get_session, init_db

    init_db()
    owner_id = get_or_create_system_user("health-check", "health-check@agentforge.local", "Health Check")
    with get_session() as session:
        task = Task(request_text="ping", status=TaskStatus.COMPLETED, final_output="pong", owner_id=owner_id)
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
    from agentsys.cancellation import TaskCancelled
    from agentsys.db.models import Task, TaskStatus
    from agentsys.db.session import get_session, init_db
    from agentsys.graph.runner import run_task

    init_db()
    try:
        run_task(task_id)
    except TaskCancelled:
        # Deliberate stop, not a failure -- must never be recorded as FAILED.
        # The API already set CANCELLED (main.py's cancel_task), but a node
        # that was mid-step when the cancel landed can have written its own
        # status afterwards, so reconcile here rather than assume: this is
        # the one point that knows for certain the run stopped because of a
        # cancel. Without it a cancelled task can sit in RUNNING forever.
        logger.info("task %s stopped: cancelled by user", task_id)
        with get_session() as session:
            task = session.get(Task, task_id)
            if task and task.status != TaskStatus.CANCELLED:
                task.status = TaskStatus.CANCELLED
                session.add(task)
                session.commit()
        return task_id
    except SoftTimeLimitExceeded:
        # Distinct from a generic crash: the task didn't do anything wrong,
        # it ran out of wall clock (see the time-limit config above). Worth
        # its own message so the user sees a cause they can act on rather
        # than "Unhandled error: SoftTimeLimitExceeded()".
        logger.warning("task %s exceeded its soft time limit", task_id)
        with get_session() as session:
            task = session.get(Task, task_id)
            if task:
                task.status = TaskStatus.FAILED
                task.final_output = (
                    f"Stopped: exceeded the {settings.task_soft_time_limit_seconds}s time limit "
                    "for a single run."
                )
                session.add(task)
                session.commit()
        return task_id
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
