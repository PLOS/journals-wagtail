"""
The article-type vocabulary and per-journal section ordering.

Deliberately file-based rather than a database model (design §4.3): section
placement is an editorial convention that wants a code review, not a drag
handle. `lru_cache` matters — a full build renders thousands of issues and
re-reading YAML per page would be a silly, invisible cost.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from django.conf import settings

CONFIG_DIR = Path(settings.BASE_DIR) / "journals" / "config"

VOCABULARY_FILE = CONFIG_DIR / "article_types.yaml"


@dataclass(frozen=True)
class ArticleType:
    key: str
    name: str  # matches the search API's article_type verbatim
    plural: str  # section heading
    description: str = ""

    @property
    def anchor(self) -> str:
        # Underscore-joined name, matching existing #Research_Article deep
        # links. Deliberately NOT the YAML key: 'expression_of_concern'
        # happens to coincide, 'short_reports' does not.
        return self.name.replace(" ", "_")


OTHER = ArticleType(key="other", name="Other", plural="Other")


@lru_cache(maxsize=None)
def _raw() -> dict:
    return yaml.safe_load(VOCABULARY_FILE.read_text())


@lru_cache(maxsize=None)
def vocabulary() -> dict[str, ArticleType]:
    """Every known article type, keyed by its YAML key."""
    return {key: ArticleType(key=key, **value) for key, value in _raw()["types"].items()}


def override_path(journal_key: str) -> Path:
    return CONFIG_DIR / f"article_types.{journal_key}.yaml"


@lru_cache(maxsize=None)
def order_keys(journal_key: str) -> tuple[str, ...]:
    """The configured key order for a journal, before it is resolved to types."""
    override = override_path(journal_key)
    if override.exists():
        return tuple(yaml.safe_load(override.read_text())["order"])
    return tuple(_raw()["default_order"])


@lru_cache(maxsize=None)
def section_order(journal_key: str) -> tuple[ArticleType, ...]:
    """Ordered sections for a journal, with OTHER always last."""
    vocab = vocabulary()
    return tuple(vocab[key] for key in order_keys(journal_key)) + (OTHER,)


@lru_cache(maxsize=None)
def by_name(journal_key: str) -> dict[str, ArticleType]:
    """Lookup from the API's article_type string to a type. Unknown -> OTHER."""
    return {article_type.name: article_type for article_type in section_order(journal_key)}


def clear_caches() -> None:
    """Drop the cached YAML. Only needed by tests and by the system check."""
    for cached in (_raw, vocabulary, order_keys, section_order, by_name):
        cached.cache_clear()
