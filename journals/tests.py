import json
import tempfile
from datetime import date
from pathlib import Path
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from wagtail.images import get_image_model
from wagtail.images.tests.utils import get_test_image_file
from wagtail.models import Page, Site

from journals import article_types
from journals.bakery_views import (
    CurrentIssueRedirectView,
    IssueDOIRedirectView,
    IssueMapView,
)
from journals.models import (
    Article,
    IssueArticle,
    IssuePage,
    JournalIndexPage,
    JournalPage,
    JournalSettings,
    VolumeIndexPage,
    VolumePage,
)


def publish(parent, page):
    """Create a page the way the editor form does: derive, then publish."""
    page.parent_page_hint = parent
    page.clean()
    parent.add_child(instance=page)
    page.save_revision().publish()
    page.refresh_from_db()
    return page


class JournalTreeMixin:
    def build_tree(self, key="pmed", slug="plosmedicine", volume_number=18, year=2021):
        root = Page.get_first_root_node()
        index = JournalIndexPage.objects.first()
        if index is None:
            index = publish(root, JournalIndexPage(title="PLOS Journals", slug="journals"))
            site = Site.objects.get(is_default_site=True)
            site.root_page = index
            site.save()
        journal = publish(
            index,
            JournalPage(
                title=slug,
                slug=slug,
                journal_key=key,
                display_name=f"PLOS {key}",
            ),
        )
        volume_index = publish(journal, VolumeIndexPage(title="Volumes", slug="volume"))
        volume = publish(
            volume_index, VolumePage(title=str(year), slug=str(year), number=volume_number)
        )
        return journal, volume_index, volume

    def make_issue(self, volume, number=2, title="February", with_cover=True):
        issue = IssuePage(title=title, slug=title.lower(), number=number)
        if with_cover:
            issue.cover_image = get_image_model().objects.create(
                title=f"{title} cover", file=get_test_image_file()
            )
        return publish(volume, issue)

    def attach(self, issue, doi, article_type, published_at):
        article, _ = Article.objects.get_or_create(doi=doi)
        article.article_type = article_type
        article.published_at = published_at
        article.title = f"Title for {doi}"
        article.save()
        IssueArticle.objects.create(page=issue, article=article)
        return article


class ArticleTypeConfigTests(TestCase):
    def test_other_is_always_last(self):
        self.assertEqual(article_types.section_order("pmed")[-1], article_types.OTHER)
        self.assertEqual(article_types.section_order("nosuchjournal")[-1], article_types.OTHER)

    def test_per_journal_override_takes_precedence(self):
        default = [t.key for t in article_types.section_order("nosuchjournal")]
        pmed = [t.key for t in article_types.section_order("pmed")]
        self.assertNotEqual(default, pmed)
        # The override puts research articles second; the default does not.
        self.assertEqual(pmed[1], "research_article")

    def test_unknown_type_maps_to_other(self):
        lookup = article_types.by_name("pmed")
        self.assertEqual(lookup.get("Perspective", article_types.OTHER), article_types.OTHER)

    def test_anchor_matches_legacy_deep_links(self):
        lookup = article_types.by_name("pmed")
        self.assertEqual(lookup["Research Article"].anchor, "Research_Article")
        self.assertEqual(lookup["Short Reports"].anchor, "Short_Reports")


class DOIDerivationTests(JournalTreeMixin, TestCase):
    def test_volume_doi_is_derived_from_journal_and_number(self):
        _, _, volume = self.build_tree()
        self.assertEqual(volume.doi, "10.1371/volume.pmed.v18")

    def test_issue_doi_and_image_doi_are_derived(self):
        _, _, volume = self.build_tree()
        issue = self.make_issue(volume, number=2)
        self.assertEqual(issue.doi, "10.1371/issue.pmed.v18.i02")
        self.assertEqual(issue.image_article_doi, "10.1371/image.pmed.v18.i02")

    def test_explicit_doi_is_kept_and_normalised(self):
        _, _, volume = self.build_tree(volume_number=1, year=2004)
        issue = IssuePage(
            title="October",
            slug="october",
            number=10,
            doi="  10.1371/Issue.PMED.v01.i10  ",
            cover_image=get_image_model().objects.create(
                title="October cover", file=get_test_image_file()
            ),
        )
        issue.parent_page_hint = volume
        issue.clean()
        self.assertEqual(issue.doi, "10.1371/issue.pmed.v01.i10")

    def test_doi_must_match_the_parent_volume(self):
        _, _, volume = self.build_tree(volume_number=18)
        issue = IssuePage(
            title="February", slug="february", number=2, doi="10.1371/issue.pmed.v17.i02"
        )
        issue.parent_page_hint = volume
        with self.assertRaises(ValidationError) as caught:
            issue.clean()
        self.assertIn("doi", caught.exception.error_dict)

    def test_doi_must_belong_to_the_journal(self):
        _, volume_index, _ = self.build_tree()
        volume = VolumePage(title="2021", slug="2021-alt", number=19, doi="10.1371/volume.pbio.v19")
        volume.parent_page_hint = volume_index
        with self.assertRaises(ValidationError):
            volume.clean()


