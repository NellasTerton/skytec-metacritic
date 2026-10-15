# Metacritic: как доставать данные (проверено 2026-10-15)

Публичного API нет. Сайт — Nuxt (SSR), который ходит во внутренний JSON-backend
`https://backend.metacritic.com`. Этот backend отвечает без авторизации и без `apiKey`
(в HTML есть `apiKey=1MOZgmNFxvmljaQR1X9KAij9Mo4xAY3u`, но он не обязателен).
Перед backend стоят Cloudflare и CDN-кэш (`Cache-Control: max-age=600`).

## Эндпоинты

| Что | URL |
|---|---|
| New Releases с /game/ (20 шт.) | `/finder/metacritic/web?mcoTypeId=13&sortBy=-releaseDate&metaScoreMin=1&offset=0&limit=20` |
| SEE ALL → Newest | `/finder/metacritic/web?mcoTypeId=13&sortBy=-releaseDate&offset={o}&limit={≤50}` |
| Вся карточка игры одним запросом | `/composer/metacritic/pages/games/{slug}/web` |
| Только product | `/games/metacritic/{slug}/web` |
| Score по платформе | `/reviews/metacritic/{critic\|user}/games/{slug}/platform/{platformSlug}/stats/web` |
| Список отзывов | `/reviews/metacritic/{critic\|user}/games/{slug}/platform/{platformSlug}/web?offset=0&limit=50&filterBySentiment=all\|positive\|neutral\|negative&sort=date\|score` |
| Справочник фильтров (жанры, platform id) | `/finder/metacritic/filters/games/web` |
| Обложка | `https://www.metacritic.com/a/img/catalog{image.bucketPath}` |

Список New Releases на /game/ совпадает с первым запросом из таблицы (проверено в браузере:
те же 20 игр).

### Что лежит в composer (`components[].meta.componentName`)
- `product` → `data.item`: `title`, `slug`, `description`, `releaseDate`, `genres[]`, `rating`,
  `images[]` (`typeName`: `cardImage` = обложка, `mainImage` = баннер),
  `production.companies[]` (`typeName == "Developer"`),
  `video` (`embedUrl`/`manifestUrl` у JW Player, часто `null`),
  `platforms[]` (`name`, `slug`, `isLeadPlatform`, `criticScoreSummary.score`).
- `critic-score-summary`, `user-score-summary`: оценки только для **lead-платформы**.
- `critic-reviews`, `user-reviews` → `item.{default,positive,neutral,negative}`: до 7 отзывов
  в каждой корзине. Уже готовая сбалансированная выборка для LLM-резюме.
- `game-stats`: распределение оценок, время прохождения, сложность (бонусные данные).

## Подводные камни

1. **User-Agent.** С `Python-urllib` backend отвечает 403. С `python-requests` 403 отдают
   страницы `www`. Везде ставить браузерный UA.
2. **Нестабильная пагинация.** Внутри одной даты релиза порядок не детерминирован: страницы
   `offset=0` и `offset=24` пересекаются. Для 2026-10-15 (`releaseDateMin=2026-10-15`,
   132 игры) полный обход всех страниц дал только 98–109 уникальных slug, и число менялось
   от прогона к прогону. Нужны дедуп по slug и курсор вида «листать, пока не набрали
   20 новых», а не «страница N».
3. `limit` у finder не больше 50 (на 51 уже 400). `sortBy` принимает только
   `±releaseDate`, `-metaScore`, `-userScore`, `-popularityCount`; составные сортировки
   не поддерживаются. `releaseDateMax` игнорируется.
4. **`?platform=` на stats игнорируется**, всегда приходит lead-платформа. Платформа
   задаётся только в пути: `/platform/{slug}/`.
5. **Отзывы критиков отдаются по 10 за запрос**, какой бы `limit` ни передать. Пользовательские
   — по 50. Текст отзыва критика — только цитата (~340 символов) и ссылка на источник.
6. **SEE ALL — в основном «пустые» игры**: 181k позиций, у первых страниц нет ни
   Metascore, ни отзывов, ни видео. New Releases, наоборот, только со score
   (`metaScoreMin=1`). Нужен статус «мало отзывов», и на пустые игры LLM не вызывать.
7. В выдаче finder у игры только одна платформа. Полный список платформ — только в `product`.
8. Идентификаторы: у игры есть family/title/game id, и game id свой для каждой платформы.
   Id платформы PC в отзывах критиков (1533000068) отличается от id в product (1500000019).
   Ключ в БД — `slug`.
9. Userscore `0` при `reviewCount 0` на сайте означает «tbd», а не ноль. Нормализовать в `null`.
10. Ресайз картинок `/a/img/resize/...` без подписи отдаёт 403. Брать оригинал по `bucketPath`.
11. Backend неофициальный и может поменяться. Весь доступ держать в одном адаптере;
    запасной вариант — парсить `<script id="__NUXT_DATA__">` из HTML.
12. Нагрузка: 60 параллельных некэшированных запросов за 3,2 с прошли без 429. Наш
    объём (~20 игр × 5–10 запросов в час) тривиальный, но всё равно стоит ограничить
    параллелизм и делать retry с backoff.
13. **composer на несуществующую игру отвечает HTTP 200**: 404 лежит внутри компонента
    (`components[product].status == 404`). Остальные эндпоинты (`/games/...`, `/reviews/...`)
    отвечают честным 404.
14. Поле `platforms[].criticScoreSummary.score` бывает `null` при `reviewCount` 1–3: пока отзывов
    меньше четырёх, Metascore не публикуется.
15. robots.txt запрещает для `*` только `/search`, `/user`, `/login` и т. п.;
    `/game/` и `/browse/` разрешены.
