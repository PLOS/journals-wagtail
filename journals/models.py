"""
Journal → Volume → Issue, as a Wagtail page tree (design §4).

The page tree replaces the old Article Admin's volume/issue CRUD screens: tree
position replaces the "which volume?" dropdown, page titles replace the `name`
fields, and DOIs are derived rather than typed.
"""

from datetime import date

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from modelcluster.fields import ParentalKey
from wagtail.admin.panels import FieldPanel, InlinePanel, MultiFieldPanel
from wagtail.contrib.settings.models import BaseSiteSetting, register_setting
from wagtail.fields import RichTextField, StreamField
from wagtail.models import Page
from wagtail.search import index

from journals.article_types import OTHER, by_name, section_order
from journals.blocks import CardStreamBlock
from journals.forms import TreeAwarePageForm

DOI_PREFIX = "10.1371"

volume_doi_validator = RegexValidator(
    r"^10\.1371/volume\.[a-z]+\.v\d{2}$",
    "Expected e.g. 10.1371/volume.pmed.v18",
)
issue_doi_validator = RegexValidator(
    r"^10\.1371/issue\.[a-z]+\.v\d{2}\.i\d{2}$",
    "Expected e.g. 10.1371/issue.pmed.v18.i02",
)


def normalise_doi(doi: str) -> str:
    """
    Lowercase and strip a DOI at the boundary.

    SQLite and PostgreSQL disagree about case sensitivity in string comparison,
    so a `unique=True` DOI can behave differently in dev and production unless
    it is normalised on the way in (design §3.1).
    """
    return (doi or "").strip().lower()


class TreePositionMixin:
    """Parent/ancestor access that also works before the page is in the tree."""

    def get_parent_page(self):
        hint = getattr(self, "parent_page_hint", None)
        if hint is not None:
            return hint
        if self.path:
            parent = self.get_parent()
            return parent.specific if parent else None
        return None

    @property
    def journal(self):
        """The JournalPage this page belongs to, or None if not yet placed."""
        parent = self.get_parent_page()
        if parent is None:
            return None
        if isinstance(parent, JournalPage):
            return parent
        ancestor = parent.get_ancestors(inclusive=True).type(JournalPage).last()
        return ancestor.specific if ancestor else None


class JournalIndexPage(Page):
    """Site root. Holds every journal as a child (design §4.6)."""

    parent_page_types = ["wagtailcore.Page"]
    subpage_types = ["journals.JournalPage"]
    max_count = 1

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        context["journals"] = JournalPage.objects.child_of(self).live().order_by("title")
        return context


class JournalPage(Page):
    """
    One journal: the DOI namespace, the root of the journal's content — and the
    journal homepage at `/<slug>/`, which is what the homepage CMS edits.

    The homepage lives on this page rather than on a separate dated record.
    A homepage is not a new document each time it changes, it is a
    new *version* of the same document at the same URL, which is exactly what a
    Wagtail page revision is. That buys the whole of the legacy tool's homepage
    list for free and correctly: the dated entries are revisions, the coloured
    dot is the draft/live status, "publish" is publish, a future date is
    `go_live_at` scheduling, and the side-by-side preview is the preview panel.
    """

    journal_key = models.SlugField(
        max_length=16,
        unique=True,
        help_text="DOI component, e.g. 'pmed' for PLOS Medicine.",
    )
    display_name = models.CharField(max_length=128)  # "PLOS Medicine"

    # Region sizes match the published layout. They are caps, not quotas: a
    # half-filled tier is a normal state for a homepage mid-edit, and the
    # template lays out whatever is there.
    hero = StreamField(
        CardStreamBlock(),
        blank=True,
        max_num=1,
        help_text="The full-width slot at the top. Needs an image; the headline "
        "is the text laid over it.",
    )
    billboard = StreamField(
        CardStreamBlock(),
        blank=True,
        max_num=1,
        help_text="The banner under the hero. The headline and teaser are the "
        "banner's heading and description.",
    )
    tier_one = StreamField(
        CardStreamBlock(), blank=True, max_num=4, verbose_name="Tier 1"
    )
    tier_two = StreamField(
        CardStreamBlock(), blank=True, max_num=3, verbose_name="Tier 2"
    )
    tier_three = StreamField(
        CardStreamBlock(), blank=True, max_num=6, verbose_name="Tier 3"
    )

    parent_page_types = ["journals.JournalIndexPage"]
    subpage_types = ["journals.VolumeIndexPage"]

    content_panels = Page.content_panels + [
        MultiFieldPanel(
            [FieldPanel("journal_key"), FieldPanel("display_name")],
            heading="Journal",
        ),
        FieldPanel("hero"),
        FieldPanel("billboard"),
        MultiFieldPanel(
            [
                FieldPanel("tier_one"),
                FieldPanel("tier_two"),
                FieldPanel("tier_three"),
            ],
            heading="Tiers",
        ),
    ]

    def clean(self):
        super().clean()
        self.journal_key = (self.journal_key or "").strip().lower()
        # The hero is the one region that is nothing without its image: it is
        # rendered as a picture with text over it, so an imageless hero is a
        # blank band at the top of the journal's front door.
        for block in self.hero:
            if not block.value.get("image"):
                raise ValidationError({"hero": "The hero needs an image."})

    @property
    def volume_index(self):
        return VolumeIndexPage.objects.child_of(self).live().first()

    def homepage_regions(self):
        """(label, cards) for every filled region, in the order they appear."""
        regions = [
            ("Hero", self.hero),
            ("Billboard", self.billboard),
            ("Tier 1", self.tier_one),
            ("Tier 2", self.tier_two),
            ("Tier 3", self.tier_three),
        ]
        return [(label, stream) for label, stream in regions if len(stream)]

    def homepage_cards(self):
        """Every filled slot on the homepage, whatever region it sits in."""
        return [block.value for _, stream in self.homepage_regions() for block in stream]

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        context["volume_index"] = self.volume_index
        context["current_issue"] = IssuePage.current_for(self)
        return context


