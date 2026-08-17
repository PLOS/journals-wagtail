"""
Per-journal editor groups and image collections (design §4.6).

One install holds every journal, which only works because Wagtail scopes group
permissions to page subtrees. Set this up before editors get access —
retrofitting permissions afterwards is unpleasant.

Images and documents are *global* rather than per-subtree, so each journal also
gets a Collection and the group's image permissions are scoped to it. Without
that, sixteen journals' covers land in one library and editors reuse each
other's.
"""

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from wagtail.models import Collection, GroupCollectionPermission, GroupPagePermission

from journals.models import JournalPage

# No bulk_delete: editors unpublish, they do not delete (design §7.6). Deleting a
# volume cascades to every issue under it, and the confirmation is easy to click
# through.
PAGE_PERMISSIONS = ["add", "change", "publish", "lock", "unlock"]

IMAGE_PERMISSIONS = [
    ("wagtailimages", "image", "add_image"),
    ("wagtailimages", "image", "change_image"),
    ("wagtailimages", "image", "choose_image"),
]


class Command(BaseCommand):
    help = "Create a per-journal editor group and image collection for each journal."

    def handle(self, *args, **options):
        root_collection = Collection.get_first_root_node()
        access_admin = Permission.objects.get(
            content_type__app_label="wagtailadmin", codename="access_admin"
        )

        for journal in JournalPage.objects.all():
            group, created = Group.objects.get_or_create(
                name=f"{journal.display_name} editors"
            )
            group.permissions.add(access_admin)

            collection = root_collection.get_children().filter(
                name=journal.display_name
            ).first()
            if collection is None:
                collection = root_collection.add_child(name=journal.display_name)

            for permission_type in PAGE_PERMISSIONS:
                GroupPagePermission.objects.get_or_create(
                    group=group,
                    page=journal,
                    permission=Permission.objects.get(
                        content_type__app_label="wagtailcore",
                        content_type__model="page",
                        codename=f"{permission_type}_page",
                    ),
                )

            for app_label, model, codename in IMAGE_PERMISSIONS:
                GroupCollectionPermission.objects.get_or_create(
                    group=group,
                    collection=collection,
                    permission=Permission.objects.get(
                        content_type__app_label=app_label,
                        content_type__model=model,
                        codename=codename,
                    ),
                )

            self.stdout.write(
                self.style.SUCCESS(
                    f"{'Created' if created else 'Updated'} group "
                    f"'{group.name}' scoped to /{journal.slug}/ "
                    f"and collection '{collection.name}'."
                )
            )
