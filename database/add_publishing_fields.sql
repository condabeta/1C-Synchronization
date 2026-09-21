-- Fields the two publishing targets need, which nothing in staging had.
--
-- 1C Fresh УНФ speaks CommerceML, where every object is identified by a GUID
-- («Ид») that must stay the same for the life of the object - lose it and 1C
-- creates a second copy of the whole catalogue on the next exchange. So the
-- GUID is stored here, generated once, rather than made up at export time.
--
-- OpenCart needs a URL for every product and category, and a meta title it
-- cannot be NULL. Names come from suppliers and repeat heavily (5,294 groups of
-- products share a name), so a slug has to be made unique deliberately; that is
-- why it is a column with a unique key rather than something computed on the fly.
--
-- VAT is per product because the rate differs by goods and the whole catalogue
-- cannot be assumed to be 20%; it is left NULL until the client states it, and
-- the export refuses to send a product without one rather than guessing.

USE svetoyar_staging;

ALTER TABLE products
  ADD COLUMN slug             VARCHAR(255) NULL COMMENT 'URL on the site, unique, transliterated from the name'
    AFTER internal_sku,
  ADD COLUMN meta_title       VARCHAR(255) NULL AFTER slug,
  ADD COLUMN meta_description VARCHAR(512) NULL AFTER meta_title,
  ADD COLUMN vat_rate         DECIMAL(5,2) NULL COMMENT 'per cent; NULL = not stated by the client yet'
    AFTER currency,
  ADD COLUMN okei_code        VARCHAR(8)   NULL COMMENT 'unit code 1C expects, 796 = шт' AFTER unit,
  ADD UNIQUE KEY uq_products_slug (slug);

ALTER TABLE categories
  ADD COLUMN onec_guid        CHAR(36)     NULL COMMENT 'CommerceML Ид of the group' AFTER slug,
  ADD COLUMN meta_title       VARCHAR(255) NULL AFTER name,
  ADD COLUMN meta_description VARCHAR(512) NULL AFTER meta_title,
  ADD UNIQUE KEY uq_categories_onec_guid (onec_guid);

-- Price types are a CommerceML object of their own: an offer states which type
-- each price is. Two to begin with - what we sell at, and what we paid.
CREATE TABLE IF NOT EXISTS price_types (
  id          INT UNSIGNED NOT NULL AUTO_INCREMENT,
  code        VARCHAR(32)  NOT NULL COMMENT 'retail, purchase',
  name        VARCHAR(128) NOT NULL,
  onec_guid   CHAR(36)     NOT NULL,
  source_field VARCHAR(32) NOT NULL COMMENT 'which products column this reads',
  currency    CHAR(3)      NOT NULL DEFAULT 'RUB',
  is_active   TINYINT(1)   NOT NULL DEFAULT 1,
  PRIMARY KEY (id),
  UNIQUE KEY uq_price_types_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO price_types (code, name, onec_guid, source_field)
VALUES
  ('retail',   'Розничная',  'b1f0a7c4-1d2e-4a3b-9c5d-0e6f7a8b9c01', 'price_retail'),
  ('purchase', 'Закупочная', 'b1f0a7c4-1d2e-4a3b-9c5d-0e6f7a8b9c02', 'price')
ON DUPLICATE KEY UPDATE name = VALUES(name);

-- What has been sent where, so an exchange can resume rather than start over,
-- and so a failed send is visible instead of silently retried for ever.
CREATE TABLE IF NOT EXISTS sync_sessions (
  id            INT UNSIGNED  NOT NULL AUTO_INCREMENT,
  target_system ENUM('onec', 'opencart') NOT NULL,
  direction     ENUM('out', 'in') NOT NULL,
  status        ENUM('running', 'success', 'failed') NOT NULL DEFAULT 'running',
  products      INT UNSIGNED  NOT NULL DEFAULT 0,
  files         INT UNSIGNED  NOT NULL DEFAULT 0,
  bytes_total   BIGINT UNSIGNED NOT NULL DEFAULT 0,
  started_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at   DATETIME      NULL,
  error         TEXT          NULL,
  notes         VARCHAR(512)  NULL,
  PRIMARY KEY (id),
  KEY idx_sync_sessions_target (target_system, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
