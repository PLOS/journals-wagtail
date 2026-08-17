"""
Buildable views for output that isn't a Wagtail page (design §7.3).

`AllPublishedPagesView` bakes the page tree and nothing else, so the DOI alias
stubs, the legacy `?id=` shim, its lookup table and the sitemap each need a view
of their own.

Every view here iterates `JournalPage.objects.live()` rather than hardcoding a
journal. That is what keeps the per-journal-build escape hatch (§7.5) available:
`build_queryset()` can be filtered later without rewriting the views.
"""

import json
import os

from bakery.views import BuildableMixin
from django.conf import settings
from django.template.loader import render_to_string
from django.test.client import RequestFactory
from wagtail.contrib.sitemaps.views import sitemap as sitemap_view
from wagtail.models import Site

from journals.models import IssuePage, JournalPage


class JournalBuildableView(BuildableMixin):
    """Shared plumbing: write one file per journal (or per issue)."""

    @property
    def build_method(self):
        return self.build

    def journals(self):
        return JournalPage.objects.live().specific()

    def issues_for(self, journal):
        return IssuePage.objects.descendant_of(journal).live().specific()

    def write(self, build_path, content):
        """`build_path` is relative to BUILD_DIR, e.g. 'plosmedicine/issue/index.html'."""
        self.prep_directory(build_path)
        full_path = os.path.join(settings.BUILD_DIR, build_path)
        self.build_file(full_path, content.encode("utf-8"))

    def create_request(self, path):
        site = Site.objects.filter(is_default_site=True).first()
        server_name = site.hostname if site else "testserver"
        return RequestFactory(SERVER_NAME=server_name).get(path)


class IssueDOIRedirectView(JournalBuildableView):
    """
    DOI alias paths (§6.2): `/plosmedicine/issue/10.1371/issue.pmed.v18.i02/`.

    DOIs are the durable identifier and appear in citations, so they get a
    permanent home — but as a redirect stub, not a second copy of the page. The
    slash inside the DOI simply becomes a directory separator.
    """

    template_name = "journals/redirect_stub.html"

    def build(self):
        for journal in self.journals():
            for issue in self.issues_for(journal):
                if not issue.doi or not issue.url:
                    continue
                build_path = os.path.join(
                    journal.slug, "issue", issue.doi, "index.html"
                )
                self.request = self.create_request("/" + build_path)
                content = render_to_string(
                    self.template_name,
                    {
                        "target_url": issue.url,
                        "title": f"{journal.display_name}: {issue.title}",
                        "doi": issue.doi,
                    },
                    request=self.request,
                )
                self.write(build_path, content)


class CurrentIssueRedirectView(JournalBuildableView):
    """
    `/<journal>/issue/index.html` — the transitional shim for legacy
    `?id=<doi>` URLs (§6.3).

    A static file server discards the query string, so this page reads `?id`
    client-side, looks it up in issue-map.json and replaces the location. With
    no `id` it forwards to the current issue. It is `noindex` so it never
    competes with the canonical paths, and it is deleted at sunset.
    """

    template_name = "journals/issue_redirect.html"

    def build(self):
        for journal in self.journals():
            current = IssuePage.current_for(journal)
            build_path = os.path.join(journal.slug, "issue", "index.html")
            self.request = self.create_request("/" + build_path)
            content = render_to_string(
                self.template_name,
                {
                    "journal": journal,
                    "current_issue": current,
                    "current_issue_url": current.url if current else journal.url,
                    "issue_map_url": f"/{journal.slug}/issue-map.json",
                },
                request=self.request,
            )
            self.write(build_path, content)


class IssueMapView(JournalBuildableView):
    """
    `/<journal>/issue-map.json` — DOI → canonical path, for the shim above.

    Sharded per journal from the start: ~280 entries for PLOS Medicine, ~3,000
    across all journals, and no reason to make every reader download all of it.
    Temporary; deleted at sunset along with the shim.
    """

    def build(self):
        for journal in self.journals():
            issue_map = {
                issue.doi: issue.url
                for issue in self.issues_for(journal)
                if issue.doi and issue.url
            }
            build_path = os.path.join(journal.slug, "issue-map.json")
            self.write(build_path, json.dumps(issue_map, indent=1, sort_keys=True))


class SitemapView(JournalBuildableView):
    """
    `sitemap.xml`.

    `wagtail.contrib.sitemaps` is a view rather than a page, so bakery omits it
    silently unless it is built explicitly. Easy to miss.
    """

    build_path = "sitemap.xml"

    def build(self):
        self.request = self.create_request("/sitemap.xml")
        response = sitemap_view(self.request)
        content = response.render().content.decode("utf-8")
        self.write(self.build_path, content)
