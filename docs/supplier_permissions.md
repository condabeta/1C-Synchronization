# Content permissions per supplier

Who has agreed to what, for photos and descriptions used on the Svetoyar site.
Keep this current - it is the record of what we are allowed to publish.

| Supplier | Site scraping | Attribution required | Source |
|---|---|---|---|
| LED Crystal | **Yes, granted** | **Yes, requested** | Email reply, 5 Sep 2026 |
| ViaSvet | Assumed, unconfirmed | unknown | needs written confirmation |
| STZ Salux | **Client instructed it** | unknown | Юлия, 21 Sep 2026: site is their only format |
| Dekomo | n/a - data comes from their export | — | |
| Arlight | n/a - data comes from their XML | — | |
| SWG | n/a - data comes from their YML | — | |
| Jazzway | n/a - public YML feed | — | |

## LED Crystal (led-crystal.ru)

Asked three questions on 5 Sep 2026: (1) can they supply a catalogue file,
(2) may we take photos and descriptions from their public pages, (3) any wishes
about crediting the source. Their reply:

> Вы можете воспользоваться вариантом номер 2 (возражений нет),
> По 3 ему пункту желательно.

So: **scraping is permitted, and they would like the source credited.** Any
product card built from their pages must carry a visible credit to LED CRYSTAL.

Technical notes for the scraper:

- `robots.txt` allows crawling; only `/html/`, `/widgets/`, `/sitesearch*`,
  `/about$` and `/__404$` are disallowed.
- `sitemap.xml` lists **391 URLs** with flat product paths - our price file has
  ~325 SKUs, so coverage should be near complete.
- Product pages carry the article in `<title>` / `og:title`, the description in
  `og:description`, and images under `/uploads/s/z/b/d/zbdhlacpzeoh/img/`.
- The existing `staging/importers/crystal_site.py` targets a Bitrix-style site
  (`/search/?q=`, `/catalog/`) that led-crystal.ru is not. It needs rewriting
  around the sitemap before it will return anything.
- Crawl politely: their catalogue is small, one pass with a delay is enough.

## ViaSvet (viasvet.ru)

Their price list has no descriptions, so the site is the only source. We have
never had this in writing - worth asking in the same three-question form that
worked for LED Crystal.

## STZ Salux (stz-salux.ru)

Salux exports no photos and no descriptions, and has no format in which to send
them. On 21 Sep 2026 Юлия said to take them from the site, which is what
`staging/importers/salux_site.py` does.

**Salux themselves have not been asked.** The client is their distributor and
gave the instruction, but a written yes from Salux is still worth having, the
way LED Crystal gave one. Every scraped card carries
«Фото и описание — СТЗ «САЛЮКС» (stz-salux.ru)» in `suppliers.content_attribution`,
so what came from them can be found and removed if they object.

Technical notes:

- `robots.txt` allows it: the disallow list is `/bitrix/`, `index.php` and
  sorting, printing and login parameters, none of which the crawler touches.
- Pages are found by walking `/products/` into its sections. `sitemap.xml` was
  last written in 2024 and misses eleven pages, including the Гранит, Оникс and
  Сегмент series.
- The site publishes **one page per series**, not per article: 56 pages, 54
  series, 228 photos. A series page covers every wattage in that series.
- The crawl is one pass with a 0.5 s delay - about a minute for the catalogue.
