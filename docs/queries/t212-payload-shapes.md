# Trading212 payload shapes: checked against the published spec and real accounts

**Status (2026-11-08).** Checked two ways: the published OpenAPI spec (`https://docs.trading212.com/_bundle/api.json`)
and a read-only run against a real Invest and a real Stocks ISA account (field names, types, currency codes and
reconciliation only; no amounts were recorded).

Confirmed:
- All paths, Basic auth, and the position, summary and instrument field names below.
- Positions and instruments are plain lists; history endpoints return `{items, nextPagePath}`.
- London ETFs are reported with instrument currency exactly `GBX`; `quantity * currentPrice` is 100 times
  `walletImpact.currentValue` for them, so prices are in pence. USD holdings carry the GBP value in
  `walletImpact.currentValue`.
- Order history items are `{order: {...}, fill: {...}}`; the id is `order.id` (not top level). Dividends and
  transactions use `reference`.
- Positions plus cash reconcile with `totalValue` for both accounts.
- The fixed satellite (BRK.B) is recognised by the normalised symbol match.

Still open:
- Both accounts had zero cash when checked, so it is not yet shown whether `totalValue` includes cash. The
  reconciliation sums all cash buckets; recheck with an account that holds cash.
- The positions request sends `limit=50`, which the spec does not list for that endpoint (it was accepted).
- The spec documents the instruments endpoint at one request per 50 seconds; a run now fetches it once for all accounts.

---

# Trading212 payload field names assumed by ingestion

The Trading212 docs list endpoint groups but not full response schemas, so the field names below
are assumptions. All parsing lives in `src/guard/ingest/t212.py`; the fixtures in
`tests/fixtures/t212/` were authored from these assumptions, not captured. Check each against the
demo host (`uv run pytest -m live tests/integration/ingest`, after removing the skip) and correct
the parser and fixtures together if any differ.

Auth: HTTP Basic, key as username and secret as password. Base URL `https://live.trading212.com/api/v0`
or `https://demo.trading212.com/api/v0`. Secrets: `guard_t212_key_<account_id>`, `guard_t212_secret_<account_id>`.

## Paths (GET only, relative to the base URL)

| Purpose | Path |
|---|---|
| Positions | `/equity/positions?limit=50` |
| Account summary and cash | `/equity/account/summary` |
| Instruments metadata | `/equity/metadata/instruments` |
| Orders | `/equity/history/orders?limit=50` |
| Dividends | `/equity/history/dividends?limit=50` |
| Transactions | `/equity/history/transactions?limit=50` |

Open: whether positions and instruments are paginated at all (the client accepts a bare list or
`{items, nextPagePath}` for every endpoint), and whether the history paths carry the `/equity/` segment.

## Pagination and rate limits

- Paged body: `{"items": [...], "nextPagePath": "/api/v0/..." | null}`. `nextPagePath` is followed as given.
- Rate limit headers read: `Retry-After` (seconds, on 429), `x-ratelimit-remaining`, `x-ratelimit-reset`
  (assumed to be an epoch-seconds timestamp).

## Instruments metadata (list of objects)

`ticker`, `isin`, `currencyCode` (`GBX` for pence-quoted London lines, `GBP`, `USD`, ...), `name`,
`shortName` (matched against configured fixed satellites such as BRK.B; the part of `ticker` before
the first underscore is also compared).

## Positions (items)

- `instrument.ticker` (fallback: top-level `ticker`), `instrument.name`, `instrument.isin` and
  `instrument.currency` (the last two are only fallbacks when the metadata lacks them)
- `quantity`, `currentPrice` (in the instrument currency; pence when the currency is `GBX`)
- `walletImpact.currency` and `walletImpact.currentValue`: the position value in account currency,
  required only for instruments not quoted in GBP or GBX

GBP and GBX lines are valued as quantity times price (divided by 100 for GBX). Other currencies use
`walletImpact.currentValue`, which must be in GBP.

## Account summary (object)

`currency` (must be `GBP`), `totalValue`, and `cash` as an object of numbers
(`availableToTrade`, `inPies`, `reservedForOrders`). Cash is the sum of every number in `cash`.
Positions plus cash must equal `totalValue` within `data.crosscheck_tolerance` of the total.

## History items

| Kind | Unique id field |
|---|---|
| Orders | `id` |
| Dividends | `reference` |
| Transactions | `reference` |

Items are stored whole as JSON. A missing id field stops the run. Decimals are stored as strings.

## Numbers

Every numeric field may arrive as a JSON number or a string; both parse. JSON numbers are read as
`Decimal` from their text, not through binary floats.
