"""
Homepage building blocks — the Lemur homepage CMS, as StreamField content.

The legacy homepage tool models each homepage as a dated record whose Hero,
Billboard and Tier slots are filled either with an article (by DOI) or with a
link to somewhere else on the web. Two things carry over directly:

* a *slot* is one of two kinds of thing — an `Article` snippet or a URL — so
  each is its own block type and the editor picks from the "+" menu;
* every slot renders the same way — kicker, headline, teaser, image — so both
  block types expose one value API (`CardValue`) and the templates never ask
  which kind they are holding.

What deliberately does *not* carry over is the dated homepage list, the
"publish" button and the "mark as completed" checkbox: those are Wagtail's
revisions, scheduled publishing and workflows, and reimplementing them here
would mean maintaining a second, worse copy of features the CMS already has.
"""

from wagtail.blocks import (
    CharBlock,
    RichTextBlock,
    StreamBlock,
    StructBlock,
    StructValue,
    TextBlock,
    URLBlock,
)
from wagtail.images.blocks import ImageChooserBlock
from wagtail.snippets.blocks import SnippetChooserBlock

# Credits routinely carry a licence link ("CC BY 4.0"), so plain text is not
# enough — but nothing beyond a link belongs in one either.
CREDIT_FEATURES = ["bold", "italic", "link"]


class CardValue(StructValue):
    """
    One homepage slot, however it was filled in.

    Article slots read through to the `Article` snippet for anything the editor
    did not override, so a card that is only a DOI still renders a headline, a
    subject and a date once `sync_article_metadata` has run. Templates use these
    properties rather than the raw fields; that is what lets one card template
    serve both block types.
    """

    # The resolved properties are deliberately not named after the child
    # blocks they read: a template resolves `value.headline` as a dictionary
    # lookup before it ever looks for a property, so a property called
    # `headline` would be silently unreachable from a template — and would
    # return the un-resolved override instead.

    @property
    def article(self):
        """The chosen snippet, or None on a URL slot. Python-side only."""
        return self.get("article")

    @property
    def href(self):
        article = self.article
        if article is not None:
            return article.url
        return self.get("url", "")

    @property
    def title(self):
        headline = self.get("headline")
        if headline:
            return headline
        article = self.article
        if article is not None:
            # A DOI is a poor headline, but it is better than a blank card and
            # it tells the editor exactly which article still needs syncing.
            return article.title or article.doi
        return self.href

    @property
    def subject(self):
        kicker = self.get("kicker")
        if kicker:
            return kicker
        article = self.article
        return article.article_type if article is not None else ""

    @property
    def authors(self):
        article = self.article
        return article.authors if article is not None else ""

    @property
    def date(self):
        article = self.article
        return article.published_at if article is not None else None

    @property
    def is_external(self):
        return self.article is None

    @property
    def needs_metadata(self):
        """An article slot whose snippet has not been synced yet."""
        article = self.article
        return article is not None and not article.is_synced


class ArticleCardBlock(StructBlock):
    """A slot filled with an `Article` snippet."""

    article = SnippetChooserBlock(
        "journals.Article",
        help_text="Search by DOI, title or author. New DOIs can be added from "
        "Snippets → Articles.",
    )
    kicker = CharBlock(
        required=False,
        max_length=64,
        label="Subject",
        help_text="Defaults to the article's type, e.g. 'Research Article'.",
    )
    headline = CharBlock(
        required=False,
        max_length=255,
        help_text="Defaults to the article's own title.",
    )
    teaser = TextBlock(
        required=False,
        help_text="The homepage summary. Written for the homepage, so it is "
        "never derived from the abstract.",
    )
    image = ImageChooserBlock(required=False)
    image_credit = RichTextBlock(required=False, features=CREDIT_FEATURES)

    class Meta:
        value_class = CardValue
        template = "journals/blocks/card.html"
        icon = "doc-full-inverse"
        label = "Article"


class LinkCardBlock(StructBlock):
    """
    A slot filled with a link to anywhere else — a collection, a blog post, a
    submission page.

    The fields repeat `ArticleCardBlock`'s rather than inheriting them: here
    the headline is the only source of the headline, so it is required, and
    there is no snippet to fall back to for the subject or the date.
    """

    url = URLBlock(label="URL")
    kicker = CharBlock(required=False, max_length=64, label="Subject")
    headline = CharBlock(max_length=255)
    teaser = TextBlock(required=False)
    image = ImageChooserBlock(required=False)
    image_credit = RichTextBlock(required=False, features=CREDIT_FEATURES)

    class Meta:
        value_class = CardValue
        template = "journals/blocks/card.html"
        icon = "link"
        label = "URL"


class CardStreamBlock(StreamBlock):
    """
    The contents of a homepage region.

    Every region — hero, billboard, each tier — is made of the same two block
    types and differs only in how many slots it holds and how it is rendered,
    so `max_num` and the template do all the varying.
    """

    article = ArticleCardBlock()
    link = LinkCardBlock()

    class Meta:
        icon = "list-ul"
