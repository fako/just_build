import pytest

from access_control.models import Workspace


@pytest.mark.django_db
def test_create_workspace(control_client):
    response = control_client.post(
        "/api/v1/workspaces/",
        data={"name": "Data Growth Django", "module": "datagrowth_django"},
        content_type="application/json",
    )

    assert response.status_code == 201
    assert response.json()["name"] == "Data Growth Django"
    assert response.json()["module"] == "datagrowth_django"
    assert response.json()["slug"] == "datagrowth-django"
    assert response.json()["setup"] == {}
    assert response.json()["api_key"].startswith("workspace:")
    assert response.json()["ssh"] == {
        "alias": None,
        "user": None,
        "host": None,
        "port": None,
        "identity_file": None,
    }
    assert Workspace.objects.filter(module="datagrowth_django", slug="datagrowth-django").exists()


@pytest.mark.django_db
def test_create_workspace_conflict(control_client):
    Workspace.objects.create(name="Acme", module="acme")

    response = control_client.post(
        "/api/v1/workspaces/",
        data={"name": "Acme Again", "module": "acme"},
        content_type="application/json",
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Workspace already exists"}


@pytest.mark.django_db
def test_list_workspaces(control_client):
    workspace_a = Workspace.objects.create(name="Zulu", module="zulu")
    workspace_b = Workspace.objects.create(name="Alpha", module="alpha")

    response = control_client.get("/api/v1/workspaces/")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": str(workspace_b.id),
            "name": "Alpha",
            "module": "alpha",
            "slug": "alpha",
            "setup": {},
            "ssh": {
                "alias": None,
                "user": None,
                "host": None,
                "port": None,
                "identity_file": None,
            },
            "git_public_key": "",
        },
        {
            "id": str(workspace_a.id),
            "name": "Zulu",
            "module": "zulu",
            "slug": "zulu",
            "setup": {},
            "ssh": {
                "alias": None,
                "user": None,
                "host": None,
                "port": None,
                "identity_file": None,
            },
            "git_public_key": "",
        },
    ]


@pytest.mark.django_db
def test_get_workspace(control_client):
    workspace = Workspace.objects.create(name="Acme", module="acme")

    response = control_client.get(f"/api/v1/workspaces/{workspace.module}/")

    assert response.status_code == 200
    assert response.json() == {
        "id": str(workspace.id),
        "name": "Acme",
        "module": "acme",
        "slug": "acme",
        "setup": {},
        "ssh": {
            "alias": None,
            "user": None,
            "host": None,
            "port": None,
            "identity_file": None,
        },
        "git_public_key": "",
    }


@pytest.mark.django_db
def test_get_workspace_not_found(control_client):
    response = control_client.get("/api/v1/workspaces/missing-workspace/")

    assert response.status_code == 404
    assert response.json() == {"detail": "Workspace not found"}


@pytest.mark.django_db
def test_delete_workspace(control_client):
    workspace = Workspace.objects.create(name="Acme", module="acme")

    response = control_client.delete(f"/api/v1/workspaces/{workspace.module}/")

    assert response.status_code == 204
    assert not Workspace.objects.filter(module="acme").exists()


@pytest.mark.django_db
def test_delete_workspace_not_found(control_client):
    response = control_client.delete("/api/v1/workspaces/missing/")

    assert response.status_code == 404
    assert response.json() == {"detail": "Workspace not found"}


@pytest.mark.django_db
def test_patch_workspace_setup_and_ssh_metadata(control_client):
    workspace = Workspace.objects.create(
        name="Acme", module="acme", setup={"workspace_directory": "2026-03-25T10:00:00Z"}
    )

    response = control_client.patch(
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
                "identity_file": "workspaces/ssh/keys/acme/id_ed25519",
            },
        },
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["setup"] == {
        "workspace_directory": "2026-03-25T10:00:00Z",
        "ssh_access": "2026-03-25T11:00:00Z",
    }
    assert response.json()["ssh"] == {
        "alias": "acme-workspace",
        "user": "acme",
        "host": "localhost",
        "port": 2222,
        "identity_file": "workspaces/ssh/keys/acme/id_ed25519",
    }

    workspace.refresh_from_db()
    assert workspace.setup["workspace_directory"] == "2026-03-25T10:00:00Z"
    assert workspace.setup["ssh_access"] == "2026-03-25T11:00:00Z"
    assert workspace.ssh == {
        "alias": "acme-workspace",
        "user": "acme",
        "host": "localhost",
        "port": 2222,
        "identity_file": "workspaces/ssh/keys/acme/id_ed25519",
    }


