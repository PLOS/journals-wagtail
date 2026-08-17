"""
Cache article metadata into `Article` snippets (design §5).

The build must never touch the network, so titles, authors, types and dates are
resolved ahead of time and stored. The source is the public PLOS search API for
now; swapping it for the article pipeline's own manifest later is a change to
`fetch_metadata()` alone, with no model or template impact — keep it that way.
"""

import time
from datetime import datetime, timedelta

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from journals.models import Article

FIELDS = "id,title_display,author_display,article_type,publication_date"


class RateLimiter:
    """Politeness, not throughput. A 400k backfill is exactly the traffic that
    gets an IP blocked, so the default is deliberately slow."""

    def __init__(self, seconds):
        self.seconds = seconds
        self.last_call = 0.0

    def wait(self):
        elapsed = time.monotonic() - self.last_call
        if elapsed < self.seconds:
            time.sleep(self.seconds - elapsed)
        self.last_call = time.monotonic()


def fetch_metadata(dois, *, rate_limiter=None, timeout=None, max_retries=4, stdout=None):
    """
    The single seam between this CMS and whatever holds article metadata.

    dois -> {doi: {"title": ..., "authors": ..., "article_type": ...,
                   "published_at": date|None}}

    Missing DOIs are simply absent from the result; the caller decides whether
    that is fatal.
    """
    if not dois:
        return {}

    url = getattr(settings, "PLOS_SEARCH_API_URL", "https://api.plos.org/search")
    timeout = timeout or getattr(settings, "PLOS_SEARCH_TIMEOUT", 30)

    # Quote each DOI: unquoted works for a single lookup, but the slashes and
    # dots are Solr-significant inside a boolean clause.
    query = " OR ".join(f'"{doi}"' for doi in dois)
    params = {
        "q": f"id:({query})",
        # Always pass `fl`: the default response includes the full abstract,
        # which is ~3 KB per article of data we discard.
        "fl": FIELDS,
        # Always pass `rows`: the Solr default is 10, so batches truncate
        # silently without it.
        "rows": len(dois),
        "wt": "json",
    }

    for attempt in range(max_retries):
        if rate_limiter:
            rate_limiter.wait()
        try:
            response = requests.get(url, params=params, timeout=timeout)
            if response.status_code == 429 or response.status_code >= 500:
                raise requests.HTTPError(f"HTTP {response.status_code}")
            response.raise_for_status()
            docs = response.json()["response"]["docs"]
            break
        except (requests.RequestException, ValueError, KeyError) as exc:
            if attempt == max_retries - 1:
                raise CommandError(f"Search API request failed: {exc}") from exc
            backoff = 2**attempt
            if stdout:
                stdout.write(f"  request failed ({exc}); retrying in {backoff}s")
            time.sleep(backoff)

    return {doc["id"].lower(): parse_doc(doc) for doc in docs if doc.get("id")}


def parse_doc(doc):
    published_at = None
    raw_date = doc.get("publication_date")
    if raw_date:
        published_at = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
    return {
        "title": doc.get("title_display", "") or "",
        "authors": ", ".join(doc.get("author_display", []) or []),
        # Stored verbatim: it is the join key into the YAML vocabulary (§4.3),
        # so normalising it here would silently break section grouping.
        "article_type": doc.get("article_type", "") or "",
        "published_at": published_at,
    }


def parse_since(value):
    """Accept '7d', '12h' or an ISO date."""
    if value.endswith("d") and value[:-1].isdigit():
        return timezone.now() - timedelta(days=int(value[:-1]))
    if value.endswith("h") and value[:-1].isdigit():
        return timezone.now() - timedelta(hours=int(value[:-1]))
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(f"Could not parse --since value {value!r}.") from exc
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed


class Command(BaseCommand):
    help = "Cache article metadata from the PLOS search API into Article snippets."

    def add_arguments(self, parser):
        parser.add_argument(
            "--since",
            help="Only sync articles last synced before this point, e.g. 7d, 12h "
            "or 2026-01-01. Articles never synced are always included.",
        )
        parser.add_argument(
            "--doi",
            action="append",
            dest="dois",
            help="Sync a specific DOI. May be repeated.",
        )
        parser.add_argument(
            "--attached-only",
            action="store_true",
            help="Only sync DOIs attached to an issue. The default once the "
            "initial backfill is done.",
        )
        parser.add_argument(
            "--fail-on-missing",
            action="store_true",
            help="Exit non-zero if any article attached to an issue still lacks "
            "a title, type or publication date. Use this as a build gate.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=getattr(settings, "PLOS_SEARCH_BATCH_SIZE", 50),
        )
        parser.add_argument(
            "--rate",
            type=float,
            default=getattr(settings, "PLOS_SEARCH_RATE_LIMIT", 1.0),
            help="Minimum seconds between requests.",
        )
        parser.add_argument("--limit", type=int, help="Stop after this many articles.")
        parser.add_argument(
            "--dry-run", action="store_true", help="Report what would be synced."
        )

    def handle(self, *args, **options):
        queryset = self.select_articles(options)
        dois = list(queryset.values_list("doi", flat=True))
        if options["limit"]:
            dois = dois[: options["limit"]]

        self.stdout.write(f"{len(dois)} article(s) to sync.")
        if options["dry_run"]:
            for doi in dois:
                self.stdout.write(f"  would sync {doi}")
        else:
            self.sync(dois, options)

        if options["fail_on_missing"]:
            self.check_completeness()

    def select_articles(self, options):
        queryset = Article.objects.all()
        if options["dois"]:
            return queryset.filter(doi__in=[doi.strip().lower() for doi in options["dois"]])
        if options["attached_only"]:
            queryset = queryset.filter(issue_entries__isnull=False).distinct()
        if options["since"]:
            cutoff = parse_since(options["since"])
            queryset = queryset.filter(
                Q(metadata_synced_at__isnull=True) | Q(metadata_synced_at__lt=cutoff)
            )
        return queryset.order_by("doi")

    def sync(self, dois, options):
        rate_limiter = RateLimiter(options["rate"])
        batch_size = options["batch_size"]
        synced = 0
        missing = []

        for start in range(0, len(dois), batch_size):
            batch = dois[start : start + batch_size]
            self.stdout.write(
                f"  fetching {start + 1}–{start + len(batch)} of {len(dois)}…"
            )
            results = fetch_metadata(
                batch, rate_limiter=rate_limiter, stdout=self.stdout
            )
            # Written per batch rather than at the end, so an interrupted run
            # resumes from where it stopped rather than starting over.
            for article in Article.objects.filter(doi__in=batch):
                data = results.get(article.doi)
                if data is None:
                    missing.append(article.doi)
                    continue
                self.apply(article, data)
                synced += 1

        self.stdout.write(self.style.SUCCESS(f"Synced {synced} article(s)."))
        if missing:
            self.stdout.write(
                self.style.WARNING(
                    f"{len(missing)} DOI(s) not found in the search index: "
                    + ", ".join(missing[:10])
                    + ("…" if len(missing) > 10 else "")
                )
            )

    def apply(self, article, data):
        """Log ordering-relevant changes: an upstream correction can reorder a
        published TOC with no CMS edit behind it (design §11)."""
        if article.published_at and data["published_at"] != article.published_at:
            self.stdout.write(
                self.style.WARNING(
                    f"  {article.doi}: publication date "
                    f"{article.published_at} → {data['published_at']} (TOC order may change)"
                )
            )
        if article.article_type and data["article_type"] != article.article_type:
            self.stdout.write(
                self.style.WARNING(
                    f"  {article.doi}: article type "
                    f"{article.article_type!r} → {data['article_type']!r} (TOC section may change)"
                )
            )
        for field, value in data.items():
            setattr(article, field, value)
        article.metadata_synced_at = timezone.now()
        article.save()

    def check_completeness(self):
        """
        The build gate (design §5).

        `published_at` and `article_type` are render-critical, not decorative:
        they decide what the TOC looks like. A null date sorts to the end of its
        section and the page renders plausibly but wrongly — the worst failure
        mode, because nobody notices.
        """
        incomplete = (
            Article.objects.filter(issue_entries__isnull=False)
            .filter(Q(published_at__isnull=True) | Q(article_type="") | Q(title=""))
            .distinct()
        )
        if not incomplete.exists():
            self.stdout.write(
                self.style.SUCCESS("All articles attached to an issue have complete metadata.")
            )
            return
        for article in incomplete[:50]:
            self.stderr.write(
                f"  incomplete: {article.doi} "
                f"(title={bool(article.title)}, type={article.article_type!r}, "
                f"published_at={article.published_at})"
            )
        raise CommandError(
            f"{incomplete.count()} article(s) attached to an issue have incomplete "
            "metadata. Refusing to let a build ship a broken table of contents."
        )