class VolumeIndexPage(TreePositionMixin, Page):
    """The /volume archive landing page."""

    max_count_per_parent = 1
    parent_page_types = ["journals.JournalPage"]
    subpage_types = ["journals.VolumePage"]
    template = "journals/volume_index_page.html"

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        context["volumes"] = (
            VolumePage.objects.child_of(self).live().order_by("-number")
        )
        context["journal"] = self.journal
        context["current_issue"] = IssuePage.current_for(self.journal)
        return context


class VolumePage(TreePositionMixin, Page):
    number = models.PositiveSmallIntegerField(
        help_text="Volume number, e.g. 18. Drives the DOI and ordering.",
    )
    doi = models.CharField(
        max_length=64,
        unique=True,
        blank=True,
        validators=[volume_doi_validator],
        help_text="Left blank, this is generated from the journal key and volume number.",
    )

    parent_page_types = ["journals.VolumeIndexPage"]
    subpage_types = ["journals.IssuePage"]
    base_form_class = TreeAwarePageForm

    content_panels = Page.content_panels + [
        FieldPanel("number"),
        FieldPanel("doi"),
    ]

    def clean(self):
        super().clean()
        journal = self.journal
        self.doi = normalise_doi(self.doi)
        if not self.doi and journal is not None:
            self.doi = f"{DOI_PREFIX}/volume.{journal.journal_key}.v{self.number:02d}"
        if journal is not None and self.doi:
            key = journal.journal_key
            if f".{key}." not in self.doi:
                raise ValidationError(
                    {"doi": f"DOI does not belong to journal '{key}'."}
                )

    @property
    def issues(self):
        return IssuePage.objects.child_of(self).live().order_by("number")

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        context["journal"] = self.journal
        context["issues"] = self.issues.specific()
        return context


