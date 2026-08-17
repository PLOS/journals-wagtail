from django.apps import AppConfig


class JournalsConfig(AppConfig):
    default_auto_field = "django.db.models.AutoField"
    name = "journals"

    def ready(self):
        from journals import checks  # noqa: F401  (registers the system check)