@pytest.mark.django_db
def test_patch_workspace_git_public_key(control_client):
    workspace = Workspace.objects.create(name="Acme", module="acme")

    response = control_client.patch(
        f"/api/v1/workspaces/{workspace.module}/",
        data={"git_public_key": "ssh-ed25519 AAAAC3Nz acme@workspace.local\n"},
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["git_public_key"] == "ssh-ed25519 AAAAC3Nz acme@workspace.local"

    workspace.refresh_from_db()
    # Stored without the newline ssh-keygen leaves behind, so it can be pasted straight into a
    # deploy key field out of the admin.
    assert workspace.git_public_key == "ssh-ed25519 AAAAC3Nz acme@workspace.local"


@pytest.mark.django_db
def test_patch_workspace_keeps_the_git_public_key_it_is_not_given(control_client):
    workspace = Workspace.objects.create(name="Acme", module="acme", git_public_key="ssh-ed25519 AAAAC3Nz")

    response = control_client.patch(
        f"/api/v1/workspaces/{workspace.module}/",
        data={"setup": {"ssh_access": "2026-03-25T11:00:00Z"}},
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["git_public_key"] == "ssh-ed25519 AAAAC3Nz"


@pytest.mark.django_db
def test_patch_workspace_not_found(control_client):
    response = control_client.patch(
        "/api/v1/workspaces/missing-workspace/",
        data={"setup": {"ssh_access": "2026-03-25T11:00:00Z"}},
        content_type="application/json",
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Workspace not found"}


@pytest.mark.django_db
def test_get_ssh_config(control_client):
    Workspace.objects.create(
        name="Acme",
        module="acme",
        ssh={
            "alias": "acme-workspace",
            "user": "acme",
            "host": "localhost",
            "port": 2222,
            "identity_file": "workspaces/ssh/keys/acme/id_ed25519",
        },
    )
    Workspace.objects.create(name="No SSH", module="no_ssh")

    response = control_client.get("/api/v1/workspaces/ssh-config/?repository_root=/checkout/just_build")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain")
    # Resolved against the caller's checkout, which is nowhere near where management sees itself.
    assert response.content.decode() == (
        "# Automatically generated by the Just Build management API.\n"
        "# Do not edit this file directly.\n"
        "\n"
        "Host acme-workspace\n"
        "    HostName localhost\n"
        "    Port 2222\n"
        "    User acme\n"
        "    IdentityFile /checkout/just_build/workspaces/ssh/keys/acme/id_ed25519\n"
    )


@pytest.mark.django_db
def test_get_ssh_config_keeps_an_absolute_identity_file_as_it_is(control_client):
    Workspace.objects.create(
        name="Acme",
        module="acme",
        ssh={
            "alias": "acme-workspace",
            "user": "acme",
            "host": "localhost",
            "port": 2222,
            "identity_file": "/elsewhere/acme/id_ed25519",
        },
    )

    response = control_client.get("/api/v1/workspaces/ssh-config/?repository_root=/checkout/just_build")

    assert "    IdentityFile /elsewhere/acme/id_ed25519\n" in response.content.decode()


@pytest.mark.django_db
def test_get_ssh_config_refuses_a_relative_repository_root(control_client):
    response = control_client.get("/api/v1/workspaces/ssh-config/?repository_root=just_build")

    assert response.status_code == 422


@pytest.mark.django_db
def test_patch_workspace_rejects_invalid_ssh_shape(control_client):
    workspace = Workspace.objects.create(name="Acme", module="acme")

    response = control_client.patch(
        f"/api/v1/workspaces/{workspace.module}/",
        data={"ssh": {"port": "not-a-port"}},
        content_type="application/json",
    )

    assert response.status_code == 422
