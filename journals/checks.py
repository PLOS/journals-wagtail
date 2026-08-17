"""
Startup validation for the article-type config.

Without this a typo in a per-journal override fails at render time, deep in a
build, with a bare KeyError (design §4.3).
"""

import yaml
from django.core.checks import Error, Tags, register

from journals import article_types


@register(Tags.compatibility)
def check_article_type_config(app_configs, **kwargs):
    errors = []

    article_types.clear_caches()
    try:
        vocabulary = article_types.vocabulary()
        order_lists = {"<default_order>": article_types._raw()["default_order"]}
    except Exception as exc:  # noqa: BLE001 - surfaced as a check, not a traceback
        return [
            Error(
                f"Could not read {article_types.VOCABULARY_FILE}: {exc}",
                id="journals.E000",
            )
        ]

    for path in sorted(article_types.CONFIG_DIR.glob("article_types.*.yaml")):
        try:
            order_lists[path.name] = yaml.safe_load(path.read_text())["order"]
        except Exception as exc:  # noqa: BLE001
            errors.append(Error(f"Could not read {path}: {exc}", id="journals.E000"))

    for source, keys in order_lists.items():
        for key in keys:
            if key not in vocabulary:
                errors.append(
                    Error(
                        f"Unknown article type '{key}' in {source}.",
                        hint=(
                            "Every key in an order list must be defined under "
                            f"'types' in {article_types.VOCABULARY_FILE.name}."
                        ),
                        id="journals.E001",
                    )
                )

    return errors