class TOCOrderingTests(JournalTreeMixin, TestCase):
    def setUp(self):
        _, _, self.volume = self.build_tree()
        self.issue = self.make_issue(self.volume)

    def test_sections_follow_the_journals_configured_order(self):
        self.attach(self.issue, "10.1371/journal.pmed.1000002", "Research Article", date(2021, 2, 3))
        self.attach(self.issue, "10.1371/journal.pmed.1000001", "Editorial", date(2021, 2, 4))
        self.attach(self.issue, "10.1371/journal.pmed.1000003", "Correction", date(2021, 2, 1))

        sections = [article_type.plural for article_type, _ in self.issue.toc_sections()]
        self.assertEqual(sections, ["Editorials", "Research Articles", "Corrections"])

    def test_articles_are_chronological_within_a_section(self):
        self.attach(self.issue, "10.1371/journal.pmed.1000010", "Research Article", date(2021, 2, 20))
        self.attach(self.issue, "10.1371/journal.pmed.1000011", "Research Article", date(2021, 2, 2))

        _, articles = self.issue.toc_sections()[0]
        self.assertEqual(
            [article.doi for article in articles],
            ["10.1371/journal.pmed.1000011", "10.1371/journal.pmed.1000010"],
        )

    def test_same_day_articles_are_broken_by_doi_so_builds_are_stable(self):
        same_day = date(2021, 2, 9)
        self.attach(self.issue, "10.1371/journal.pmed.1000021", "Research Article", same_day)
        self.attach(self.issue, "10.1371/journal.pmed.1000020", "Research Article", same_day)

        _, articles = self.issue.toc_sections()[0]
        self.assertEqual(
            [article.doi for article in articles],
            ["10.1371/journal.pmed.1000020", "10.1371/journal.pmed.1000021"],
        )

    def test_unknown_types_land_in_other_at_the_bottom(self):
        self.attach(self.issue, "10.1371/journal.pmed.1000030", "Perspective", date(2021, 2, 5))
        self.attach(self.issue, "10.1371/journal.pmed.1000031", "Editorial", date(2021, 2, 6))

        sections = self.issue.toc_sections()
        self.assertEqual(sections[-1][0], article_types.OTHER)
        self.assertEqual(sections[-1][1][0].doi, "10.1371/journal.pmed.1000030")

    def test_missing_publication_date_sorts_last_rather_than_crashing(self):
        undated = self.attach(
            self.issue, "10.1371/journal.pmed.1000040", "Research Article", None
        )
        self.attach(self.issue, "10.1371/journal.pmed.1000041", "Research Article", date(2021, 2, 7))

        _, articles = self.issue.toc_sections()[0]
        self.assertEqual(articles[-1], undated)


class BulkDOIEntryTests(JournalTreeMixin, TestCase):
    def setUp(self):
        _, _, self.volume = self.build_tree()
        self.issue = self.make_issue(self.volume)

    def test_pasted_dois_become_articles_and_the_box_is_cleared(self):
        self.issue.bulk_dois = (
            "10.1371/journal.pmed.1000001\n"
            "  10.1371/JOURNAL.PMED.1000002  \n"
            "\n"
            "10.1371/journal.pmed.1000001\n"  # duplicate within the paste
        )
        self.issue.clean()
        self.issue.save()

        self.assertEqual(self.issue.issue_articles.count(), 2)
        self.assertEqual(self.issue.bulk_dois, "")
        self.assertTrue(Article.objects.filter(doi="10.1371/journal.pmed.1000002").exists())

    def test_already_attached_dois_are_not_duplicated(self):
        self.attach(self.issue, "10.1371/journal.pmed.1000001", "Editorial", date(2021, 2, 1))
        self.issue.bulk_dois = "10.1371/journal.pmed.1000001"
        self.issue.clean()
        self.issue.save()
        self.assertEqual(self.issue.issue_articles.count(), 1)


