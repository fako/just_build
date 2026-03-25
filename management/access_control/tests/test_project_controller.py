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
    assert response.json()["django_module"] == "web"
    assert response.json()["setup"] == {}
    assert response.json()["ssh"] == {
        "alias": None,
        "user": None,
        "host": None,
        "port": None,
        "identity_file": None,
    }
    assert Project.objects.filter(slug="acme").exists()


@pytest.mark.django_db
def test_create_project_conflict(client):
    Project.objects.create(name="Acme", slug="acme")

    response = client.post(
        "/api/v1/projects/",
        data={"name": "Acme Again", "slug": "acme"},
        content_type="application/json",
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Project already exists"}


@pytest.mark.django_db
def test_list_projects(client):
    project_a = Project.objects.create(name="Zulu", slug="zulu")
    project_b = Project.objects.create(name="Alpha", slug="alpha")

    response = client.get("/api/v1/projects/")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": str(project_b.id),
            "name": "Alpha",
            "slug": "alpha",
            "django_module": "web",
            "setup": {},
            "ssh": {
                "alias": None,
                "user": None,
                "host": None,
                "port": None,
                "identity_file": None,
            },
        },
        {
            "id": str(project_a.id),
            "name": "Zulu",
            "slug": "zulu",
            "django_module": "web",
            "setup": {},
            "ssh": {
                "alias": None,
                "user": None,
                "host": None,
                "port": None,
                "identity_file": None,
            },
        },
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
        "django_module": "web",
        "setup": {},
        "ssh": {
            "alias": None,
            "user": None,
            "host": None,
            "port": None,
            "identity_file": None,
        },
    }


@pytest.mark.django_db
def test_get_project_not_found(client):
    response = client.get("/api/v1/projects/missing-project/")

    assert response.status_code == 404
    assert response.json() == {"detail": "Project not found"}


@pytest.mark.django_db
def test_patch_project_setup_and_ssh_metadata(client):
    project = Project.objects.create(name="Acme", slug="acme", setup={"workspace_directory": "2026-03-25T10:00:00Z"})

    response = client.patch(
        f"/api/v1/projects/{project.slug}/",
        data={
            "setup": {
                "ssh_access": "2026-03-25T11:00:00Z",
            },
            "ssh": {
                "alias": "acme-workspace",
                "user": "acme",
                "host": "localhost",
                "port": 2222,
                "identity_file": "workspaces/ssh/keys/projects/acme/id_ed25519",
            },
        },
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["django_module"] == "web"
    assert response.json()["setup"] == {
        "workspace_directory": "2026-03-25T10:00:00Z",
        "ssh_access": "2026-03-25T11:00:00Z",
    }
    assert response.json()["ssh"] == {
        "alias": "acme-workspace",
        "user": "acme",
        "host": "localhost",
        "port": 2222,
        "identity_file": "workspaces/ssh/keys/projects/acme/id_ed25519",
    }

    project.refresh_from_db()
    assert project.setup["workspace_directory"] == "2026-03-25T10:00:00Z"
    assert project.setup["ssh_access"] == "2026-03-25T11:00:00Z"
    assert project.ssh == {
        "alias": "acme-workspace",
        "user": "acme",
        "host": "localhost",
        "port": 2222,
        "identity_file": "workspaces/ssh/keys/projects/acme/id_ed25519",
    }


@pytest.mark.django_db
def test_patch_project_not_found(client):
    response = client.patch(
        "/api/v1/projects/missing-project/",
        data={"setup": {"ssh_access": "2026-03-25T11:00:00Z"}},
        content_type="application/json",
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Project not found"}


@pytest.mark.django_db
def test_get_ssh_config(client, settings):
    settings.BASE_DIR = settings.BASE_DIR.parent / "management"

    Project.objects.create(
        name="Acme",
        slug="acme",
        ssh={
            "alias": "acme-workspace",
            "user": "acme",
            "host": "localhost",
            "port": 2222,
            "identity_file": "workspaces/ssh/keys/projects/acme/id_ed25519",
        },
    )
    Project.objects.create(name="No SSH", slug="no-ssh")

    response = client.get("/api/v1/projects/ssh-config/")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain")
    assert response.content.decode() == (
        "# Automatically generated by the Just Build management API.\n"
        "# Do not edit this file directly.\n"
        "\n"
        "Host acme-workspace\n"
        "    HostName localhost\n"
        "    Port 2222\n"
        "    User acme\n"
        f"    IdentityFile {settings.BASE_DIR.parent / 'workspaces/ssh/keys/projects/acme/id_ed25519'}\n"
    )


@pytest.mark.django_db
def test_patch_project_rejects_invalid_ssh_shape(client):
    project = Project.objects.create(name="Acme", slug="acme")

    response = client.patch(
        f"/api/v1/projects/{project.slug}/",
        data={"ssh": {"port": "not-a-port"}},
        content_type="application/json",
    )

    assert response.status_code == 422
