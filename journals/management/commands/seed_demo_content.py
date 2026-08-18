"""
Populate a clone-and-run PoC: two journals, volumes, issues, covers, articles.

Two journals rather than one on purpose — a single-journal seed lets
multi-journal assumptions (DOI namespacing, per-journal section order, per-journal
build views) go unexercised until they are expensive to fix.

Everything here is synthetic. No network calls: article metadata is filled in
locally and marked synced, so `manage.py build` works offline. Run
`sync_article_metadata` against real DOIs to exercise the real thing.
"""

import io
import random
from datetime import date, timedelta

from django.core.files.images import ImageFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from PIL import Image as PILImage
from PIL import ImageDraw
from wagtail.images import get_image_model
from wagtail.models import Collection, Page, Site

from journals.models import (
    Article,
    IssueArticle,
    IssuePage,
    JournalIndexPage,
    JournalPage,
    VolumeIndexPage,
    VolumePage,
)

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

JOURNALS = [
    {
        "key": "pmed",
        "slug": "plosmedicine",
        "display_name": "PLOS Medicine",
        "title": "PLOS Medicine",
        # (volume number, year, months) — v01 is deliberately irregular: three
        # issues, starting in October, exactly like the real thing.
        "volumes": [
            (1, 2004, [10, 11, 12]),
            (17, 2020, list(range(1, 13))),
            (18, 2021, [1, 2, 3]),
        ],
        "doi_start": 1003000,
    },
    {
        "key": "pbio",
        "slug": "plosbiology",
        "display_name": "PLOS Biology",
        "title": "PLOS Biology",
        "volumes": [(23, 2025, [1, 2])],
        "doi_start": 3002000,
    },
]

# Weighted so a demo issue looks like a real one. "Perspective" is not in the
# vocabulary — it is here so the "Other" bucket is visible in the built output
# rather than being a theoretical fallback.
ARTICLE_TYPES = (
    ["Research Article"] * 8
    + ["Editorial", "Review", "Correction", "Short Reports", "Pearls", "Perspective"]
)

TITLE_WORDS = [
    "malaria", "cohort", "vaccination", "antimicrobial resistance", "maternal health",
    "tuberculosis", "diabetes", "air pollution", "health policy", "randomised trial",
    "genomic surveillance", "primary care", "mental health", "nutrition",
]

SURNAMES = [
    "Okafor", "Nakamura", "Silva", "Ahmed", "Kowalski", "Ferreira", "Nguyen",
    "Haddad", "Lindqvist", "Mbeki", "Rossi", "Chen", "Patel", "Ivanova",
]


