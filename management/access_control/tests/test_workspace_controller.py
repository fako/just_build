import pytest

from access_control.models import Workspace


@pytest.mark.django_db
def test_create_workspace(client):
    response = client.post(
        "/api/v1/workspaces/",
        data={"name": "Data Growth Django", "module": "datagrowth_django"},
        content_type="application/json",
    )

    assert response.status_code == 201
    assert response.json()["name"] == "Data Growth Django"
    assert response.json()["module"] == "datagrowth_django"
    assert response.json()["slug"] == "datagrowth-django"
    assert response.json()["django_module"] == "web"
    assert response.json()["setup"] == {}
    assert response.json()["ssh"] == {
        "alias": None,
        "user": None,
        "host": None,
        "port": None,
        "identity_file": None,
    }
    assert Workspace.objects.filter(module="datagrowth_django", slug="datagrowth-django").exists()


@pytest.mark.django_db
def test_create_workspace_conflict(client):
    Workspace.objects.create(name="Acme", module="acme")

    response = client.post(
        "/api/v1/workspaces/",
        data={"name": "Acme Again", "module": "acme"},
        content_type="application/json",
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Workspace already exists"}


@pytest.mark.django_db
def test_list_workspaces(client):
    workspace_a = Workspace.objects.create(name="Zulu", module="zulu")
    workspace_b = Workspace.objects.create(name="Alpha", module="alpha")

    response = client.get("/api/v1/workspaces/")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": str(workspace_b.id),
            "name": "Alpha",
            "module": "alpha",
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
            "id": str(workspace_a.id),
            "name": "Zulu",
            "module": "zulu",
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
def test_get_workspace(client):
    workspace = Workspace.objects.create(name="Acme", module="acme")

    response = client.get(f"/api/v1/workspaces/{workspace.module}/")

    assert response.status_code == 200
    assert response.json() == {
        "id": str(workspace.id),
        "name": "Acme",
        "module": "acme",
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
def test_get_workspace_not_found(client):
    response = client.get("/api/v1/workspaces/missing-workspace/")

    assert response.status_code == 404
    assert response.json() == {"detail": "Workspace not found"}


@pytest.mark.django_db
def test_delete_workspace(client):
    workspace = Workspace.objects.create(name="Acme", module="acme")

    response = client.delete(f"/api/v1/workspaces/{workspace.module}/")

    assert response.status_code == 204
    assert not Workspace.objects.filter(module="acme").exists()


@pytest.mark.django_db
def test_delete_workspace_not_found(client):
    response = client.delete("/api/v1/workspaces/missing/")

    assert response.status_code == 404
    assert response.json() == {"detail": "Workspace not found"}


@pytest.mark.django_db
def test_patch_workspace_setup_and_ssh_metadata(client):
    workspace = Workspace.objects.create(
        name="Acme", module="acme", setup={"workspace_directory": "2026-03-25T10:00:00Z"}
    )

    response = client.patch(
        f"/api/v1/workspaces/{workspace.module}/",
        data={
            "setup": {
                "ssh_access": "2026-03-25T11:00:00Z",
            },
            "ssh": {
                "alias": "acme-workspace",
                "user": "acme",
                "host": "localhost",
                "port": 2222,
                "identity_file": "workspaces/ssh/keys/src/acme/id_ed25519",
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
        "identity_file": "workspaces/ssh/keys/src/acme/id_ed25519",
    }

    workspace.refresh_from_db()
    assert workspace.setup["workspace_directory"] == "2026-03-25T10:00:00Z"
    assert workspace.setup["ssh_access"] == "2026-03-25T11:00:00Z"
    assert workspace.ssh == {
        "alias": "acme-workspace",
        "user": "acme",
        "host": "localhost",
        "port": 2222,
        "identity_file": "workspaces/ssh/keys/src/acme/id_ed25519",
    }


@pytest.mark.django_db
def test_patch_workspace_not_found(client):
    response = client.patch(
        "/api/v1/workspaces/missing-workspace/",
        data={"setup": {"ssh_access": "2026-03-25T11:00:00Z"}},
        content_type="application/json",
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Workspace not found"}


@pytest.mark.django_db
def test_get_ssh_config(client, settings):
    settings.BASE_DIR = settings.BASE_DIR.parent / "management"

    Workspace.objects.create(
        name="Acme",
        module="acme",
        ssh={
            "alias": "acme-workspace",
            "user": "acme",
            "host": "localhost",
            "port": 2222,
            "identity_file": "workspaces/ssh/keys/src/acme/id_ed25519",
        },
    )
    Workspace.objects.create(name="No SSH", module="no_ssh")

    response = client.get("/api/v1/workspaces/ssh-config/")

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
        f"    IdentityFile {settings.BASE_DIR.parent / 'workspaces/ssh/keys/src/acme/id_ed25519'}\n"
    )


@pytest.mark.django_db
def test_patch_workspace_rejects_invalid_ssh_shape(client):
    workspace = Workspace.objects.create(name="Acme", module="acme")

    response = client.patch(
        f"/api/v1/workspaces/{workspace.module}/",
        data={"ssh": {"port": "not-a-port"}},
        content_type="application/json",
    )

    assert response.status_code == 422
