-- Salux and Svet NN: price sources and markup rules.
--
-- Both price lists arrived on 2026-09-08. The client had listed them together
-- as "Салюкс / Свет НН - цена из прайса x 1,6", and the files show why: Svet NN
-- resells Salux hardware, and its "Маркировка" column repeats the same
-- ССдВз/ССдС/ССдП order markings the Salux workbook uses. They are still two
-- separate suppliers with two separate price lists, so each gets its own row.
--
-- Svet NN's file carries a "Цена для дилера*1,6" column, which is the client's
-- coefficient stated by the supplier. We compute retail from the dealer price
-- and use that column only to check the result.
--
-- Both files are named .pdf but are XLSX workbooks. The importers name the
-- openpyxl engine explicitly rather than trusting the extension.

USE svetoyar_staging;

INSERT INTO suppliers (code, name, website, notes) VALUES
  ('svetnn', 'Свет НН', NULL,
   'XLSX price list, dealer price plus the supplier own x1.6 column. Resells Salux hardware - order markings overlap')
ON DUPLICATE KEY UPDATE
  name = VALUES(name),
  notes = VALUES(notes);

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
         'D:\\projects\\1C\\Салюкс\\Прайс_лист_Дистрибьютор_Июнь_2026_Салюкс.pdf' AS location,
         NULL AS header_row, 'Маркировка для заказа' AS sku_field,
         JSON_OBJECT(
           'engine', 'openpyxl',
           'note', 'file is XLSX despite the .pdf extension',
           'layout', 'blocks: group title, header, price sub-header, rows',
           'price_tiers', JSON_ARRAY('Розница', 'Опт', 'Дилер', 'Дистрибьютор'),
           'base_tier', 'Дистрибьютор'
         ) AS config_json
  UNION ALL
  SELECT 'svetnn', 'price_xlsx', 'file', 'xlsx',
         'D:\\projects\\1C\\Виа Свет\\светнн1.pdf',
         0, 'Артикул',
         JSON_OBJECT(
           'engine', 'openpyxl',
           'note', 'file is XLSX despite the .pdf extension, stored in the ViaSvet folder',
           'base_column', 'Цена для дилера',
           'verify_column', 'Цена для дилера*1,6'
         )
) v ON v.supplier_code = s.code
ON DUPLICATE KEY UPDATE
  location = VALUES(location),
  sku_field = VALUES(sku_field),
  config_json = VALUES(config_json);

-- Markup: x1.6 on the whole price list, the same coefficient as Salux.
INSERT INTO supplier_pricing_rules
  (supplier_id, rule_name, match_type, match_value, base_field, coefficient, priority, notes)
SELECT id, 'Все товары x1.6', 'all', '', 'price', 1.6000, 100,
       'Подтверждено колонкой "Цена для дилера*1,6" в прайсе'
FROM suppliers WHERE code = 'svetnn'
ON DUPLICATE KEY UPDATE
  coefficient = VALUES(coefficient),
  notes = VALUES(notes);
