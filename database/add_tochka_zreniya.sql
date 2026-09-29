-- Точка Зрения as a supplier, and own articles that stop being unique.
--
-- Анна wrote on 29.09.2026: article 000001, "Светодиодный проектор Premier
-- MINI", is not Salux at all. It is Точка Зрения - a different supplier whose
-- price list she also keeps by hand, and whose articles run 000001 upwards just
-- as Salux's do. Minutes later she added SWG to the list: their hand-made
-- articles collide with both.
--
-- The site already knew this and we did not. On svetoyar.pro 127 six-digit
-- articles are carried by 276 products, told apart by manufacturer: 122 under
-- «Россия» (the Salux goods sold under our own article), 82 under «SWG», 72
-- under «Точка Зрения». Article 000006 alone is three different products.
--
-- So `own_articles.own_sku` was never a key. It was unique here only because we
-- had loaded one of the three lists. The constraint moves to (supplier, article)
-- and the site's manufacturer is recorded beside it, because that is the column
-- the site matches on and guessing from the article alone is what produced the
-- replacement file that would have renamed 288 other brands' products.

USE svetoyar_staging;

INSERT INTO suppliers (code, name, website, is_active, notes)
VALUES (
  'tochka_zreniya', 'Точка Зрения', 'https://www.optosvet.spb.ru', 1,
  'Светодиодные проекторы Premier и общие вводы. Прайс ведётся вручную, артикулы 000001 и далее — '
  'те же номера, что у Салюкса и SWG. На сайте отличаются производителем «Точка Зрения».'
)
ON DUPLICATE KEY UPDATE name = VALUES(name), website = VALUES(website), notes = VALUES(notes);

INSERT INTO supplier_sources (
  supplier_id, code, source_type, format, location, header_row, sku_field, config_json
)
SELECT s.id, v.code, v.source_type, v.format, v.location, v.header_row, v.sku_field, v.config_json
FROM suppliers s
JOIN (
  SELECT 'tochka_zreniya' AS supplier_code, 'price_xlsx' AS code, 'file' AS source_type,
         'xlsx' AS format, 'D:\\projects\\1C\\Точка Зрения.xlsx' AS location,
         2 AS header_row, 'Артикул' AS sku_field,
         JSON_OBJECT(
           'engine', 'openpyxl',
           'note', 'прислан Анной 29.09.2026, ведётся вручную',
           'site_manufacturer', 'Точка Зрения'
         ) AS config_json
) v ON v.supplier_code = s.code
ON DUPLICATE KEY UPDATE
  location = VALUES(location),
  header_row = VALUES(header_row),
  sku_field = VALUES(sku_field),
  config_json = VALUES(config_json);

-- The article is only unique within a supplier.
ALTER TABLE own_articles
  DROP INDEX uq_own_articles,
  ADD COLUMN site_manufacturer VARCHAR(255) NULL
      COMMENT 'производитель на сайте: «Россия», «SWG», «Точка Зрения» — по нему сайт их и различает'
      AFTER supplier_id,
  ADD UNIQUE KEY uq_own_articles (supplier_id, own_sku),
  ADD KEY idx_own_articles_manufacturer (site_manufacturer);

-- The rows loaded so far are the Salux ones, sold on the site under «Россия».
UPDATE own_articles oa
JOIN suppliers s ON s.id = oa.supplier_id AND s.code = 'salux'
SET oa.site_manufacturer = 'Россия'
WHERE oa.site_manufacturer IS NULL;
