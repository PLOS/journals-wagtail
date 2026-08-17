"""
List article types seen in the data that the YAML vocabulary doesn't know about
(design §4.3).

"Other" is a symptom, not a resting place. This output is exactly what editorial
needs in order to decide placement for a new type.
"""

from collections import defaultdict

from django.core.management.base import BaseCommand

from journals.article_types import by_name, vocabulary
from journals.models import Article, JournalPage


class Command(BaseCommand):
    help = "Report article_type values that are not in the article-type vocabulary."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fail",
            action="store_true",
            help="Exit non-zero if any unknown type is found. Off by default: a "
            "hard failure on an upstream vocabulary change makes the build "
            "hostage to someone else's release schedule.",
        )

    def handle(self, *args, **options):
        known_globally = {article_type.name for article_type in vocabulary().values()}

        # A type can be in the vocabulary but absent from a journal's order, in
        # which case its articles land in "Other" for that journal only.
        per_journal_known = {
            journal.journal_key: set(by_name(journal.journal_key))
            for journal in JournalPage.objects.all()
        }

        counts = defaultdict(list)
        for article in Article.objects.exclude(article_type="").iterator():
            counts[article.article_type].append(article.doi)

        unknown = {
            name: dois for name, dois in counts.items() if name not in known_globally
        }

        if unknown:
            self.stdout.write(self.style.WARNING("Not in the vocabulary at all:"))
            for name, dois in sorted(unknown.items(), key=lambda kv: -len(kv[1])):
                self.stdout.write(
                    f"  {name!r}: {len(dois)} article(s), e.g. {', '.join(dois[:3])}"
                )
        else:
            self.stdout.write(self.style.SUCCESS("Every article type is in the vocabulary."))

        for journal_key, known in sorted(per_journal_known.items()):
            unordered = {
                name: dois
                for name, dois in counts.items()
                if name in known_globally and name not in known
            }
            if unordered:
                self.stdout.write(
                    self.style.WARNING(
                        f"In the vocabulary but missing from {journal_key}'s order "
                        "(these fall into 'Other'):"
                    )
                )
                for name, dois in sorted(unordered.items(), key=lambda kv: -len(kv[1])):
                    self.stdout.write(f"  {name!r}: {len(dois)} article(s)")

        if unknown and options["fail"]:
            raise SystemExit(1)
