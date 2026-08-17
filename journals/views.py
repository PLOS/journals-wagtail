from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from wagtail.admin import messages as wagtail_messages
from wagtail.models import Site

from journals.models import IssuePage, JournalSettings


def set_current_issue(request, page_id):
    """
    The old admin's "Set as current" radio button, as a Wagtail admin action.

    GET confirms, POST applies — the pointer is reader-visible, so it should not
    move because something prefetched a link.
    """
    page = get_object_or_404(IssuePage, id=page_id)
    if not page.permissions_for_user(request.user).can_publish():
        return redirect("wagtailadmin_home")

    next_url = request.GET.get("next") or request.POST.get("next")

    if request.method == "POST":
        site = page.get_site() or Site.objects.get(is_default_site=True)
        journal_settings = JournalSettings.for_site(site)
        journal_settings.current_issue = page
        journal_settings.save()
        wagtail_messages.success(
            request, _("'%(title)s' is now the current issue.") % {"title": page.title}
        )
        if next_url:
            return redirect(next_url)
        return redirect("wagtailadmin_explore", page.get_parent().id)

    return render(
        request,
        "journals/admin/confirm_set_current_issue.html",
        {"page": page, "next": next_url},
    )
