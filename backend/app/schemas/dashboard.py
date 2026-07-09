from __future__ import annotations


from pydantic import BaseModel, ConfigDict


class ProjectOpenTasksRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: int
    project_name: str
    open_task_count: int


class DashboardRead(BaseModel):
    total_open_tasks: int
    projects: list[ProjectOpenTasksRow]