class CurrentIssueTests(JournalTreeMixin, TestCase):
    def test_blank_setting_falls_back_to_the_most_recent_issue(self):
        journal, _, volume = self.build_tree()
        self.make_issue(volume, number=1, title="January")
        february = self.make_issue(volume, number=2, title="February")
        self.assertEqual(IssuePage.current_for(journal), february)

    def test_explicit_pointer_wins(self):
        journal, _, volume = self.build_tree()
        january = self.make_issue(volume, number=1, title="January")
        self.make_issue(volume, number=2, title="February")

        settings_obj = JournalSettings.for_site(journal.get_site())
        settings_obj.current_issue = january
        settings_obj.save()

        self.assertEqual(IssuePage.current_for(journal), january)

    def test_pointer_from_another_journal_is_ignored(self):
        """One Site serves every journal, so the site-wide pointer has to be
        checked against the journal asking for it."""
        pmed, _, pmed_volume = self.build_tree()
        pbio, _, pbio_volume = self.build_tree(
            key="pbio", slug="plosbiology", volume_number=23, year=2025
        )
        pmed_issue = self.make_issue(pmed_volume, number=1, title="January")
        pbio_issue = self.make_issue(pbio_volume, number=1, title="January")

        settings_obj = JournalSettings.for_site(pmed.get_site())
        settings_obj.current_issue = pmed_issue
        settings_obj.save()

        self.assertEqual(IssuePage.current_for(pmed), pmed_issue)
        self.assertEqual(IssuePage.current_for(pbio), pbio_issue)


class CoverImageTests(JournalTreeMixin, TestCase):
    def test_live_issue_without_a_cover_is_invalid(self):
        _, _, volume = self.build_tree()
        issue = self.make_issue(volume)
        issue.cover_image = None
        with self.assertRaises(ValidationError) as caught:
            issue.clean()
        self.assertIn("cover_image", caught.exception.error_dict)

    def test_alt_text_falls_back_to_the_images_own(self):
        _, _, volume = self.build_tree()
        issue = self.make_issue(volume)
        self.assertEqual(issue.cover_alt, issue.cover_image.default_alt_text)
        issue.cover_alt_text = "Something more specific"
        self.assertEqual(issue.cover_alt, "Something more specific")


class ArticleTests(TestCase):
    def test_doi_is_normalised_on_save(self):
        article = Article.objects.create(doi="  10.1371/JOURNAL.PMED.1000001 ")
        self.assertEqual(article.doi, "10.1371/journal.pmed.1000001")

    def test_journal_key_is_read_from_the_doi(self):
        self.assertEqual(
            Article(doi="10.1371/journal.pcbi.1012387").journal_key, "pcbi"
        )

    def test_url_uses_the_journal_slug_not_the_doi_key(self):
        root = Page.get_first_root_node()
        index = publish(root, JournalIndexPage(title="PLOS Journals", slug="journals"))
        publish(
            index,
            JournalPage(
                title="PLOS Computational Biology",
                slug="ploscompbiol",
                journal_key="pcbi",
                display_name="PLOS Computational Biology",
            ),
        )
        article = Article(doi="10.1371/journal.pcbi.1012387")
        self.assertEqual(
            article.url, "/ploscompbiol/article?id=10.1371/journal.pcbi.1012387"
        )


class BakeryViewTests(JournalTreeMixin, TestCase):
    def setUp(self):
        self.journal, _, self.volume = self.build_tree()
        self.issue = self.make_issue(self.volume)
        self.build_dir = tempfile.mkdtemp()

    def build(self, view_class):
        with override_settings(BUILD_DIR=self.build_dir):
            view_class().build_method()
        return Path(self.build_dir)

    def test_issue_map_is_sharded_per_journal(self):
        built = self.build(IssueMapView)
        issue_map = json.loads((built / "plosmedicine" / "issue-map.json").read_text())
        self.assertEqual(
            issue_map["10.1371/issue.pmed.v18.i02"], "/plosmedicine/volume/2021/february/"
        )

    def test_doi_alias_stub_points_at_the_canonical_path(self):
        built = self.build(IssueDOIRedirectView)
        stub = built / "plosmedicine/issue/10.1371/issue.pmed.v18.i02/index.html"
        content = stub.read_text()
        self.assertIn('rel="canonical" href="/plosmedicine/volume/2021/february/"', content)
        self.assertIn('name="robots" content="noindex"', content)

    def test_legacy_query_param_shim_is_built_per_journal(self):
        built = self.build(CurrentIssueRedirectView)
        shim = (built / "plosmedicine" / "issue" / "index.html").read_text()
        self.assertIn("/plosmedicine/issue-map.json", shim)
        self.assertIn('name="robots" content="noindex"', shim)


