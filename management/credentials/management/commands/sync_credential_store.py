from django.core.management.base import BaseCommand, CommandError

from credentials.models import CredentialStore


class Command(BaseCommand):
    help = "Synchronize a credential store by slug."

    def add_arguments(self, parser) -> None:
        parser.add_argument("slug", help="Slug of the credential store to synchronize.")
        parser.add_argument(
            "--exclude",
            action="append",
            default=[],
            help="Exclude a credential path or subtree from sync. May be provided multiple times.",
        )

    def handle(self, *args, **options) -> None:
        slug = options["slug"]
        exclude = options["exclude"]

        try:
            store = CredentialStore.objects.get(slug=slug)
        except CredentialStore.DoesNotExist as exc:
            raise CommandError(f"Credential store '{slug}' does not exist.") from exc

        result = store.sync(exclude=exclude)
        excluded_suffix = ""
        if exclude:
            excluded_suffix = f" Excluded: {', '.join(exclude)}."
        self.stdout.write(
            self.style.SUCCESS(
                f"Synchronized '{slug}': "
                f"{result['created']} created, {result['updated']} updated, {result['deleted']} deleted."
                f"{excluded_suffix}"
            )
        )
