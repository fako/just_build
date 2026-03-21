from django.http import HttpRequest
from ninja import ModelSchema, Router
from ninja.errors import HttpError

from access_control.models import Project


controller = Router()


class ProjectSchema(ModelSchema):
    class Meta:
        model = Project
        fields = ["id", "name", "slug"]


class ProjectCreateSchema(ModelSchema):
    class Meta:
        model = Project
        fields = ["name", "slug"]


@controller.post("/", response={201: ProjectSchema}, tags=["Projects"])
def create_project(request: HttpRequest, data: ProjectCreateSchema) -> tuple[int, Project]:
    project = Project.objects.create(**data.model_dump())
    return 201, project


@controller.get("/", response=list[ProjectSchema], tags=["Projects"])
def list_projects(request: HttpRequest) -> list[Project]:
    return list(Project.objects.order_by("name"))


@controller.get("/{project_slug}/", response=ProjectSchema, tags=["Projects"])
def get_project(request: HttpRequest, project_slug: str) -> Project:
    try:
        return Project.objects.get(slug=project_slug)
    except Project.DoesNotExist as exc:
        raise HttpError(404, "Project not found") from exc
