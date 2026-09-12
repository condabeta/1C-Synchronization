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
| `suppliers` | Dekomo, SWG, Jazzway, Crystal, ViaSvet, Arlight, Salux, Svet NN |
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
| `supplier_pricing_rules` | Markup coefficients per supplier/category → retail price |
| `supplier_field_sync_config` | Which fields a supplier import may overwrite |
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
| `Штрих-код` | `barcode` |
| `ТН ВЭД` | `attributes_json.tnved` |
| `Закупочная цена` | `price` |
| `МРЦ/РРЦ` | `price_retail` |
| full offer | `raw_data_json` |

The HTML-XLS export widened from 26 columns to 170 on 09.09.2026, adding barcode
(95% filled, GTIN-13), `ТН ВЭД` (49% filled), `Закупочная цена`, activity and
marketplace-ban flags, document links and a dozen more photo slots. Barcode,
`ТН ВЭД` and `Закупочная цена` are mapped - the rest still land in
`attributes_json` as raw text. Before that export `price` could only be a copy of
the MRC; now it is the real purchase price, which is what `price` means for every
other supplier. Dekomo's own markup runs about x1.78. No displayed price moved:
`price_retail` is still the MRC, and Dekomo has no markup rule. `ТН ВЭД` uses the same `tnved` key Arlight does, so one query reads the
customs code across suppliers. Barcode is merged with `COALESCE`: re-importing an
older, narrower export must not erase a code a newer one supplied.

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

### Salux (distributor XLSX, 16 sheets)

Each sheet is a stack of blocks - group title, header, price sub-header, rows - so
columns are read per block rather than per sheet.

| Source field | Column |
|---|---|
| `Маркировка для заказа` / `Маркировка` | `supplier_sku`, `manufacturer_code` |
| `Наименование` | `name` |
| `Дистрибьютор` (4th price tier) | `price` |
| sheet + block title | `supplier_category_path` |
| all four price tiers, power, flux, size, mass | `attributes_json` |

The order marking is **not unique**: 102 rows share one with a different product
(same marking, different size and price). Colliding rows are split by the first
field that separates them - size, then name, then sheet - appended in brackets.
Blocks whose header has no marking column are option lists (dimming, IP upgrade,
extended warranty) and are skipped.

### Svet NN (XLSX)

One flat table per sheet. Resells Salux hardware, so `Маркировка` repeats the Salux
order markings - 259 positions overlap and will need merging in the catalogue.

| Source field | Column |
|---|---|
| `Артикул` | `supplier_sku` |
| `Наименование` | `name` |
| `Маркировка` | `manufacturer_code` (shared key with Salux) |
| `Категория` | `supplier_category` |
| `Цена для дилера` | `price` |
| `Цена для дилера*1,6` | not imported - used to verify the markup rule |


## Business rules

### New vs existing products

| Case | Action |
|---|---|
| New `supplier_sku` | Create `supplier_products` → create `products` → `moderation_queue` (`new_product`) |
| Existing SKU, price/stock only changed | Update `product_supplier_offers`; auto-update `products` if already `published` |
| Existing SKU, name/images/description changed | Flag `is_changed`; queue `major_change` for moderation |
| Same item from 2 suppliers | Link via `product_supplier_links`; dedupe by barcode or manual merge |

### Retail pricing

`price` is always what the supplier sent; `price_retail` is derived from it by the
markup rules in `supplier_pricing_rules`, applied at batch upsert time by
`staging/pricing.py`. Coefficients, category matching and the recalculation script
are documented in [../docs/pricing_rules.md](../docs/pricing_rules.md).

| Supplier | Rule |
|---|---|
| Salux | everything x1.6, on the «Дистрибьютор» tier |
| Svet NN | everything x1.6, on «Цена для дилера» (verified against the file's own x1.6 column) |
| LED Crystal | tape x2, everything else x1.5 |
| Jazzway | track x1.5, power supplies x1.5, lamps x1.35, luminaires x1.25, rest x1.25 |
| ViaSvet | RRC column verbatim, no markup |
| Dekomo, SWG, Arlight | no rules - price passes through unchanged |

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
