# Svetoyar supplier staging

Collects the price lists and product feeds of Svetoyar's lighting suppliers into
one MySQL staging database, cleans them up, applies the markup rules, and puts
new or changed products through moderation before they reach the catalogue.

The end goal is a single approved catalogue that is synchronised to 1C Fresh
and the OpenCart site. The staging, pricing and moderation parts are built; the
1C / OpenCart sync is not yet (see [Status](#status)).

```
supplier files / feeds
        │  scripts/import_*.py
        ▼
import_runs ──► supplier_products        raw rows, one set per supplier
        │         price        = what the supplier sent
        │         price_retail = price × markup rule
        ▼
products + product_supplier_links        merged catalogue
        │
moderation_queue ──► web UI              review before publishing
        │
sync_outbox ──► 1C Fresh ──► OpenCart    (not built yet)
```

## Suppliers

| Code | Supplier | Source | Script |
|---|---|---|---|
| `dekomo` | Декомо | HTML export saved as `.xls` (~190k rows, parsed as a stream) | `import_dekomo.py` |
| `arlight` | Арлайт | `products.xml` + Excel price with stock | `import_arlight.py` |
| `swg` | SWG | YML feed (private URL) | `import_swg.py` |
| `jazzway` | Jazzway | Daily stock/price XLSX + public YML feed for content | `import_jazzway.py` |
| `salux` | Салюкс | Distributor price XLSX, four price tiers | `import_salux.py` |
| `crystal` | LED Crystal | Price XLS, one sheet per category | `import_crystal.py` |
| `viasvet` | ViaSvet | Multi-sheet XLSX; stock shown as cell colour | `import_viasvet.py` |

Svetoyar's own articles and names for Salux-made goods are not a supplier feed;
they are loaded into `own_articles` by `import_own_articles.py`.

## Requirements

- Python 3.11+
- MySQL 8+ (developed on MySQL 9.6) or MariaDB, utf8mb4
- Windows paths are used in the defaults, but nothing is Windows-specific

```
pip install -r requirements.txt
```

## Setup

1. **Configure.** Copy `config.env.example` to `config.env` and fill in the
   MySQL credentials and the paths to the supplier files.
   `config.env` is gitignored and must stay that way: `SWG_YML_URL` contains a
   private token that gives access to SWG's full price feed. Get it from the
   project owner.

2. **Create the database** and the base schema:

   ```
   python scripts/setup_database.py
   ```

   This also applies the migrations. On an existing database, apply any new
   ones with:

   ```
   python scripts/migrate.py --status
   python scripts/migrate.py
   ```

   `schema_migrations` records what has run, with a checksum, so a migration is
   never applied twice and an edited one is reported instead of re-run.

## Usage

### Importing

Every importer accepts `--dry-run` (parse only, write nothing) and `--file` to
override the configured path. Re-running an import is safe: rows are matched by
supplier article and only changed rows are rewritten.

```
python scripts/import_jazzway.py --dry-run
python scripts/import_jazzway.py
python scripts/check_import_status.py      # last run and its counts
python scripts/supplier_report.py          # data quality per supplier
```

A failed batch is retried row by row, so one bad row is logged to
`import_run_errors` instead of stopping the run.

### Prices

The markup rules are stored in the `supplier_pricing_rules` table and applied during
every import. After changing a coefficient, recalculate the prices already loaded:

```
python scripts/recalc_prices.py --supplier jazzway --list-categories
python scripts/recalc_prices.py --supplier jazzway --dry-run
python scripts/recalc_prices.py --supplier jazzway
```

The current rules and how a rule is chosen for a product are in
[docs/pricing_rules.md](docs/pricing_rules.md).

### Categories

The site's own category tree, and the rules mapping each supplier's sections
into it, are in `staging/categories.py`:

```
python scripts/build_categories.py --dry-run
python scripts/build_categories.py --unmapped
```

### Catalogue prices

Imports write supplier rows; this pushes the figures a supplier owns through to
the catalogue, leaving fields a manager has edited alone:

```
python scripts/refresh_catalogue.py --dry-run
python scripts/refresh_catalogue.py
```

A price that moved by more than 5x is reported rather than applied.

### Publishing to 1C

```
python scripts/prepare_publishing.py            # slugs, GUIDs, meta titles
python scripts/prepare_publishing.py --export   # write import.xml / offers.xml
```

1C connects to `/1c_exchange` on our side; see
[docs/publishing.md](docs/publishing.md).

### Certificates

Loads the conformity documents (declarations, certificates, refusal letters)
from the Arlight, Jazzway, LED Crystal and Salux registries and links them to
products:

```
python scripts/import_certificates.py --dry-run
python scripts/import_certificates.py
```

See [docs/certificates.md](docs/certificates.md).

### Supplier site content

Salux and LED Crystal publish photos and descriptions only on their own sites.
Salux's are scraped per series and applied to every article of that series:

```
python scripts/import_salux_content.py --dry-run
python scripts/import_salux_content.py
```

Who has agreed to what is recorded in
[docs/supplier_permissions.md](docs/supplier_permissions.md).

### Moderation

```
python scripts/build_moderation_queue.py --supplier viasvet
python scripts/bulk_moderate.py --supplier jazzway --approve-complete --dry-run
python scripts/run_moderation_ui.py          # http://127.0.0.1:8080
```

The web UI shows the review queue, individual product reviews, and
**Discrepancies**. A discrepancy is a supplier value that was not applied
because a manager had edited that field. It waits there for the manager to
accept or reject.
Which fields each supplier may overwrite is described in
[docs/supplier_permissions.md](docs/supplier_permissions.md).

## Project layout

```
staging/
  config.py            settings and default file paths (from config.env)
  db.py                connection helpers, SQL file runner
  pricing.py           markup rules → retail price
  certificates.py      registry loaders and document ↔ product matching
  own_articles.py      Svetoyar's own articles for Salux goods
  importers/           one module per supplier + common.py (shared upsert)
  moderation/          matching, moderation queue, field discrepancies
  web/                 Flask moderation UI
scripts/               command-line entry points
database/              schema and migrations (see STAGING_SCHEMA.md)
docs/                  pricing, certificates, supplier permissions
```

## Status

Done:

- Imports for all seven suppliers.
- Markup rules.
- Stock tracking.
- Certificates and registry links.
- Moderation UI and field discrepancies.
- Detection of products that dropped out of a supplier's latest price list
  (`last_import_run_id`).

- Category tree, and every product placed in it.
- Slugs and 1C GUIDs for the whole catalogue.
- CommerceML export and the exchange endpoint 1C connects to.

Not built yet:

- **Loading a catalogue back from 1C.** The endpoint accepts and keeps the
  file; whether to read it in depends on which side owns the catalogue, which
  the client has not decided.
- **OpenCart export.** Needs the site access to build against.
- **Images are hotlinks.** All 459,134 `product_images.stored_path` are empty -
  nothing has been downloaded, so the catalogue's pictures depend on the
  suppliers' own servers.
- **Orders.** No orders or customers tables; `type=sale` is refused.
- **Retired products.** What happens to products a supplier stops listing
  (hide them automatically, or flag them for a manager) is still undecided.
- **Salux mapping.** Our own articles are not yet applied to Salux products,
  because some markings match more than one product.
