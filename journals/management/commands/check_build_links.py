"""
Verify the built output before anyone publishes it.

Two checks, both of which have caught real problems in this design:

* every internal link resolves to a file that exists in BUILD_DIR — this is what
  turns a missing image rendition or a stale DOI stub into a failed build
  instead of a broken page;
* the build contains a plausible number of pages, so a `--delete` publish can
  never wipe the site from an empty or half-finished build (design §7.6).

Article links (`/<journal>/article?id=…`) are skipped: those pages belong to a
separate pipeline (§7.7). Checking them needs a crawl of the live bucket, which
is the cross-pipeline link check in M5, not something this build can answer.
"""

import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

LINK_RE = re.compile(r'(?:href|src)="(/[^"]*)"')


class Command(BaseCommand):
    help = "Check that internal links in the built site resolve, and that it looks complete."

    def add_arguments(self, parser):
        parser.add_argument("--build-dir", default=settings.BUILD_DIR)
        parser.add_argument(
            "--min-pages",
            type=int,
            default=1,
            help="Fail if the build contains fewer HTML pages than this. Set it "
            "to something close to the real page count in CI.",
        )

    def handle(self, *args, **options):
        build_dir = Path(options["build_dir"])
        if not build_dir.exists():
            raise CommandError(f"{build_dir} does not exist. Run `manage.py build` first.")

        pages = list(build_dir.rglob("*.html"))
        pages = [page for page in pages if "static" not in page.relative_to(build_dir).parts]
        self.stdout.write(f"{len(pages)} page(s) in {build_dir}.")

        broken = []
        for page in pages:
            text = page.read_text(errors="ignore")
            for link in sorted(set(LINK_RE.findall(text))):
                url = link.split("#")[0].split("?")[0]
                if not url or "/article" in url:
                    continue
                target = build_dir / url.lstrip("/")
                if target.is_dir():
                    target = target / "index.html"
                if not target.exists():
                    broken.append((page.relative_to(build_dir), link))

        for page, link in broken[:50]:
            self.stderr.write(f"  {page} → {link}")

        if broken:
            raise CommandError(f"{len(broken)} broken internal link(s) in the build.")

        if len(pages) < options["min_pages"]:
            raise CommandError(
                f"Only {len(pages)} page(s) built, expected at least "
                f"{options['min_pages']}. Refusing to call this build complete."
            )

        self.stdout.write(self.style.SUCCESS("Every internal link in the build resolves."))
