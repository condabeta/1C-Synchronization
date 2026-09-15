-- Salux: price source and markup rule.
--
-- The price list arrived on 2026-09-08 misnamed .pdf and was renamed to .xlsx
-- on 2026-09-12. The importer names the openpyxl engine explicitly rather than
-- trusting the extension.
--
-- A second file that arrived with it was first read as a supplier named
-- "Свет НН". It is not one - see database/add_own_articles.sql.

USE svetoyar_staging;

UPDATE suppliers
SET notes = 'Distributor XLSX, 16 sheets of blocks, four price tiers. Order marking is the SKU and is not unique'
WHERE code = 'salux';

INSERT INTO supplier_sources (
  supplier_id, code, source_type, format, location, header_row, sku_field, config_json
)
SELECT s.id, v.code, v.source_type, v.format, v.location, v.header_row, v.sku_field, v.config_json
FROM suppliers s
JOIN (
  SELECT 'salux' AS supplier_code, 'price_xlsx' AS code, 'file' AS source_type, 'xlsx' AS format,
         'D:\\projects\\1C\\Салюкс\\Прайс_лист_Дистрибьютор_Июнь_2026_Салюкс.xlsx' AS location,
         NULL AS header_row, 'Маркировка для заказа' AS sku_field,
         JSON_OBJECT(
           'engine', 'openpyxl',
           'note', 'arrived misnamed .pdf, renamed to .xlsx 2026-09-12',
           'layout', 'blocks: group title, header, price sub-header, rows',
           'price_tiers', JSON_ARRAY('Розница', 'Опт', 'Дилер', 'Дистрибьютор'),
           'base_tier', 'Дистрибьютор'
         ) AS config_json
) v ON v.supplier_code = s.code
ON DUPLICATE KEY UPDATE
  location = VALUES(location),
  sku_field = VALUES(sku_field),
  config_json = VALUES(config_json);
