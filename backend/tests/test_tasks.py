from sqlalchemy.orm import Session

from app.db.models import TaskPriority, TaskStatus
from app.services import projects as projects_service
from app.services import tasks as tasks_service


def test_task_create_markdone_softdelete(db_session: Session) -> None:
    project = projects_service.create_project(db_session, name="Firewall")

    task = tasks_service.create_task(
        db_session, project_id=project.id, title="audit rules"
    )
    assert task.id is not None
    assert task.project_id == project.id
    assert task.status == TaskStatus.accepted
    assert task.priority == TaskPriority.medium

    assert task.id in [t.id for t in tasks_service.list_tasks(db_session, project.id)]

    done = tasks_service.mark_done(db_session, task)
    assert done.status == TaskStatus.done

    tasks_service.soft_delete_task(db_session, task)

    assert tasks_service.get_task(db_session, task.id) is None
    assert task.id not in [
        t.id for t in tasks_service.list_tasks(db_session, project.id)
    ]
