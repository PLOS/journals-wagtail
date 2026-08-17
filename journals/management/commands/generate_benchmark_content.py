"""
Synthetic content at 16-journal scale, for the build benchmark (design §7.5).

django-bakery has no incremental build, so every deploy is a full rebuild:
~3,000 issue pages and ~400,000 article rows once every journal is onboarded.
That assumption needs a measured number rather than a guess, and the number is
much cheaper to get now than after content import.

    manage.py generate_benchmark_content --journals 16 --volumes 12 --issues 12
    time manage.py build

One cover image is shared by every generated issue: renditions are the thing
being timed, not upload throughput.
"""

import io
import time

from django.core.files.images import ImageFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from PIL import Image as PILImage
from wagtail.images import get_image_model

from journals.management.commands.seed_demo_content import MONTHS
from journals.models import (
    Article,
    IssueArticle,
    IssuePage,
    JournalIndexPage,
    JournalPage,
    VolumeIndexPage,
    VolumePage,
)


class Command(BaseCommand):
    help = "Generate synthetic journals/volumes/issues/articles for build benchmarking."

    def add_arguments(self, parser):
        parser.add_argument("--journals", type=int, default=16)
        parser.add_argument("--volumes", type=int, default=12, help="Volumes per journal.")
        parser.add_argument("--issues", type=int, default=12, help="Issues per volume.")
        parser.add_argument("--articles", type=int, default=20, help="Articles per issue.")
        parser.add_argument(
            "--prefix",
            default="bm",
            help="Journal key prefix, so benchmark content is easy to tell apart "
            "and easy to delete.",
        )

    def handle(self, *args, **options):
        started = time.monotonic()
        index = JournalIndexPage.objects.first()
        if index is None:
            raise SystemExit("Run seed_demo_content first — no JournalIndexPage exists.")

        cover = self.make_shared_cover()
        totals = {"issues": 0, "articles": 0}

        for journal_number in range(options["journals"]):
            self.build_journal(index, journal_number, cover, options, totals)
            self.stdout.write(
                f"  journal {journal_number + 1}/{options['journals']} done "
                f"({totals['issues']} issues, {totals['articles']} articles)"
            )

        elapsed = time.monotonic() - started
        self.stdout.write(
            self.style.SUCCESS(
                f"Generated {totals['issues']} issues and {totals['articles']} "
                f"articles in {elapsed:.1f}s. Now time `manage.py build`."
            )
        )

    @transaction.atomic
    def build_journal(self, index, journal_number, cover, options, totals):
        # DOI journal keys are letters only, so number the benchmark journals in
        # letters too rather than tripping the DOI validators.
        suffix = chr(ord("a") + journal_number // 26) + chr(ord("a") + journal_number % 26)
        key = f"{options['prefix']}{suffix}"
        journal = JournalPage.objects.filter(journal_key=key).first()
        if journal is None:
            journal = JournalPage(
                title=f"Benchmark Journal {journal_number:02d}",
                slug=f"benchmark-{journal_number:02d}",
                journal_key=key,
                display_name=f"Benchmark Journal {journal_number:02d}",
            )
            self.publish(index, journal)

        volume_index = VolumeIndexPage.objects.child_of(journal).first()
        if volume_index is None:
            volume_index = VolumeIndexPage(title="Volumes", slug="volume")
            self.publish(journal, volume_index)

        for volume_number in range(1, options["volumes"] + 1):
            volume = VolumePage(
                title=str(2000 + volume_number),
                slug=str(2000 + volume_number),
                number=volume_number,
            )
            self.publish(volume_index, volume)

            for issue_number in range(1, options["issues"] + 1):
                issue = IssuePage(
                    title=MONTHS[(issue_number - 1) % 12],
                    slug=f"issue-{issue_number:02d}",
                    number=issue_number,
                    cover_image=cover,
                )
                self.publish(volume, issue)
                totals["issues"] += 1
                totals["articles"] += self.attach_articles(
                    issue, key, volume_number, issue_number, options["articles"]
                )

    def attach_articles(self, issue, key, volume_number, issue_number, count):
        now = timezone.now()
        articles = [
            Article(
                doi=f"10.1371/journal.{key}.{volume_number:02d}{issue_number:02d}{n:04d}",
                title=f"Benchmark article {n} for {key} v{volume_number} i{issue_number}",
                authors="A Author, B Author, C Author",
                article_type="Research Article" if n % 4 else "Editorial",
                published_at=now.date(),
                metadata_synced_at=now,
            )
            for n in range(count)
        ]
        Article.objects.bulk_create(articles, ignore_conflicts=True)
        created = Article.objects.filter(doi__in=[a.doi for a in articles])
        IssueArticle.objects.bulk_create(
            [IssueArticle(page=issue, article=article) for article in created],
            ignore_conflicts=True,
        )
        return len(articles)

    def publish(self, parent, page):
        page.parent_page_hint = parent
        page.clean()
        parent.add_child(instance=page)
        page.save_revision().publish()
        return page

    def make_shared_cover(self):
        image_model = get_image_model()
        existing = image_model.objects.filter(title="Benchmark cover").first()
        if existing:
            return existing
        buffer = io.BytesIO()
        PILImage.new("RGB", (480, 600), (90, 110, 160)).save(buffer, format="PNG")
        buffer.seek(0)
        return image_model.objects.create(
            title="Benchmark cover",
            file=ImageFile(buffer, name="benchmark-cover.png"),
        )
