-- Maytoni as a supplier in its own right.
--
-- Their goods reached us only through Dekomo until now. Анна asked for them to
-- be separated (25.09.2026): they buy Maytoni direct, and the direct price
-- differs from Dekomo's. She sent the address of their price list, which turns
-- out to cover seven brands - Maytoni, Technical, Outdoor, Ledstrip, Lighting
-- control, Freya and Voltega - about 7,500 articles.
--
-- The files are worth more than the prices: each row carries a barcode, and the
-- workbook has a photograph embedded against every product. Those are the two
-- things the catalogue is short of - Dekomo's image links are dead and only
-- three suppliers give us GTINs at all.

USE svetoyar_staging;

INSERT INTO suppliers (code, name, website, is_active, notes)
VALUES (
  'maytoni', 'Maytoni', 'https://maytoni.ru', 1,
  'Прямой прайс РРЦ: https://shared.maytoni.ru/files/PRICE_RRC/ - семь файлов по брендам. '
  'Цены РРЦ, в файлах есть штрихкоды и фотографии. Те же товары приходят и от Декомо; '
  'приоритет у прямого прайса.'
)
ON DUPLICATE KEY UPDATE name = VALUES(name), website = VALUES(website), notes = VALUES(notes);
