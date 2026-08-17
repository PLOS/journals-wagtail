"""
Delete Article snippets that no issue references (design §7.6).

`IssueArticle.article` is PROTECT, so an article in use can never be deleted by
accident — the flip side is that orphans accumulate as issues are deleted.
"""

from django.core.management.base import BaseCommand

from journals.models import Article


class Command(BaseCommand):
    help = "Delete Article snippets that are not attached to any issue."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List what would be deleted without deleting it.",
        )

    def handle(self, *args, **options):
        orphans = Article.objects.filter(issue_entries__isnull=True)
        count = orphans.count()

        if options["dry_run"]:
            for article in orphans[:100]:
                self.stdout.write(f"  would delete {article.doi}")
            self.stdout.write(f"{count} orphan article(s).")
            return

        orphans.delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {count} orphan article(s)."))
