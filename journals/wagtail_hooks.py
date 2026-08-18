from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.html import format_html
from wagtail import hooks
from wagtail.admin import messages as wagtail_messages
from wagtail.admin.widgets.button import ListingButton
from wagtail.snippets.models import register_snippet
from wagtail.snippets.views.snippets import SnippetViewSet

from journals import views
from journals.models import Article, IssuePage, JournalPage


class ArticleViewSet(SnippetViewSet):
    """
    Listing for the cached article metadata (design §4.3).

    Read-mostly: the DOI is the only editable field, everything else is filled
    in by `sync_article_metadata`, so the listing's job is to make it obvious
    what has *not* been synced yet.
    """

    model = Article
    icon = "doc-full-inverse"
    menu_label = "Articles"
    list_display = ["doi", "title", "article_type", "published_at", "sync_status"]
    list_filter = ["article_type"]
    search_fields = ["doi", "title", "authors"]
    list_per_page = 50
    ordering = ["doi"]
    inspect_view_enabled = True


register_snippet(ArticleViewSet)


@hooks.register("register_admin_urls")
def register_journal_admin_urls():
    return [
        path(
            "journals/issue/<int:page_id>/set-current/",
            views.set_current_issue,
            name="journals_set_current_issue",
        ),
    ]


@hooks.register("register_page_listing_buttons")
def issue_listing_buttons(page, user, next_url=None):
    """Replaces the old admin's 'Set as current' radio column."""
    if not isinstance(page.specific_deferred, IssuePage):
        return
    if not page.permissions_for_user(user).can_publish():
        return
    url = reverse("journals_set_current_issue", args=[page.id])
    if next_url:
        url = f"{url}?next={next_url}"
    yield ListingButton("Set as current issue", url, icon_name="pick", priority=45)


@hooks.register("before_publish_page")
def require_cover_image(request, page):
    """
    Covers are Wagtail-owned (decision 4) and there is no remote fallback, so a
    published issue without one is a broken image on the live archive.
    """
    if not isinstance(page, IssuePage) or page.cover_image_id:
        return
    wagtail_messages.error(
        request,
        f"'{page.title}' cannot be published without a cover image.",
    )
    return redirect("wagtailadmin_pages:edit", page.id)


@hooks.register("before_edit_page")
def warn_about_unsynced_articles(request, page):
    """Surface stale metadata where the editor is actually working (design §5)."""
    if not isinstance(page, IssuePage) or not page.pk:
        return
    unsynced = [
        article
        for article in Article.objects.filter(issue_entries__page=page)
        if not article.is_synced
    ]
    if not unsynced:
        return
    listed = ", ".join(article.doi for article in unsynced[:5])
    if len(unsynced) > 5:
        listed += f" (+{len(unsynced) - 5} more)"
    wagtail_messages.warning(
        request,
        format_html(
            "{} article(s) on this issue have no cached metadata yet: {}. "
            "Run <code>manage.py sync_article_metadata</code> before building.",
            len(unsynced),
            listed,
        ),
    )


@hooks.register("before_edit_page")
def warn_about_unsynced_homepage_cards(request, page):
    """
    The homepage equivalent of the warning above.

    A homepage card falls back to the article's cached title, so an unsynced
    article does not fail — it quietly puts a DOI where a headline should be,
    on the journal's front page. Say so while the editor is still on the form.
    """
    if not isinstance(page, JournalPage) or not page.pk:
        return
    unsynced = sorted(
        {
            card.article.doi
            for card in page.homepage_cards()
            if card.needs_metadata
        }
    )
    if not unsynced:
        return
    listed = ", ".join(unsynced[:5])
    if len(unsynced) > 5:
        listed += f" (+{len(unsynced) - 5} more)"
    wagtail_messages.warning(
        request,
        format_html(
            "{} homepage card(s) point at articles with no cached metadata yet: "
            "{}. They will show a DOI instead of a headline until "
            "<code>manage.py sync_article_metadata</code> has run.",
            len(unsynced),
            listed,
        ),
    )
