-- =============================================================================
-- Staging database schema for Svetoyar supplier import pipeline
-- OpenCart 2.0.2.0 + 1C Fresh (UNF) via CommerceML
--
-- Flow:
--   supplier feed/file/parser -> import_run -> supplier_products
--   -> match/merge -> products (+ images, attributes)
--   -> moderation (new/changed) -> approved -> 1C -> OpenCart
-- =============================================================================

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

CREATE DATABASE IF NOT EXISTS svetoyar_staging
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE svetoyar_staging;

-- -----------------------------------------------------------------------------
-- Reference: suppliers and import sources
-- -----------------------------------------------------------------------------

CREATE TABLE suppliers (
  id              INT UNSIGNED NOT NULL AUTO_INCREMENT,
  code            VARCHAR(32)  NOT NULL COMMENT 'dekomo, swg, jazzway, crystal, viasvet, arlight, salux',
  name            VARCHAR(128) NOT NULL,
  website         VARCHAR(255) NULL,
  contact_email   VARCHAR(255) NULL,
  is_active       TINYINT(1)   NOT NULL DEFAULT 1,
  notes           TEXT         NULL,
  created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_suppliers_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE supplier_sources (
  id              INT UNSIGNED NOT NULL AUTO_INCREMENT,
  supplier_id     INT UNSIGNED NOT NULL,
  code            VARCHAR(64)  NOT NULL COMMENT 'price_xml, price_csv, stock_xlsx, photos_folder, website_parser',
  source_type     ENUM('file', 'url', 'email', 'folder', 'parser') NOT NULL,
  format          ENUM('commerceml', 'yml', 'csv', 'xlsx', 'xls', 'html_xls', 'json', 'images') NOT NULL,
  location        TEXT         NULL COMMENT 'file path, URL, or parser key',
  sheet_name      VARCHAR(128) NULL COMMENT 'Excel sheet name if applicable',
  header_row      SMALLINT UNSIGNED NULL,
  sku_field       VARCHAR(64)  NULL COMMENT 'column/tag name for supplier SKU',
  schedule_cron   VARCHAR(64)  NULL COMMENT 'e.g. 0 6 * * * for daily 06:00',
  is_active       TINYINT(1)   NOT NULL DEFAULT 1,
  config_json     JSON         NULL COMMENT 'parser options, column mapping overrides',
  last_success_at DATETIME     NULL,
  last_error      TEXT         NULL,
  created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_supplier_sources (supplier_id, code),
  KEY idx_supplier_sources_active (supplier_id, is_active),
  CONSTRAINT fk_supplier_sources_supplier
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Seed suppliers (based on current project data)
INSERT INTO suppliers (code, name, website, notes) VALUES
  ('dekomo',   'Декомо',       'https://www.dekomo.ru',   'CommerceML XML/CSV/XLS, ~150k SKU, full data'),
  ('swg',      'SWG Shop',     'https://swgshop.ru',      'YML URL export, ~3361 offers'),
  ('jazzway',  'Jazz-Way',     'https://jazz-way.com',    'Daily XLSX stock/price + image URLs'),
  ('crystal',  'LED Crystal',  'https://led-crystal.ru',  'Manual XLS price, images/descriptions from site'),
  ('viasvet',  'ViaSvet',      'https://www.viasvet.ru',  'Multi-sheet XLSX + local photo folders, site allowed'),
  ('arlight',  'Arlight',      'https://arlight.ru',      'Pending supplier feed'),
  ('salux',    'STZ Salux',    'https://stz-salux.ru',    'Pending supplier feed, manual prices expected');

INSERT INTO supplier_sources (supplier_id, code, source_type, format, location, sheet_name, header_row, sku_field, schedule_cron, config_json)
SELECT s.id, v.code, v.source_type, v.format, v.location, v.sheet_name, v.header_row, v.sku_field, v.schedule_cron, v.config_json
FROM suppliers s
JOIN (
  SELECT 'dekomo' AS supplier_code, 'price_xls' AS code, 'file' AS source_type, 'html_xls' AS format,
         'D:\\projects\\1C\\Декомо\\content_12_08_2026_12_12.xls' AS location,
         NULL AS sheet_name, 0 AS header_row, 'Артикул' AS sku_field, '0 6 * * *' AS schedule_cron,
         JSON_OBJECT('also_available', JSON_ARRAY('csv', 'commerceml_xml')) AS config_json
  UNION ALL
  SELECT 'dekomo', 'price_xml', 'file', 'commerceml',
         'D:\\projects\\1C\\Декомо\\content_12_08_2026_12_11.xml', NULL, NULL, 'article', '0 6 * * *', NULL
  UNION ALL
  SELECT 'swg', 'yml_export', 'url', 'yml',
         'https://go.swg.ru/public_api/export/69d8c94368119bec81a1cc9f', NULL, NULL, 'vendorCode', '0 7 * * *',
         JSON_OBJECT('images_fallback', 'website_parser') AS config_json
  UNION ALL
  SELECT 'jazzway', 'stock_xlsx', 'file', 'xlsx',
         'D:\\projects\\1C\\Джазвея\\11.08 Остатки для клиента.xlsx', NULL, 5, 'Артикул', '0 8 * * *',
         JSON_OBJECT('import_filter', 'price_not_null') AS config_json
  UNION ALL
  SELECT 'crystal', 'price_xls', 'file', 'xls',
         'D:\\projects\\1C\\crystal\\ПРАЙС LEDCRYSTAL от 05.08.2026.xls', NULL, 9, 'Артикул', NULL,
         JSON_OBJECT('images_from', 'parser:led-crystal.ru', 'stock_from', 'manual') AS config_json
  UNION ALL
  SELECT 'viasvet', 'price_xlsx', 'file', 'xlsx',
         'D:\\projects\\1C\\Виа Свет\\ViaSvet_led_профиль_блоки_питания_лента_ПОСТУПЛЕНИЕ5.xlsx',
         'Профили для LED ленты', 3, 'SP', NULL,
         JSON_OBJECT('sheets', JSON_ARRAY('Профили для LED ленты', 'Профили для светильников', 'КОМПЛЕКТУЮЩИЕ', 'ЛЕНТА!!!')) AS config_json
  UNION ALL
  SELECT 'viasvet', 'photos_folder', 'folder', 'images',
         'D:\\projects\\1C\\Виа Свет\\на сайт', NULL, NULL, 'folder_name', NULL,
         JSON_OBJECT('sku_normalize', JSON_OBJECT('cyrillic_v', 'latin_b')) AS config_json
  UNION ALL
  SELECT 'viasvet', 'website_parser', 'parser', 'json', 'viasvet.ru', NULL, NULL, 'article', '0 9 * * 0', NULL
) v ON v.supplier_code = s.code;

-- -----------------------------------------------------------------------------
-- Import runs and raw supplier rows
-- -----------------------------------------------------------------------------

CREATE TABLE import_runs (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  supplier_id     INT UNSIGNED    NOT NULL,
  source_id       INT UNSIGNED    NULL,
  status          ENUM('running', 'success', 'partial', 'failed') NOT NULL DEFAULT 'running',
  trigger_type    ENUM('manual', 'cron', 'upload') NOT NULL DEFAULT 'manual',
  source_file     VARCHAR(512)    NULL,
  source_hash     CHAR(64)        NULL COMMENT 'SHA-256 of source file/content',
  rows_total      INT UNSIGNED    NOT NULL DEFAULT 0,
  rows_imported   INT UNSIGNED    NOT NULL DEFAULT 0,
  rows_updated    INT UNSIGNED    NOT NULL DEFAULT 0,
  rows_skipped    INT UNSIGNED    NOT NULL DEFAULT 0,
  rows_errors     INT UNSIGNED    NOT NULL DEFAULT 0,
  started_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at     DATETIME        NULL,
  error_summary   TEXT            NULL,
  meta_json       JSON            NULL,
  PRIMARY KEY (id),
  KEY idx_import_runs_supplier (supplier_id, started_at),
  KEY idx_import_runs_status (status, started_at),
  CONSTRAINT fk_import_runs_supplier
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_import_runs_source
    FOREIGN KEY (source_id) REFERENCES supplier_sources(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE import_run_errors (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  import_run_id   BIGINT UNSIGNED NOT NULL,
  source_row_number INT UNSIGNED    NULL,
  supplier_sku    VARCHAR(128)    NULL,
  error_code      VARCHAR(64)     NULL,
  error_message   TEXT            NOT NULL,
  raw_fragment    JSON            NULL,
  created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_import_run_errors_run (import_run_id),
  CONSTRAINT fk_import_run_errors_run
    FOREIGN KEY (import_run_id) REFERENCES import_runs(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE supplier_products (
  id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  supplier_id         INT UNSIGNED    NOT NULL,
  import_run_id       BIGINT UNSIGNED NULL,
  supplier_sku        VARCHAR(128)    NOT NULL COMMENT 'normalized supplier article',
  supplier_sku_raw    VARCHAR(128)    NULL COMMENT 'as in source file before normalization',
  name                VARCHAR(512)    NULL,
  brand               VARCHAR(128)    NULL,
  model               VARCHAR(512)    NULL,
  description         MEDIUMTEXT      NULL,
  supplier_category   VARCHAR(512)    NULL,
  supplier_category_path VARCHAR(1024) NULL,
  price               DECIMAL(12,4)   NULL,
  price_old           DECIMAL(12,4)   NULL,
  price_retail        DECIMAL(12,4)   NULL COMMENT 'MRC/RRC if separate from purchase price',
  currency            CHAR(3)         NOT NULL DEFAULT 'RUB',
  stock_qty           DECIMAL(12,3)   NULL,
  unit                VARCHAR(32)     NULL DEFAULT 'шт',
  min_order_qty       DECIMAL(12,3)   NULL,
  is_available        TINYINT(1)      NULL,
  product_url         VARCHAR(1024)   NULL,
  barcode             VARCHAR(64)     NULL,
  manufacturer_code   VARCHAR(128)    NULL,
  kit_description     TEXT            NULL COMMENT 'ViaSvet: what is included in set',
  attributes_json     JSON            NULL COMMENT 'all supplier-specific params',
  images_json         JSON            NULL COMMENT 'array of URLs or local paths from source',
  raw_data_json       JSON            NULL COMMENT 'full original row/offer payload',
  content_hash        CHAR(64)        NULL COMMENT 'hash for change detection',
  product_id          BIGINT UNSIGNED NULL COMMENT 'linked unified product after matching',
  match_status        ENUM('unmatched', 'matched', 'duplicate', 'ignored') NOT NULL DEFAULT 'unmatched',
  match_confidence    TINYINT UNSIGNED NULL COMMENT '0-100',
  is_new              TINYINT(1)      NOT NULL DEFAULT 1,
  is_changed          TINYINT(1)      NOT NULL DEFAULT 0,
  first_seen_at       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_import_run_id  BIGINT UNSIGNED NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_supplier_products (supplier_id, supplier_sku),
  KEY idx_supplier_products_product (product_id),
  KEY idx_supplier_products_match (match_status, is_new, is_changed),
  KEY idx_supplier_products_import (import_run_id),
  CONSTRAINT fk_supplier_products_supplier
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_supplier_products_import_run
    FOREIGN KEY (import_run_id) REFERENCES import_runs(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- Unified catalog (master products)
-- -----------------------------------------------------------------------------

CREATE TABLE categories (
  id              INT UNSIGNED NOT NULL AUTO_INCREMENT,
  parent_id       INT UNSIGNED NULL,
  name            VARCHAR(255) NOT NULL,
  slug            VARCHAR(255) NULL,
  path            VARCHAR(1024) NULL COMMENT 'materialized path e.g. /svet/profil/',
  sort_order      INT          NOT NULL DEFAULT 0,
  is_active       TINYINT(1)   NOT NULL DEFAULT 1,
  created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_categories_parent (parent_id),
  CONSTRAINT fk_categories_parent
    FOREIGN KEY (parent_id) REFERENCES categories(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE supplier_category_map (
  id                  INT UNSIGNED NOT NULL AUTO_INCREMENT,
  supplier_id         INT UNSIGNED NOT NULL,
  supplier_category   VARCHAR(512) NOT NULL,
  supplier_category_id VARCHAR(128) NULL,
  category_id         INT UNSIGNED NOT NULL,
  created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_supplier_category_map (supplier_id, supplier_category(191), supplier_category_id),
  CONSTRAINT fk_supplier_category_map_supplier
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_supplier_category_map_category
    FOREIGN KEY (category_id) REFERENCES categories(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE products (
  id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  internal_sku        VARCHAR(128)    NOT NULL COMMENT 'our master SKU/article',
  name                VARCHAR(512)    NOT NULL,
  brand               VARCHAR(128)    NULL,
  model               VARCHAR(512)    NULL,
  description         MEDIUMTEXT      NULL,
  short_description   TEXT            NULL,
  category_id         INT UNSIGNED    NULL,
  status              ENUM(
                        'draft',
                        'pending_moderation',
                        'approved',
                        'rejected',
                        'synced_1c',
                        'published',
                        'archived'
                      ) NOT NULL DEFAULT 'draft',
  moderation_required TINYINT(1)      NOT NULL DEFAULT 1,
  primary_supplier_id INT UNSIGNED    NULL COMMENT 'preferred supplier for content',
  unit                VARCHAR(32)     NOT NULL DEFAULT 'шт',
  weight              DECIMAL(10,3)   NULL,
  barcode             VARCHAR(64)     NULL,
  -- Pricing (effective values after business rules)
  price               DECIMAL(12,4)   NULL,
  price_old           DECIMAL(12,4)   NULL,
  price_retail        DECIMAL(12,4)   NULL,
  currency            CHAR(3)         NOT NULL DEFAULT 'RUB',
  stock_qty           DECIMAL(12,3)   NULL DEFAULT 0,
  is_available        TINYINT(1)      NOT NULL DEFAULT 0,
  -- External system IDs
  opencart_product_id INT UNSIGNED    NULL,
  onec_guid           CHAR(36)        NULL,
  onec_code           VARCHAR(64)     NULL,
  -- Sync flags
  sync_1c_status      ENUM('none', 'pending', 'synced', 'error') NOT NULL DEFAULT 'none',
  sync_opencart_status ENUM('none', 'pending', 'synced', 'error') NOT NULL DEFAULT 'none',
  last_sync_1c_at     DATETIME        NULL,
  last_sync_opencart_at DATETIME      NULL,
  content_hash        CHAR(64)        NULL,
  created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_products_internal_sku (internal_sku),
  KEY idx_products_status (status, moderation_required),
  KEY idx_products_category (category_id),
  KEY idx_products_opencart (opencart_product_id),
  KEY idx_products_onec (onec_guid),
  CONSTRAINT fk_products_category
    FOREIGN KEY (category_id) REFERENCES categories(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_products_primary_supplier
    FOREIGN KEY (primary_supplier_id) REFERENCES suppliers(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE supplier_products
  ADD CONSTRAINT fk_supplier_products_product
    FOREIGN KEY (product_id) REFERENCES products(id)
    ON DELETE SET NULL ON UPDATE CASCADE;

CREATE TABLE product_supplier_links (
  id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  product_id          BIGINT UNSIGNED NOT NULL,
  supplier_id         INT UNSIGNED    NOT NULL,
  supplier_product_id BIGINT UNSIGNED NOT NULL,
  supplier_sku        VARCHAR(128)    NOT NULL,
  link_type           ENUM('primary', 'alternate', 'merged') NOT NULL DEFAULT 'alternate',
  is_active           TINYINT(1)      NOT NULL DEFAULT 1,
  created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_product_supplier_links (product_id, supplier_id, supplier_sku),
  KEY idx_product_supplier_links_supplier (supplier_id, supplier_sku),
  CONSTRAINT fk_product_supplier_links_product
    FOREIGN KEY (product_id) REFERENCES products(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_product_supplier_links_supplier
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_product_supplier_links_supplier_product
    FOREIGN KEY (supplier_product_id) REFERENCES supplier_products(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE product_attributes (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  product_id      BIGINT UNSIGNED NOT NULL,
  attr_group      VARCHAR(64)     NULL COMMENT 'e.g. electrical, dimensions',
  attr_name       VARCHAR(128)    NOT NULL,
  attr_value      TEXT            NOT NULL,
  unit            VARCHAR(32)     NULL,
  sort_order      INT             NOT NULL DEFAULT 0,
  source_supplier_id INT UNSIGNED NULL,
  created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_product_attributes (product_id, attr_name(100)),
  KEY idx_product_attributes_product (product_id),
  CONSTRAINT fk_product_attributes_product
    FOREIGN KEY (product_id) REFERENCES products(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_product_attributes_supplier
    FOREIGN KEY (source_supplier_id) REFERENCES suppliers(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE product_images (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  product_id      BIGINT UNSIGNED NOT NULL,
  supplier_id     INT UNSIGNED    NULL,
  image_type      ENUM('main', 'gallery', 'scheme', 'preview') NOT NULL DEFAULT 'gallery',
  source_type     ENUM('url', 'local_file', 'downloaded') NOT NULL,
  source_path     VARCHAR(1024)   NOT NULL COMMENT 'URL or local path',
  stored_path     VARCHAR(1024)   NULL COMMENT 'path after download/processing',
  file_hash       CHAR(64)        NULL,
  width           INT UNSIGNED    NULL,
  height          INT UNSIGNED    NULL,
  file_size       INT UNSIGNED    NULL,
  sort_order      INT             NOT NULL DEFAULT 0,
  is_active       TINYINT(1)      NOT NULL DEFAULT 1,
  quality_note    VARCHAR(255)    NULL COMMENT 'low quality, scheme only, etc.',
  created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_product_images_product (product_id, sort_order),
  KEY idx_product_images_hash (file_hash),
  CONSTRAINT fk_product_images_product
    FOREIGN KEY (product_id) REFERENCES products(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_product_images_supplier
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Per-supplier price/stock snapshots (when multiple suppliers sell same product)
CREATE TABLE product_supplier_offers (
  id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  product_id          BIGINT UNSIGNED NOT NULL,
  supplier_id         INT UNSIGNED    NOT NULL,
  supplier_product_id BIGINT UNSIGNED NULL,
  supplier_sku        VARCHAR(128)    NOT NULL,
  price               DECIMAL(12,4)   NULL,
  price_retail        DECIMAL(12,4)   NULL,
  stock_qty           DECIMAL(12,3)   NULL,
  currency            CHAR(3)         NOT NULL DEFAULT 'RUB',
  is_available        TINYINT(1)      NULL,
  imported_at         DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_product_supplier_offers (product_id, supplier_id),
  KEY idx_product_supplier_offers_supplier (supplier_id, supplier_sku),
  CONSTRAINT fk_product_supplier_offers_product
    FOREIGN KEY (product_id) REFERENCES products(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_product_supplier_links_supplier2
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_product_supplier_offers_sp
    FOREIGN KEY (supplier_product_id) REFERENCES supplier_products(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- Moderation
-- -----------------------------------------------------------------------------

CREATE TABLE moderation_queue (
  id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  product_id          BIGINT UNSIGNED NOT NULL,
  supplier_product_id BIGINT UNSIGNED NULL,
  import_run_id       BIGINT UNSIGNED NULL,
  queue_reason        ENUM(
                        'new_product',
                        'major_change',
                        'missing_images',
                        'missing_description',
                        'price_anomaly',
                        'duplicate_suspect',
                        'manual'
                      ) NOT NULL,
  priority            TINYINT UNSIGNED NOT NULL DEFAULT 5 COMMENT '1=highest',
  status              ENUM('pending', 'in_review', 'approved', 'rejected') NOT NULL DEFAULT 'pending',
  reviewer            VARCHAR(128)    NULL,
  review_notes        TEXT            NULL,
  diff_json           JSON            NULL COMMENT 'what changed vs last approved version',
  created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  reviewed_at         DATETIME        NULL,
  PRIMARY KEY (id),
  KEY idx_moderation_queue_status (status, priority, created_at),
  KEY idx_moderation_queue_product (product_id),
  CONSTRAINT fk_moderation_queue_product
    FOREIGN KEY (product_id) REFERENCES products(id)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT fk_moderation_queue_supplier_product
    FOREIGN KEY (supplier_product_id) REFERENCES supplier_products(id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_moderation_queue_import_run
    FOREIGN KEY (import_run_id) REFERENCES import_runs(id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE product_status_history (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  product_id      BIGINT UNSIGNED NOT NULL,
  old_status      VARCHAR(32)     NULL,
  new_status      VARCHAR(32)     NOT NULL,
  changed_by      VARCHAR(128)    NULL,
  reason          TEXT            NULL,
  created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_product_status_history_product (product_id, created_at),
  CONSTRAINT fk_product_status_history_product
    FOREIGN KEY (product_id) REFERENCES products(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -----------------------------------------------------------------------------
-- SKU normalization and duplicate detection
-- -----------------------------------------------------------------------------

CREATE TABLE sku_aliases (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  supplier_id     INT UNSIGNED    NULL COMMENT 'NULL = global alias',
  alias_sku       VARCHAR(128)    NOT NULL,
  canonical_sku   VARCHAR(128)    NOT NULL,
  notes           VARCHAR(255)    NULL COMMENT 'e.g. Cyrillic V -> Latin B for ViaSvet',
  created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_sku_aliases (supplier_id, alias_sku),
  CONSTRAINT fk_sku_aliases_supplier
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ViaSvet: SP251В (cyrillic) -> SP251B (latin)
INSERT INTO sku_aliases (supplier_id, alias_sku, canonical_sku, notes)
SELECT s.id, a.alias_sku, a.canonical_sku, a.notes
FROM suppliers s
CROSS JOIN (
  SELECT 'SP251В' AS alias_sku, 'SP251B' AS canonical_sku, 'Cyrillic V in price, Latin B in photo folder' AS notes
  UNION ALL SELECT 'SP251В2', 'SP251B2', 'Cyrillic V in price'
  UNION ALL SELECT 'SP259В', 'SP259B', 'Cyrillic V in price'
  UNION ALL SELECT 'SP262В', 'SP262B', 'Black variant naming'
  UNION ALL SELECT 'SP280В', 'SP280B', 'Black variant naming'
) a
WHERE s.code = 'viasvet';

-- -----------------------------------------------------------------------------
-- Sync outbox (1C + OpenCart) — prepared for phase 2/3
-- -----------------------------------------------------------------------------

CREATE TABLE sync_outbox (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  entity_type     ENUM('product', 'price', 'stock', 'order', 'customer') NOT NULL,
  entity_id       BIGINT UNSIGNED NOT NULL,
  target_system   ENUM('onec', 'opencart') NOT NULL,
  action          ENUM('create', 'update', 'delete', 'export') NOT NULL,
  payload_json    JSON            NULL,
  status          ENUM('pending', 'processing', 'done', 'error') NOT NULL DEFAULT 'pending',
  attempts        TINYINT UNSIGNED NOT NULL DEFAULT 0,
  last_error      TEXT            NULL,
  created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  processed_at    DATETIME        NULL,
  PRIMARY KEY (id),
  KEY idx_sync_outbox_pending (status, target_system, created_at),
  KEY idx_sync_outbox_entity (entity_type, entity_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET FOREIGN_KEY_CHECKS = 1;
