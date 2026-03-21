import pytest

from access_control.models import Project


@pytest.mark.django_db
def test_create_project(client):
    response = client.post(
        "/api/v1/projects/",
        data={"name": "Acme", "slug": "acme"},
        content_type="application/json",
    )

    assert response.status_code == 201
    assert response.json()["name"] == "Acme"
    assert response.json()["slug"] == "acme"
    assert Project.objects.filter(slug="acme").exists()


@pytest.mark.django_db
def test_list_projects(client):
    project_a = Project.objects.create(name="Zulu", slug="zulu")
    project_b = Project.objects.create(name="Alpha", slug="alpha")

    response = client.get("/api/v1/projects/")

    assert response.status_code == 200
    assert response.json() == [
        {"id": str(project_b.id), "name": "Alpha", "slug": "alpha"},
        {"id": str(project_a.id), "name": "Zulu", "slug": "zulu"},
    ]


@pytest.mark.django_db
def test_get_project(client):
    project = Project.objects.create(name="Acme", slug="acme")

    response = client.get(f"/api/v1/projects/{project.slug}/")

    assert response.status_code == 200
    assert response.json() == {
        "id": str(project.id),
        "name": "Acme",
        "slug": "acme",
    }


@pytest.mark.django_db
def test_get_project_not_found(client):
    response = client.get("/api/v1/projects/missing-project/")

    assert response.status_code == 404
    assert response.json() == {"detail": "Project not found"}