class IssuePage(TreePositionMixin, Page):
    number = models.PositiveSmallIntegerField(help_text="Issue number within the volume, 1–12.")
    doi = models.CharField(
        max_length=64,
        unique=True,
        blank=True,
        validators=[issue_doi_validator],
        help_text="Left blank, this is generated from the volume DOI and issue number.",
    )
    image_article_doi = models.CharField(
        max_length=64,
        blank=True,
        help_text="Left blank, derived from the issue DOI (issue. → image.). "
        "Used as the cover's link target, not as the image source.",
    )
    # null=True so deleting an image in the image library never raises
    # ProtectedError; blank=False so the editor form requires one. Publish is
    # additionally gated in clean() and by the before_publish_page hook.
    cover_image = models.ForeignKey(
        "wagtailimages.Image",
        null=True,
        blank=False,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Issue cover. Required to publish.",
    )
    cover_alt_text = models.CharField(
        max_length=255,
        blank=True,
        help_text="Leave blank to fall back to the image's own default alt text.",
    )
    cover_caption = RichTextField(
        blank=True,
        features=["bold", "italic", "link"],
        help_text="The 'About this image' text shown on the archive and issue pages.",
    )
    cover_credit = models.CharField(max_length=255, blank=True)
    bulk_dois = models.TextField(
        blank=True,
        verbose_name="Add articles by DOI",
        help_text="Paste article DOIs, one per line. They are added to the list "
        "below on save and this box is cleared. Order does not matter — the "
        "table of contents is grouped by article type and sorted by date.",
    )

    parent_page_types = ["journals.VolumePage"]
    subpage_types = []
    base_form_class = TreeAwarePageForm

    content_panels = Page.content_panels + [
        MultiFieldPanel(
            [FieldPanel("number"), FieldPanel("doi")],
            heading="Identity",
        ),
        MultiFieldPanel(
            [
                FieldPanel("image_article_doi"),
                FieldPanel("cover_image"),
                FieldPanel("cover_alt_text"),
                FieldPanel("cover_caption"),
                FieldPanel("cover_credit"),
            ],
            heading="Cover",
        ),
        FieldPanel("bulk_dois"),
        InlinePanel("issue_articles", label="Article"),
    ]

    class Meta:
        ordering = ["number"]

    @property
    def volume(self):
        return self.get_parent_page()

    def clean(self):
        super().clean()
        volume = self.volume
        self.doi = normalise_doi(self.doi)
        self.image_article_doi = normalise_doi(self.image_article_doi)
        if volume is not None:
            journal = volume.journal
            if not self.doi and journal is not None:
                self.doi = (
                    f"{DOI_PREFIX}/issue.{journal.journal_key}"
                    f".v{volume.number:02d}.i{self.number:02d}"
                )
        if not self.image_article_doi and self.doi:
            self.image_article_doi = self.doi.replace("/issue.", "/image.")
        # The DOI must agree with where the page actually sits in the tree.
        if volume is not None and self.doi:
            expected_volume = f".v{volume.number:02d}."
            if expected_volume not in self.doi:
                raise ValidationError(
                    {"doi": f"DOI does not match parent volume {volume.number}."}
                )
            journal = volume.journal
            if journal is not None and f".{journal.journal_key}." not in self.doi:
                raise ValidationError(
                    {"doi": f"DOI does not belong to journal '{journal.journal_key}'."}
                )
        if self.live and not self.cover_image_id:
            raise ValidationError({"cover_image": "A cover image is required to publish."})
        self.absorb_bulk_dois()

    def absorb_bulk_dois(self):
        """
        Turn a pasted block of DOIs into `IssueArticle` rows (design §4.4).

        Pasting twenty DOIs beats clicking "Add article" twenty times, and it is
        the one place the old admin's UX beat idiomatic Wagtail. Paste order
        carries no meaning (TOC order is derived), so this is set union, not a
        sequence merge.
        """
        if not self.bulk_dois:
            return
        dois = []
        for line in self.bulk_dois.splitlines():
            doi = normalise_doi(line)
            if doi and doi not in dois:
                dois.append(doi)

        existing = {normalise_doi(row.article.doi) for row in self.issue_articles.all()}
        for doi in dois:
            if doi in existing:
                continue
            article, _ = Article.objects.get_or_create(doi=doi)
            self.issue_articles.add(IssueArticle(article=article))
            existing.add(doi)
        self.bulk_dois = ""

    @property
    def cover_alt(self):
        return self.cover_alt_text or (
            self.cover_image.default_alt_text if self.cover_image else ""
        )

    @property
    def image_article_url(self):
        """Cover links through to the image article, as it does today."""
        journal = self.volume.journal if self.volume else None
        key = journal.journal_key if journal else ""
        return f"https://journals.plos.org/{key}/article?id={self.image_article_doi}"

    @property
    def articles(self):
        return Article.objects.filter(issue_entries__page=self)

    def toc_sections(self):
        """
        TOC order is fully derived (decision 8): sections in the journal's
        configured order, articles chronological within each section. Nothing
        reads editor input.
        """
        journal = self.volume.journal if self.volume else None
        journal_key = journal.journal_key if journal else ""
        lookup = by_name(journal_key)
        rows = self.issue_articles.select_related("article")
        articles = sorted(
            (row.article for row in rows),
            # DOI is a deterministic tiebreak: same-day publication within an
            # issue is common, and without it the build output is unstable
            # between runs.
            key=lambda a: (a.published_at or date.max, a.doi),
        )
        grouped = {}
        for article in articles:
            grouped.setdefault(lookup.get(article.article_type, OTHER), []).append(article)
        # section_order() ends with OTHER, so unknown types render last, never vanish.
        return [
            (article_type, grouped[article_type])
            for article_type in section_order(journal_key)
            if article_type in grouped
        ]

    def get_context(self, request, *args, **kwargs):
        context = super().get_context(request, *args, **kwargs)
        volume = self.volume
        context["volume"] = volume
        context["journal"] = volume.journal if volume else None
        context["toc_sections"] = self.toc_sections()
        return context

    @classmethod
    def current_for(cls, journal):
        """
        The issue to feature on the archive page.

        The explicit pointer wins; blank means "the most recent published
        issue", which removes the old admin's monthly "set as current" click
        and the silent, reader-visible failure when someone forgets it.
        """
        if journal is None:
            return None
        site = journal.get_site()
        if site is not None:
            setting = JournalSettings.for_site(site)
            current = setting.current_issue
            # One Wagtail Site serves every journal (§4.6), so a site-wide
            # pointer has to be checked against the journal asking for it.
            if (
                current is not None
                and current.live
                and current.get_ancestors().filter(pk=journal.pk).exists()
            ):
                return current
        issues = cls.objects.descendant_of(journal).live().specific()
        return max(
            issues,
            key=lambda issue: (issue.volume.number, issue.number),
            default=None,
        )


