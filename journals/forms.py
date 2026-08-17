from wagtail.admin.forms import WagtailAdminPageForm


class TreeAwarePageForm(WagtailAdminPageForm):
    """
    Make the parent page reachable from `Page.clean()`.

    Wagtail validates a brand-new page *before* it is added to the tree, so
    `get_parent()` returns nothing during the first save. Several models here
    derive DOIs from their ancestors (design §4.2), so they need the parent at
    validation time. The form knows it; hand it to the instance.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.parent_page is not None:
            self.instance.parent_page_hint = self.parent_page.specific
