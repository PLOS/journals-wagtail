### Wagtail for PLOS Content

to run:

```console
pip install -r requirements.txt
python manage.py migrate
```

For creating a superuser
`python manage.py createsuperuser`

and to run

`python manage.py runserver`

### Exisiting CMS structure

#### Homepages Authoring CMS
Production homepage content is hosted at `journals.plos.org/{journal_slug}`, i.e. [PLOS One](https://journals.plos.org/plosone/).


#### Sitecontent Authoring CMS
Similarly, ancillary journal information is hosted at `/{journal_slug}/s/{article_slug}`, i.e.
[PLOS One data availability policy](https://journals.plos.org/plosone/s/data-availability). Note that other journals share similar content [PLOS Water data availability policy](https://journals.plos.org/water/s/data-availability).


#### Article Admin volumes and issues CMS
Volumes and Issues (which only certain journals use) can be viewed at `/{journal_slug}/volume` i.e [PLOS Medicine volumes](https://journals.plos.org/plosmedicine/volume) and an example issue would be [PLOS Medicinge March 2021](https://journals.plos.org/plosmedicine/issue?id=10.1371/issue.pmed.v18.i03)

---

## The `journals` app — volumes and issues

Implements the volumes/issues design through the static build (milestones 1–4).
Publishing to S3/CloudFront is deliberately **not** included.

### Quick start

```console
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo_content          # two journals, volumes, issues, covers, articles, homepages
python manage.py setup_journal_permissions  # per-journal editor groups and image collections
python manage.py createsuperuser
python manage.py runserver
```

Then:

* `/admin/` — the editor experience (page tree, homepage editor, issue editor, article snippets)
* `/plosmedicine/` — the journal homepage
* `/plosmedicine/volume/` — the archive
* `/plosmedicine/volume/2021/february/` — an issue

The demo content is synthetic and needs no network access: article metadata is
filled in locally and marked as synced so the build works offline.

### Building the static site

```console
python manage.py sync_article_metadata --attached-only --fail-on-missing  # build gate
python manage.py build                    # → build/
python manage.py check_build_links        # every internal link resolves?
python manage.py buildserver              # check the built output at :8000
```

`build` clears `build/` first, so a deleted issue disappears from the output —
that matters because publication is two-way and a static server will happily
keep serving a page the CMS no longer knows about.

For a staging build that includes unpublished pages — the replacement for the
old admin's "on Staging" / "on Production" toggle:

```console
python manage.py build wagtailbakery.views.AllPagesView
```

### What the build produces

| Output | Path |
|---|---|
| Journal homepage | `plosmedicine/index.html` |
| Archive | `plosmedicine/volume/index.html` |
| Volume | `plosmedicine/volume/2021/index.html` |
| Issue | `plosmedicine/volume/2021/february/index.html` |
| DOI alias stub | `plosmedicine/issue/10.1371/issue.pmed.v18.i02/index.html` |
| Legacy `?id=` shim | `plosmedicine/issue/index.html` |
| Shim lookup table | `plosmedicine/issue-map.json` |
| Sitemap | `sitemap.xml` |

Human-readable paths are canonical. DOI paths are permanent aliases. The `?id=`
shim is transitional — it is instrumented so its retirement can be driven by
measured traffic, and it is `noindex` so it never competes with the canonical
pages.

### Management commands

| Command | What it is for |
|---|---|
| `seed_demo_content [--reset]` | Clone-and-run demo data: journals, volumes, issues, generated placeholder covers and a populated homepage per journal |
| `setup_journal_permissions` | Per-journal editor group scoped to that journal's subtree, plus an image Collection |
| `sync_article_metadata` | Cache titles/authors/types/dates from `api.plos.org/search` into `Article` snippets |
| `report_unknown_article_types` | Article types seen in the data but missing from the vocabulary or from a journal's order |
| `delete_orphan_articles` | Remove `Article` snippets no issue references any more |
| `build` | `django-bakery` build, plus a media re-copy so lazily-created image renditions are included |
| `check_build_links` | Assert every internal link in the build resolves and the build looks complete |
| `generate_benchmark_content` | Synthetic content at 16-journal scale, for timing a full rebuild |

`sync_article_metadata` is the only command that touches the network. The build
never does.

### Article types

Section vocabulary and per-journal TOC order live in version control rather than
the database, because placement is an editorial decision that wants a code
review:

* `journals/config/article_types.yaml` — the shared vocabulary and default order
* `journals/config/article_types.<journal_key>.yaml` — a journal's complete
  ordered list (see `article_types.pmed.yaml`)

Unknown types fall into an "Other" section at the bottom, so nothing ever
silently disappears from a table of contents. A Django system check
(`journals.E001`) validates every key in every order list at startup, so a typo
in an override fails immediately rather than mid-build.

### Tests

```console
python manage.py test journals
```

Covers DOI derivation and tree-position validation, TOC grouping and ordering
(including the DOI tiebreak that keeps builds byte-stable), the bulk DOI paste,
current-issue resolution across journals, the buildable views, the search-API
field mapping, the `--fail-on-missing` gate, and the homepage cards — snippet
fallback, editor overrides, external links, the hero's image and the region
caps.

---

## The journal homepage CMS

The homepage authoring tool, rebuilt on the page tree. The homepage for a
journal *is* the `JournalPage` at `/<journal_slug>/` — edit it at
**Pages → PLOS Journals → PLOS Medicine**.

### Regions

| Region | Slots | Rendered as |
|---|---|---|
| Hero | 1 | Full-width image with the headline laid over it. The image is required. |
| Billboard | 1 | Banner: heading, description, "Read more". |
| Tier 1 | 4 | Cards |
| Tier 2 | 3 | Cards |
| Tier 3 | 6 | Cards |

### Slots hold one of two things

Every region is a StreamField of the same two block types, so the "+" menu in
any region offers the same choice:

* **Article** — an `Article` snippet, chosen by DOI, title or author. The card
  reads the cached metadata for its headline, subject, authors and date, so a
  card that is only a DOI still renders once `sync_article_metadata` has run.
  Any of it can be overridden per card: a homepage headline is often shorter
  than the article's own title.
* **URL** — a link to anywhere else: a collection, a blog post, a policy page.
  The headline is required here, because there is no snippet to fall back to.

Both kinds also carry a teaser, an image and an image credit, and both expose
the same properties to templates (`journals/blocks.py`), so one card template
renders either.

New DOIs are added under **Snippets → Articles**; the edit view warns when a
homepage card points at an article whose metadata has not been synced yet,
because such a card renders its DOI where a headline should be.

### What the old tool did that Wagtail already does

The legacy tool keeps a list of dated homepages, each with a draft/live dot, a
publish button, a preview pane and a "mark as completed" checkbox. None of that
is reimplemented here, because a homepage is not a new document every day — it
is a new *version* of the page at the same URL:

| Old homepage tool | Here |
|---|---|
| Dated homepage list | Page revision history |
| Draft/live dot, Publish button | Wagtail's status and publish |
| A homepage dated in the future | `go_live_at` scheduled publishing |
| Preview pane beside the editor | The live preview panel |
| "Mark as completed" | Workflows and moderation, if wanted |

The one consequence worth stating: reverting is `Revisions → revert`, and the
homepage's history is the page's history rather than a row per day.