class IssueArticle(models.Model):
    """
    Issue↔article membership. Deliberately NOT an Orderable (decision 8): TOC
    order is derived from article_type and publication date, so a drag handle
    here would imply a control that has no effect. Membership only.
    """

    page = ParentalKey(IssuePage, on_delete=models.CASCADE, related_name="issue_articles")
    article = models.ForeignKey(
        "journals.Article", on_delete=models.PROTECT, related_name="issue_entries"
    )

    panels = [FieldPanel("article")]

    class Meta:
        unique_together = [("page", "article")]

    def __str__(self):
        return str(self.article)


class Article(index.Indexed, models.Model):
    """A DOI plus metadata cached from the article repository. Never edited by hand."""

    doi = models.CharField(max_length=64, unique=True, db_index=True)

    title = models.TextField(blank=True)
    authors = models.TextField(blank=True, help_text="Display string, comma separated.")
    article_type = models.CharField(max_length=64, blank=True)  # "Research Article", …
    published_at = models.DateField(null=True, blank=True)
    metadata_synced_at = models.DateTimeField(null=True, blank=True)

    panels = [FieldPanel("doi")]

    search_fields = [
        index.SearchField("doi"),
        index.SearchField("title"),
        index.SearchField("authors"),
        index.FilterField("article_type"),
    ]

    class Meta:
        ordering = ["doi"]

    def __str__(self):
        return self.title or self.doi

    def clean(self):
        super().clean()
        self.doi = normalise_doi(self.doi)

    def save(self, *args, **kwargs):
        self.doi = normalise_doi(self.doi)
        return super().save(*args, **kwargs)

    @property
    def journal_key(self):
        """'10.1371/journal.pcbi.1012387' → 'pcbi'."""
        try:
            return self.doi.rsplit("/", 1)[-1].split(".")[1]
        except IndexError:
            return ""

    @property
    def author_list(self):
        return [author.strip() for author in self.authors.split(",") if author.strip()]

    @property
    def is_synced(self):
        return bool(
            self.metadata_synced_at
            and self.title
            and self.article_type
            and self.published_at
        )

    def sync_status(self):
        """Column for the snippet listing — the whole point of that screen."""
        if self.is_synced:
            return f"Synced {self.metadata_synced_at:%Y-%m-%d}"
        if self.metadata_synced_at:
            return "Incomplete"
        return "Not synced"

    sync_status.short_description = "Metadata"

    @property
    def url(self):
        # The search API returns no URL, so we construct one. The DOI carries
        # the journal's DOI key ('pcbi'), which is NOT the URL slug
        # ('ploscompbiol') — resolve via JournalPage, which holds both. Single
        # method, so the article pipeline's eventual URL scheme change (§7.7) is
        # a one-line edit here.
        journal = JournalPage.objects.filter(journal_key=self.journal_key).first()
        prefix = f"/{journal.slug}" if journal else ""
        return f"{prefix}/article?id={self.doi}"


@register_setting
class JournalSettings(BaseSiteSetting):
    current_issue = models.ForeignKey(
        "journals.IssuePage",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Leave blank to use the most recent published issue.",
    )
    panels = [FieldPanel("current_issue")]

    class Meta:
        verbose_name = "Journal settings"
