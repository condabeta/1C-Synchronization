# Staging DB Schema

Database: `svetoyar_staging` (MySQL/MariaDB, utf8mb4)

SQL file: `staging_schema.sql`

## Architecture

```
suppliers + supplier_sources
        ↓
   import_runs → supplier_products (raw per supplier)
        ↓
   match/merge → products + product_supplier_links
        ↓              + product_attributes + product_images
   moderation_queue (new / major changes)
        ↓
   approved products → sync_outbox → 1C Fresh → OpenCart
```

## Tables

| Table | Purpose |
|---|---|
| `suppliers` | Dekomo, SWG, Jazzway, Crystal, ViaSvet, Arlight, Salux |
| `supplier_sources` | File path, URL, parser, cron schedule per data type |
| `import_runs` | Each import batch with counts and errors |
| `import_run_errors` | Row-level import errors |
| `supplier_products` | Normalized rows from each supplier (before merge) |
| `categories` | Unified site category tree |
| `supplier_category_map` | Map supplier categories → unified categories |
| `products` | Master catalog (one row per sellable product) |
| `product_supplier_links` | Many suppliers → one product |
| `product_supplier_offers` | Per-supplier price/stock |
| `product_attributes` | Specs (IP, power, dimensions, etc.) |
| `product_images` | Main/gallery/scheme images |
| `moderation_queue` | Pre-publication review |
| `product_status_history` | Audit trail |
| `sku_aliases` | Normalize SKU variants (e.g. ViaSvet В→B) |
| `sync_outbox` | Queue for 1C and OpenCart sync |

## Supplier field mapping

### Dekomo (CommerceML / CSV / HTML-XLS)

| Source field | `supplier_products` column |
|---|---|
| `article` | `supplier_sku` |
| `model` | `name` / `model` |
| `price` | `price` |
| `oldprice` | `price_old` |
| `stock` / `stock_quantity` | `stock_qty` |
| `vendor` | `brand` |
| `picture`, `preview` | `images_json` |
| `param[@name]` | `attributes_json` |
| full offer | `raw_data_json` |

### SWG (YML URL)

| Source field | Column |
|---|---|
| `vendorCode` | `supplier_sku` |
| `model` | `name` |
| `price` | `price` |
| `categoryId` | `supplier_category_id` |
| `picture` | `images_json` (fallback: website parser) |
| `param` | `attributes_json` |
| `barcode` | `barcode` |

### Jazzway (XLSX price + YML content feed)

Two sources, joined on import. Neither alone is enough for a product card.

**Price XLSX** (header row 5, daily) — owns price and stock:

| Source field | Column |
|---|---|
| `Артикул` | `supplier_sku` |
| `Номенклатура` | `name` |
| `Остаток` | `stock_qty` |
| `Цена клиента` | `price` |
| `Ссылка на картинку` | `images_json` |
| `Ссылка на сайт` | `product_url` |

Import filter: skip rows without `Цена клиента` (~2845 product rows).

**Content YML** — `https://www.jazz-way.com/bitrix/catalog_export/export_all.xml`
(public, no auth, ~11 MB, 2098 offers, regenerated nightly). Owns description,
images, specs and document links:

| Source field | Column |
|---|---|
| `param[Код для заказа]` | join key -> XLSX `Артикул` without its leading dot |
| `description` | `description` (HTML) |
| `picture`, `param[pictureN]` | `images_json` (~13 400 URLs, avg 6/offer) |
| `param[*]` | `attributes_json` |
| `param[Штрих-код]` | `barcode` |
| `param[Артикул]` | `manufacturer_code` (the real Jazzway article) |
| `param[Документация (…)]` | `attributes_json.documents`, keyed by kind |
| `categoryId` -> `<categories>` | `supplier_category`, `supplier_category_path` |

Caveats:

- The feed has **no `vendorCode` and no `barcode` tag**; both live in `param`.
- Every offer is `<price>1</price>` — placeholder. Price comes from the XLSX only.
- Feed covers **1973 of 2845** priced rows; 872 import without description or specs.
- 169 `pictureN` params are the bare domain used as a "no image" placeholder and
  are filtered out.
- `Документация (Сертификат)` holds ~5940 certificate PDF links — the only
  machine-readable certificate source any supplier currently provides.

### LED Crystal (XLS price + site scrape)

**Price XLS** (header row 9, 9 sheets, manual):

