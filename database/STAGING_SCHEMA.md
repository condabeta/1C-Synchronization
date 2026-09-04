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

### Jazzway (XLSX, header row 5)

| Source field | Column |
|---|---|
| `Артикул` | `supplier_sku` |
| `Номенклатура` | `name` |
| `Остаток` | `stock_qty` |
| `Цена клиента` | `price` |
| `Ссылка на картинку` | `images_json` |
| `Ссылка на сайт` | `product_url` |

Import filter: skip rows without `Цена клиента` (~2845 product rows).

### LED Crystal (XLS, header row 9)

| Source field | Column |
|---|---|
| Col 1 `Артикул` (LR1-R) | `supplier_sku` |
| Col 0 `Фото` | ignored (use parser) |
| specs columns | `attributes_json` |
| `Цена` | `price` |
| — | `stock_qty` = NULL (manual) |
| led-crystal.ru parser | `description`, `images_json` |

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
