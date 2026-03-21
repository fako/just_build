import json
import os
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone as datetime_timezone
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.dateparse import parse_datetime


class CredentialStoreError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ParsedCredential:
    value: str
    headers: dict[str, str]
    notes: str


def _parse_git_timestamp(raw_value: str) -> datetime | None:
    if not raw_value:
        return None

    parsed = parse_datetime(raw_value.strip())
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, datetime_timezone.utc)
    return parsed


def _split_path(path: str) -> tuple[str, str, str]:
    parts = [part for part in path.split("/") if part]
    if not parts:
        return ("root", "", "")
    if len(parts) == 1:
        return ("root", "", parts[0])
    return (parts[0], "/".join(parts[1:-1]), parts[-1])


def _normalize_credential_path(path: str) -> str:
    return path.strip().strip("/")


def _is_excluded_path(path: str, excluded_paths: set[str]) -> bool:
    normalized = _normalize_credential_path(path)
    return any(
        normalized == excluded_path or normalized.startswith(f"{excluded_path}/")
        for excluded_path in excluded_paths
    )


class CredentialStore(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    repository_path = models.CharField(max_length=1024)
    store_key = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    @property
    def repository(self) -> Path:
        path = Path(self.repository_path).expanduser()
        if path.is_absolute():
            return path
        return Path(settings.BASE_DIR) / path

    def ensure_repository_exists(self) -> None:
        if not self.repository.exists():
            raise CredentialStoreError(f"Credential store path '{self.repository}' does not exist.")
        if not self.repository.is_dir():
            raise CredentialStoreError(f"Credential store path '{self.repository}' is not a directory.")

    def entry_file_for(self, key: str) -> Path:
        normalized_key = key.strip().strip("/")
        return self.repository / f"{normalized_key}.gpg"

    def credential_paths(self) -> list[str]:
        self.ensure_repository_exists()

        paths: list[str] = []
        for entry in self.repository.rglob("*.gpg"):
            relative_path = entry.relative_to(self.repository)
            if any(part.startswith(".") for part in relative_path.parts):
                continue
            paths.append(str(relative_path.with_suffix("")).strip("/"))

        return sorted(paths)

    def pass_command_environment(self) -> dict[str, str]:
        environment: dict[str, str] = {}
        for key in ("PATH", "HOME", "LANG", "LC_ALL"):
            value = os.environ.get(key)
            if value:
                environment[key] = value

        environment["PASSWORD_STORE_DIR"] = str(self.repository)
        environment["PASSWORD_STORE_GPG_OPTS"] = "--trust-model always"

        if self.store_key:
            environment["PASSWORD_STORE_KEY"] = self.store_key

        assert settings.GPGHOME, "Missing management credentials gpghome configuration."
        environment["GNUPGHOME"] = settings.GPGHOME

        return environment

    def run_pass_command(self, arguments: list[str], *,
                         input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        command = ["pass", *arguments]
        try:
            return subprocess.run(
                command,
                input=input_text,
                check=True,
                capture_output=True,
                text=True,
                env=self.pass_command_environment(),
            )
        except FileNotFoundError as exc:
            raise CredentialStoreError("The 'pass' command is not available.") from exc
        except subprocess.CalledProcessError as exc:
            command_text = " ".join(arguments)
            message = exc.stderr.strip() or exc.stdout.strip() or f"Pass command failed: {command_text}"
            raise CredentialStoreError(message) from exc

    def read_credential(self, key: str) -> str:
        normalized_key = key.strip().strip("/")
        return self.run_pass_command(["show", normalized_key]).stdout

    @staticmethod
    def parse_credential(raw_value: str) -> ParsedCredential:
        if raw_value == "":
            raise CredentialStoreError("Credential entry is empty.")

        normalized = raw_value.replace("\r\n", "\n")
        lines = normalized.split("\n")
        if lines and lines[-1] == "":
            lines = lines[:-1]

        value = lines[0] if lines else ""
        if not value:
            raise CredentialStoreError("Credential entry is missing a value line.")

        headers: dict[str, str] = {}
        notes_start = len(lines)

        for index, line in enumerate(lines[1:], start=1):
            if line == "":
                notes_start = index + 1
                break
            if ":" in line:
                header_name, header_value = line.split(":", 1)
                headers[header_name.strip()] = header_value.strip()
            else:
                raise CredentialStoreError(f"Malformed header line: {line}")
        else:
            notes_start = len(lines)

        notes = "\n".join(lines[notes_start:]).strip()
        return ParsedCredential(value=value, headers=headers, notes=notes)

    def get_credential(self, key: str) -> ParsedCredential:
        return self.parse_credential(self.read_credential(key))

    def last_updated_for(self, key: str) -> datetime | None:
        entry_file = self.entry_file_for(key)
        try:
            result = subprocess.run(
                ["git", "-C", str(self.repository), "log", "-1", "--format=%cI", "--", str(entry_file.relative_to(self.repository))],
                check=True,
                capture_output=True,
                text=True,
            )
        except (FileNotFoundError, ValueError, subprocess.CalledProcessError):
            return None
        return _parse_git_timestamp(result.stdout)

    def sync(self, *, exclude: list[str] | tuple[str, ...] = ()) -> dict[str, int]:
        excluded_paths = {
            normalized for path in exclude
            if (normalized := _normalize_credential_path(path))
        }
        configured_paths = {
            path for path in self.credential_paths()
            if not _is_excluded_path(path, excluded_paths)
        }
        existing = {credential.path: credential for credential in self.credentials.all()}

        created = 0
        updated = 0
        deleted = 0

        for missing_path in sorted(configured_paths - existing.keys()):
            credential = Credential(
                store=self,
                path=missing_path,
                last_updated_at=self.last_updated_for(missing_path),
            )
            credential.clean()
            credential.save()
            existing[credential.path] = credential
            created += 1

        for stale_path in sorted(existing.keys() - configured_paths):
            existing[stale_path].delete()
            deleted += 1

        for retained_path in sorted(configured_paths & existing.keys()):
            last_updated_at = self.last_updated_for(retained_path)
            if last_updated_at is None or existing[retained_path].last_updated_at == last_updated_at:
                continue
            existing[retained_path].last_updated_at = last_updated_at
            existing[retained_path].save(update_fields=("last_updated_at", "modified_at"))
            updated += 1

        return {"created": created, "updated": updated, "deleted": deleted}


class Credential(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    store = models.ForeignKey(CredentialStore, on_delete=models.CASCADE, related_name="credentials")
    path = models.CharField(max_length=1024)
    folder = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)
    last_updated_at = models.DateTimeField(null=True, blank=True)

    _cached_secret: ParsedCredential | None = None

    class Meta:
        ordering = ("path",)
        constraints = [
            models.UniqueConstraint(fields=("store", "path"), name="unique_credential_path_per_store"),
        ]

    def __str__(self) -> str:
        return f"{self.store.slug}:{self.path}"

    def clean(self) -> None:
        super().clean()
        normalized = _normalize_credential_path(self.path)
        if not normalized:
            raise ValidationError({"path": "Credential path cannot be empty."})
        self.path = normalized
        self.folder = _split_path(self.path)[0]

    def _load_secret(self) -> ParsedCredential:
        if self._cached_secret is None:
            self._cached_secret = self.store.get_credential(self.path)
        return self._cached_secret

    def clear_cached_secret(self) -> None:
        self._cached_secret = None

    @property
    def value(self) -> str:
        return self._load_secret().value

    @property
    def headers(self) -> dict[str, str]:
        return dict(self._load_secret().headers)

    @property
    def notes(self) -> str:
        return self._load_secret().notes

    @property
    def section(self) -> str:
        return _split_path(self.path)[1]

    @property
    def label(self) -> str:
        return _split_path(self.path)[2]

    @property
    def headers_json(self) -> str:
        return json.dumps(self.headers, indent=2, sort_keys=True)