class Command(BaseCommand):
    help = "Create demo journals, volumes, issues, covers and articles."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing journal content first.",
        )
        parser.add_argument("--seed", type=int, default=20260817)

    @transaction.atomic
    def handle(self, *args, **options):
        self.random = random.Random(options["seed"])

        if options["reset"]:
            self.reset()

        index = self.get_or_create_index()
        for spec in JOURNALS:
            self.build_journal(index, spec)

        self.stdout.write(self.style.SUCCESS("Demo content created."))
        self.stdout.write(
            "Next: manage.py setup_journal_permissions && manage.py build"
        )

    def reset(self):
        # Site.root_page cascades, so a Site pointed at the index would be
        # deleted along with it. Park it on the tree root first.
        site = Site.objects.filter(is_default_site=True).first()
        if site and JournalIndexPage.objects.filter(pk=site.root_page_id).exists():
            site.root_page = Page.get_first_root_node()
            site.save()
        for page in JournalIndexPage.objects.all():
            page.delete()
        IssueArticle.objects.all().delete()
        Article.objects.all().delete()
        self.stdout.write("Existing journal content deleted.")

    def get_or_create_index(self):
        index = JournalIndexPage.objects.first()
        if index is None:
            root = Page.get_first_root_node()
            index = JournalIndexPage(title="PLOS Journals", slug="journals")
            root.add_child(instance=index)
            index.save_revision().publish()

        # The archive lives at the site root, so journals sit at
        # /plosmedicine/ rather than /journals/plosmedicine/ (design §4.6).
        site = Site.objects.filter(is_default_site=True).first()
        if site and site.root_page_id != index.pk:
            site.root_page = index
            site.save()
        return index

    def build_journal(self, index, spec):
        journal = JournalPage.objects.filter(journal_key=spec["key"]).first()
        if journal is None:
            journal = JournalPage(
                title=spec["title"],
                slug=spec["slug"],
                journal_key=spec["key"],
                display_name=spec["display_name"],
            )
            self.add_page(index, journal)

        volume_index = VolumeIndexPage.objects.child_of(journal).first()
        if volume_index is None:
            volume_index = VolumeIndexPage(title="Volumes", slug="volume")
            self.add_page(journal, volume_index)

        collection = self.get_or_create_collection(spec["display_name"])
        doi_counter = spec["doi_start"]
        featured = []  # candidates for the homepage, newest issue last

        for number, year, months in spec["volumes"]:
            volume = VolumePage.objects.filter(number=number).child_of(volume_index).first()
            if volume is None:
                volume = VolumePage(title=str(year), slug=str(year), number=number)
                self.add_page(volume_index, volume)

            for month in months:
                slug = MONTHS[month - 1].lower()
                if IssuePage.objects.child_of(volume).filter(slug=slug).exists():
                    continue
                issue = IssuePage(
                    title=MONTHS[month - 1],
                    slug=slug,
                    number=month,
                    cover_caption=(
                        "<p>The cover shows an illustration commissioned for this "
                        "issue. Replace with the real caption when covers are "
                        "backfilled.</p>"
                    ),
                    cover_credit="Image credit: placeholder, generated for the PoC.",
                )
                issue.cover_image = self.make_cover(
                    collection, spec["display_name"], year, MONTHS[month - 1]
                )
                self.add_page(volume, issue)

                count = self.random.randint(6, 14)
                featured = []
                for _ in range(count):
                    doi_counter += 1
                    article = self.make_article(spec["key"], doi_counter, year, month)
                    IssueArticle.objects.get_or_create(page=issue, article=article)
                    featured.append(article)

                self.stdout.write(
                    f"  {spec['key']} v{number:02d} {MONTHS[month - 1]}: "
                    f"{count} articles, {issue.doi}"
                )

        self.seed_homepage(journal, collection, featured)

    def add_page(self, parent, page):
        """Create and publish, deriving DOIs the way the editor form would."""
        page.parent_page_hint = parent
        page.clean()
        parent.add_child(instance=page)
        page.save_revision().publish()
        return page

    def get_or_create_collection(self, name):
        root = Collection.get_first_root_node()
        collection = root.get_children().filter(name=name).first()
        return collection or root.add_child(name=name)

    def make_cover(self, collection, journal_name, year, month):
        """
        A generated placeholder, because covers are Wagtail-owned (decision 4)
        and there is no remote fallback to hide a missing one. The real backfill
        derives the figure URL from the issue DOI — see §7.4.
        """
        return self.make_image(
            collection,
            f"{journal_name} {month} {year} cover",
            f"cover-{journal_name.lower().replace(' ', '-')}-{year}-{month.lower()}.png",
            (480, 600),
            [journal_name, f"{month} {year}", "PLACEHOLDER COVER"],
        )

    def make_image(self, collection, title, filename, size, lines):
        width, height = size
        hue = self.random.randint(0, 255)
        image = PILImage.new("RGB", (width, height), (hue, (hue * 3) % 255, 180))
        draw = ImageDraw.Draw(image)
        draw.rectangle([20, 20, width - 20, height - 20], outline=(255, 255, 255), width=6)
        for offset, line in enumerate(lines):
            draw.text((40, 60 + offset * 30), line, fill=(255, 255, 255))

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)

        return get_image_model().objects.create(
            title=title,
            file=ImageFile(buffer, name=filename),
            collection=collection,
        )

    def seed_homepage(self, journal, collection, articles):
        """
        Fill the journal homepage: a hero, a billboard and three tiers, mixing
        article slots with links out, because both kinds have to be exercised
        for the demo to say anything useful about the editing experience.

        Skipped if the homepage already has content, so re-running the seed
        never overwrites something someone has been editing.
        """
        if len(journal.hero) or len(journal.tier_one):
            return
        if not articles:
            return

        def card(article):
            return (
                "article",
                {
                    "article": article,
                    "teaser": (
                        f"{article.author_list[0].split()[-1] if article.author_list else 'The authors'} "
                        "and colleagues report findings from this study."
                    ),
                },
            )

        hero_article = articles[0]
        journal.hero = [
            (
                "article",
                {
                    "article": hero_article,
                    "headline": "The overlay text for the hero image",
                    "image": self.make_hero_image(collection, journal.display_name),
                },
            )
        ]
        journal.billboard = [
            (
                "link",
                {
                    "url": "https://collections.plos.org/collection/covid-19/",
                    "headline": "Read the latest COVID-19 research",
                    "teaser": (
                        "This Collection highlights content published across the "
                        "PLOS journals relating to the COVID-19 pandemic."
                    ),
                },
            )
        ]
        journal.tier_one = [card(article) for article in articles[1:5]]
        journal.tier_two = [
            (
                "link",
                {
                    "url": "https://protocols.io/",
                    "headline": "Submit your Lab and Study Protocols to PLOS",
                    "kicker": "Announcement",
                },
            )
        ] + [card(article) for article in articles[5:7]]
        journal.tier_three = [card(article) for article in articles[7:10]]

        journal.clean()
        journal.save()
        journal.save_revision().publish()
        self.stdout.write(f"  {journal.journal_key}: homepage seeded")

    def make_hero_image(self, collection, journal_name):
        return self.make_image(
            collection,
            f"{journal_name} hero",
            f"hero-{journal_name.lower().replace(' ', '-')}.png",
            (1200, 480),
            [journal_name, "PLACEHOLDER HERO IMAGE"],
        )

    def make_article(self, journal_key, number, year, month):
        doi = f"10.1371/journal.{journal_key}.{number}"
        article_type = self.random.choice(ARTICLE_TYPES)
        published_at = date(year, month, 1) + timedelta(days=self.random.randint(0, 27))
        authors = ", ".join(
            f"{self.random.choice('ABCDEFGHJKLMNPRSTUVW')} {surname}"
            for surname in self.random.sample(SURNAMES, self.random.randint(2, 5))
        )
        title = (
            f"{article_type} on {self.random.choice(TITLE_WORDS)} and "
            f"{self.random.choice(TITLE_WORDS)}: a {year} study"
        )
        article, _ = Article.objects.get_or_create(doi=doi)
        article.title = title
        article.authors = authors
        article.article_type = article_type
        article.published_at = published_at
        # Marked synced so the build gate passes offline. Real runs get this
        # from sync_article_metadata.
        article.metadata_synced_at = timezone.now()
        article.save()
        return article