| Source field | Column |
|---|---|
| Col 1 `Артикул` (LR1-R) | `supplier_sku` |
| Col 0 `Фото` | ignored (use scraper) |
| specs columns | `attributes_json` |
| `Цена` | `price` |
| — | `stock_qty` = NULL (manual) |

**Site scrape** — `staging/importers/crystal_site.py`, run with
`import_crystal.py --fetch-images`. Permission granted in writing 2026-09-05,
source credit required (`suppliers.content_attribution`).

The site is a uKit-style builder, not Bitrix: no `/search/`, no `/catalog/`
tree. Everything hangs off `sitemap.xml` as flat slugs, so the scraper crawls it
once (391 URLs, ~0.5 s apart) and answers lookups from memory.

| Source | Column |
|---|---|
| `<link itemprop="contentUrl">` | `images_json` |
| `og:description` | `description` |
| `<title>` | article codes for matching |
| page URL | `product_url` |

Matching and filtering rules:

- Product images are the only ones marked up as `schema.org/ImageObject`. Site
  chrome (logo, two banners) repeats on every page and carries no `contentUrl`.
- A page with no `contentUrl` is a category page — 35 of the 391.
- Every page falls back to a site-wide blurb starting "Официальный сайт LED
  CRYSTAL"; that is not a product description and is discarded.
- Titles are latinized **before** tokenizing: the site types Cyrillic lookalikes
  inside its own articles (`LC10S-ССT-01` has a Cyrillic `С`).
- Articles match exactly first, then with punctuation stripped
  (`LB114020--1-W` in the price list vs `LB114020-1-W` on the site). Both stages
  compare whole codes — substring matching would hand `-1-W` the page for
  `-1-WW`, a different colour temperature.
- A code claimed by more than one page stays unmatched. A wrong photo is worse
  than no photo.

Coverage: **290 of 325 priced SKUs** matched, 527 images, 290 descriptions. The
35 misses are mostly accessory kits (R39-01, R49-02, R53-04…) with no page.

### ViaSvet (multi-sheet XLSX + photo folders)

**Sheets:**

| Sheet | Product type | SKU pattern |
|---|---|---|
| Профили для LED ленты | Aluminum profile | SP251, SP251W, … |
| Профили для светильников | Profile | SP286–SP302 |
| КОМПЛЕКТУЮЩИЕ | Accessories | ENSP*, CLSP*, PSSP* |
| ЛЕНТА!!! | LED strip | VST-* |
| Блоки * / Трансформаторы | Power supplies | LE*, LEDIP*, LEYT* |

| Source field | Column |
|---|---|
| Article in name (SP251…) | `supplier_sku` (via `sku_aliases`) |
| Row text | `name` |
| `Доп. информация` | `kit_description` |
| `РРЦ` | `price_retail` |
| `Ваша цена` | `price` |
| `упаковка` | `attributes_json.packaging` |
| Photo folder `на сайт/{SKU}/` | `product_images` (local_file) |
| viasvet.ru | `description`, extra attrs |

**Scope for Svetoyar (confirm with client):** profiles first; accessories/tape/power optional.

## Business rules

### New vs existing products

| Case | Action |
|---|---|
| New `supplier_sku` | Create `supplier_products` → create `products` → `moderation_queue` (`new_product`) |
| Existing SKU, price/stock only changed | Update `product_supplier_offers`; auto-update `products` if already `published` |
| Existing SKU, name/images/description changed | Flag `is_changed`; queue `major_change` for moderation |
| Same item from 2 suppliers | Link via `product_supplier_links`; dedupe by barcode or manual merge |

### Product status flow

```
draft → pending_moderation → approved → synced_1c → published
                          ↘ rejected
                          ↘ archived
```

### Internal SKU strategy

Default: `{supplier_code}:{normalized_sku}` until merged, e.g. `dekomo:NN_411-123`.

After manual merge or barcode match: assign unified `internal_sku` (client's 1C article).

## Indexes and scale notes

- Dekomo ~150k rows: `supplier_products` indexed on `(supplier_id, supplier_sku)`
- Use `content_hash` to skip unchanged rows on re-import
- Store full payload in `raw_data_json` for debugging; normalize key fields to columns

## Next steps

1. Run `staging_schema.sql` on MySQL
2. Build importers: Dekomo → SWG → Jazzway → Crystal → ViaSvet
3. Admin UI: moderation queue + import log viewer
4. Phase 2: `sync_outbox` → 1C CommerceML
5. Phase 3: OpenCart product sync