SAMPLE_API_RESPONSE = {
    "response": {
        "numFound": 1,
        "docs": [
            {
                "id": "10.1371/journal.pcbi.1012387",
                "publication_date": "2024-09-05T00:00:00Z",
                "article_type": "Research Article",
                "author_display": [
                    "Caterina Millevoi",
                    "Damiano Pasetto",
                    "Massimiliano Ferronato",
                ],
                "title_display": (
                    "A Physics-Informed Neural Network approach for compartmental "
                    "epidemiological models"
                ),
            }
        ],
    }
}


class MetadataSyncTests(JournalTreeMixin, TestCase):
    """
    Exercises the one seam between this CMS and the article repository. The
    network is mocked deliberately: the build must never depend on it, and
    neither should the tests.
    """

    def test_search_api_fields_map_onto_the_snippet(self):
        Article.objects.create(doi="10.1371/journal.pcbi.1012387")

        with mock.patch("journals.management.commands.sync_article_metadata.requests.get") as get:
            get.return_value = mock.Mock(
                status_code=200,
                json=lambda: SAMPLE_API_RESPONSE,
                raise_for_status=lambda: None,
            )
            call_command("sync_article_metadata", rate=0, verbosity=0)

        article = Article.objects.get(doi="10.1371/journal.pcbi.1012387")
        self.assertEqual(article.article_type, "Research Article")
        self.assertEqual(article.published_at, date(2024, 9, 5))
        self.assertEqual(
            article.authors,
            "Caterina Millevoi, Damiano Pasetto, Massimiliano Ferronato",
        )
        self.assertTrue(article.title.startswith("A Physics-Informed"))
        self.assertTrue(article.is_synced)

    def test_request_always_trims_fields_and_sets_rows(self):
        Article.objects.create(doi="10.1371/journal.pcbi.1012387")

        with mock.patch("journals.management.commands.sync_article_metadata.requests.get") as get:
            get.return_value = mock.Mock(
                status_code=200,
                json=lambda: SAMPLE_API_RESPONSE,
                raise_for_status=lambda: None,
            )
            call_command("sync_article_metadata", rate=0, verbosity=0)

        params = get.call_args.kwargs["params"]
        # Without `fl` the response carries the whole abstract; without `rows`
        # Solr silently truncates the batch to 10.
        self.assertIn("title_display", params["fl"])
        self.assertNotIn("abstract", params["fl"])
        self.assertEqual(params["rows"], 1)
        self.assertIn('"10.1371/journal.pcbi.1012387"', params["q"])

    def test_fail_on_missing_rejects_an_incomplete_attached_article(self):
        _, _, volume = self.build_tree()
        issue = self.make_issue(volume)
        self.attach(issue, "10.1371/journal.pmed.1000001", "", None)

        with self.assertRaises(CommandError):
            call_command(
                "sync_article_metadata",
                fail_on_missing=True,
                dois=["10.1371/journal.pmed.9999999"],
                verbosity=0,
            )

    def test_fail_on_missing_passes_when_metadata_is_complete(self):
        _, _, volume = self.build_tree()
        issue = self.make_issue(volume)
        article = self.attach(
            issue, "10.1371/journal.pmed.1000001", "Research Article", date(2021, 2, 1)
        )
        article.metadata_synced_at = timezone.now()
        article.save()

        call_command(
            "sync_article_metadata",
            fail_on_missing=True,
            dois=["10.1371/journal.pmed.9999999"],
            verbosity=0,
        )


class QueryCountTests(JournalTreeMixin, TestCase):
    def test_issue_page_render_does_not_scale_with_article_count(self):
        """
        N+1 queries are merely slow in a request; in a full rebuild of every
        journal they are fatal (design §7.5).
        """
        _, _, volume = self.build_tree()
        issue = self.make_issue(volume)

        def attach_and_count(start, stop):
            for n in range(start, stop):
                self.attach(
                    issue,
                    f"10.1371/journal.pmed.100{n:04d}",
                    "Research Article",
                    date(2021, 2, 1),
                )
            issue.refresh_from_db()
            with CaptureQueriesContext(connection) as captured:
                issue.toc_sections()
            return len(captured)

        with_five = attach_and_count(0, 5)
        with_forty = attach_and_count(5, 40)
        self.assertEqual(with_five, with_forty)
