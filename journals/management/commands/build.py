"""
`manage.py build`, with the Wagtail image renditions actually included.

django-bakery copies MEDIA_ROOT into BUILD_DIR *before* it renders any view,
but Wagtail creates image renditions lazily, as a side effect of the `{% image %}`
tags rendering during that render pass. So on any build that produces a new
rendition — a cold checkout, or the first build after a template change — the
cover files are created just after they were copied, and the built site ships
with broken images. It looks fine on the second build, which is what makes it
worth fixing rather than remembering.

This command re-copies the media directory once the views are done. It is the
cheap fix. The better one, once this is more than a PoC, is to take media out of
the build entirely: point MEDIA_URL at a CDN-fronted bucket and have
django-storages write renditions there directly (design §7.4). Cover originals
are large, they almost never change, and copying them into every full rebuild
wastes minutes per deploy.

This shadows `bakery.management.commands.build`, which works because Django
resolves management commands in INSTALLED_APPS order and "journals" is listed
before "bakery".
"""

from bakery.management.commands.build import Command as BakeryBuildCommand


class Command(BakeryBuildCommand):
    help = (
        "Bake out the site as flat files in the build directory, including image "
        "renditions created during the build itself."
    )

    def handle(self, *args, **options):
        super().handle(*args, **options)

        if options.get("skip_media"):
            return

        if self.verbosity > 1:
            self.stdout.write("Re-copying media for renditions created during the build")
        self.build_media()
