-- Retail price calculation from per-supplier markup rules.
--
-- Suppliers send their own price. The catalogue needs a retail price, and the
-- client sets the markup per supplier and, for some of them, per category:
--
--   Salux / Svet NN  - everything x 1.6
--   LED Crystal      - LED tape x 2, everything else x 1.5
--   Jazzway          - track lights x 1.5, power supplies x 1.5, lamps x 1.35,
--                      luminaires x 1.25, everything else x 1.25
--   ViaSvet          - the RRC column of the price file, verbatim, no markup
--
-- Rules live here rather than in code so changing a coefficient is a row edit
-- plus scripts/recalc_prices.py, not a deploy.

USE svetoyar_staging;

CREATE TABLE IF NOT EXISTS supplier_pricing_rules (
  id            INT UNSIGNED    NOT NULL AUTO_INCREMENT,
  supplier_id   INT UNSIGNED    NOT NULL,
  rule_name     VARCHAR(128)    NOT NULL COMMENT 'human label shown in reports',
  match_type    ENUM('all', 'category', 'name') NOT NULL DEFAULT 'category'
                COMMENT 'all = supplier fallback; category/name = substring test',
  match_value   VARCHAR(255)    NOT NULL DEFAULT ''
                COMMENT 'case-insensitive substring, empty for match_type=all',
  base_field    ENUM('price', 'price_retail') NOT NULL DEFAULT 'price'
                COMMENT 'which supplier column the coefficient multiplies',
  coefficient   DECIMAL(8,4)    NOT NULL DEFAULT 1.0000,
  round_to      DECIMAL(8,2)    NULL
                COMMENT 'round to nearest multiple: 1.00 = whole rubles; NULL = kopecks',
  priority      SMALLINT        NOT NULL DEFAULT 100
                COMMENT 'lower wins first; a fallback rule always loses to a matching one',
  is_active     TINYINT(1)      NOT NULL DEFAULT 1,
  notes         VARCHAR(255)    NULL,
  created_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_supplier_pricing_rules (supplier_id, match_type, match_value),
  KEY idx_supplier_pricing_rules_lookup (supplier_id, is_active, priority),
  CONSTRAINT fk_supplier_pricing_rules_supplier
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Which rule produced the current price_retail. Lets a report answer
-- "why is this product priced like that" without re-running the engine.
ALTER TABLE supplier_products
  ADD COLUMN pricing_rule_id INT UNSIGNED NULL
    COMMENT 'markup rule applied to price_retail on last import',
  ADD CONSTRAINT fk_supplier_products_pricing_rule
    FOREIGN KEY (pricing_rule_id) REFERENCES supplier_pricing_rules(id)
    ON DELETE SET NULL ON UPDATE CASCADE;

-- -----------------------------------------------------------------------------
-- Seed: the coefficients the client confirmed
-- -----------------------------------------------------------------------------

INSERT INTO supplier_pricing_rules
  (supplier_id, rule_name, match_type, match_value, base_field, coefficient, round_to, priority, notes)
SELECT s.id, v.rule_name, v.match_type, v.match_value, v.base_field, v.coefficient, 1.00, v.priority, v.notes
FROM suppliers s
JOIN (
  -- Salux / Svet NN: one coefficient for the whole price list.
  SELECT 'salux' AS supplier_code, 'Все товары x1.6' AS rule_name, 'all' AS match_type,
         '' AS match_value, 'price' AS base_field, 1.6000 AS coefficient, 100 AS priority,
         'Салюкс / Свет НН, наценка на весь прайс' AS notes
  UNION ALL

  -- LED Crystal: sheet name is the category, and the tape sheet is its own rate.
  SELECT 'crystal', 'Светодиодная лента x2', 'category', 'светодиодные лент', 'price', 2.0000, 10,
         'Лист прайса "СВЕТОДИОДНЫЕ ЛЕНТЫ"'
  UNION ALL
  SELECT 'crystal', 'Остальные категории x1.5', 'all', '', 'price', 1.5000, 100,
         NULL
  UNION ALL

  -- Jazzway: category comes from the section headers of the price file.
  -- Track systems sit *under* luminaires, so they need the lower priority number.
  SELECT 'jazzway', 'Трековые светильники x1.5', 'category', 'трековые', 'price', 1.5000, 10,
         'Разделы "Трековые системы"'
  UNION ALL
  SELECT 'jazzway', 'Блоки питания x1.5', 'category', 'блок питания', 'price', 1.5000, 10,
         NULL
  UNION ALL
  SELECT 'jazzway', 'БП для ленты x1.5', 'category', 'бп,', 'price', 1.5000, 15,
         'Раздел "4.6. БП, контроллеры, акс-ры для светодиодной ленты"'
  UNION ALL
  SELECT 'jazzway', 'Лампы x1.5', 'category', 'ламп', 'price', 1.5000, 30,
         'Разделы "Лампы", "Настольные светодиодные лампы"'
  UNION ALL
  SELECT 'jazzway', 'Светильники x1.5', 'category', 'светильник', 'price', 1.5000, 50,
         NULL
  UNION ALL
  SELECT 'jazzway', 'Остальные категории x1.5', 'all', '', 'price', 1.5000, 100,
         NULL
  UNION ALL

  -- ViaSvet: the price file already carries the retail column, taken as is.
  SELECT 'viasvet', 'РРЦ из прайса, без наценки', 'all', '', 'price_retail', 1.0000, 100,
         'Колонка РРЦ в Excel-прайсе'
) v ON v.supplier_code = s.code;
