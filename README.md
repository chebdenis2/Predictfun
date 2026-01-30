# Predict.fun autonomous trader bot

This repository contains a Python bot that watches Predict.fun markets and uses
Binance spot data to estimate short-term probability for BTC/ETH 15-min Up/Down
markets. Trades are executed only after explicit approval.

## Strategy interpretation

The bot treats "implied odds" as the Predict.fun YES price (probability). It
derives a model probability from Binance spot momentum and (optionally) the
distance between spot and the market strike:

```
return_lookback = (close_now / close_prev) - 1
p_mom = clamp(0.5 + k * return_lookback, 0, 1)

spot_diff = (spot - strike) / strike
p_spot = clamp(0.5 + spot_sensitivity * spot_diff, 0, 1)

p_model = w_spot * p_spot + (1 - w_spot) * p_mom
(if strike is unavailable, p_model = p_mom)
```

The bot also accounts for fees and spread. A trade is proposed only if the
edge clears:

```
required_edge = EDGE_THRESHOLD + (fee_rate_bps/10000)*FEE_EDGE_MULTIPLIER + spread/2
```

If `p_model - p_market >= required_edge` the bot proposes a YES trade. If
`p_market - p_model >= required_edge` it proposes a NO trade.

## Strategy mode

Set `STRATEGY_MODE` in `.env`:

- `edge` (default): directional edge strategy.
- `farm`: two-sided bid/ask farming strategy (see `prompt2.md`).

Farm settings (defaults in `.env.example`):

- `FARM_MIN_SPREAD` / `FARM_MAX_SPREAD`: target bid/ask spread.
- `FARM_MIN_HOLD_SEC`: minimum time an order stays live (>= 300s).
- `FARM_MIN_VOLUME_USD` / `FARM_MAX_VOLUME_USD`: liquidity filter.
- `FARM_ORDER_USD`: per-side order size (used if min/max not set).
- `FARM_ORDER_USD_MIN` / `FARM_ORDER_USD_MAX`: randomize per-order size.
- `FARM_MAX_INVENTORY_USD`: cap on per-market inventory (USD).
- `FARM_ALLOW_POSITION_ADD`: allow adding to an existing position (default false).
- `FARM_ORDER_EXPIRY_MINUTES` / `FARM_ORDER_EXPIRY_MAX_MINUTES`: randomize limit expiry.
- `FARM_STOP_LOSS_PCT`: stop-loss threshold (e.g. 0.30 = -30%).
- `FARM_STOP_LOSS_EXPIRY_MINUTES`: stop-loss order expiry (minutes).
- `FARM_MAX_OPEN_MARKETS`: cap on markets farmed simultaneously.
- `FARM_AVOID_MINUTES` / `FARM_MAX_VOLATILITY`: avoid high volatility near expiry.
- `FARM_TOP_LEVELS`: ensure orders stay within top-5 levels.

For farming across all instruments, set:
```
STRATEGY_MODE=farm
ALLOWED_SYMBOLS=*
ALLOWED_RESOLUTIONS=
```
The farm mode ignores symbol/resolution filters and only applies status/kind +
liquidity/volatility constraints.

## Quick start

1. Install dependencies:

```
python -m pip install -r requirements.txt
```

2. Configure environment variables (see `.env.example`).
3. Run the bot:

```
python -m predictfun_bot run
```

Preview lines look like:

```
[a1b2c3d4] market=... symbol=BTCUSDT side=YES size_usd=7.50 p_market=0.4800 ...
```

Approve by writing to `~/clawd-approvals.txt`:

```
approve a1b2c3d4
```

To trade fully automatically, set:
```
APPROVAL_MODE=auto
```

## Authentication

Predict.fun requires an **API key** on mainnet and a **JWT token** for any
personal operations (orders/positions). The bot can fetch a JWT automatically:

- `PREDICTFUN_AUTH_MODE=predict` (default): uses the **Privy Wallet private key**
  and the **Predict account (deposit) address** from your profile settings.
- `PREDICTFUN_AUTH_MODE=eoa`: uses a standard EOA private key.
- Or provide `PREDICTFUN_JWT` to skip JWT fetching.

The bot signs `/v1/auth/message` and exchanges it for a token via `/v1/auth`,
following the official developer docs.

If you are on **testnet**, the API key is not required (per docs). For mainnet
you must set `PREDICTFUN_API_KEY` (header defaults to `x-api-key`).

## Commands

```
python -m predictfun_bot once        # single loop
python -m predictfun_bot run         # continuous loop
python -m predictfun_bot report      # append daily P&L report
python -m predictfun_bot candidates  # print top 3 candidates
```

## Logs

Trades are logged as JSON lines to `~/clawd-trades.log`. Daily P&L summaries
are appended to `~/clawd-pnl.log` (UTC).

Enable verbose runtime logs with:
```
VERBOSE_LOGS=true
```

To log every rejection reason:
```
LOG_REJECTIONS=true
```

For farming, the bot places **both bid and ask** within top-5 orderbook levels
and keeps them open for at least `FARM_MIN_HOLD_SEC` seconds. It favors markets
with 50-150k volume and avoids high volatility near expiry.

Auto-relax mode (optional): if no orders can be placed, the bot can temporarily
relax spread bounds:
```
FARM_AUTO_RELAX=true
FARM_RELAX_MIN_SPREAD=0.002
FARM_RELAX_MAX_SPREAD=0.01
```

## Pricing model (orderbook)

Predict.fun orderbooks are **YES-side only**. The bot derives NO prices using
the complement at the market’s decimal precision:

```
yes_ask = asks[0][0]
yes_bid = bids[0][0]
no_ask = complement(yes_bid)
no_bid = complement(yes_ask)
```

Entry prices use `yes_ask` for YES and `no_ask` for NO. Exit prices use the
corresponding bids.

## Predict.fun API usage

The bot uses the official Predict API endpoints:

- `GET /v1/markets`
- `GET /v1/markets/{id}/orderbook`
- `GET /v1/markets/{id}/stats`
- `POST /v1/orders`
- `GET /v1/positions`

Market parsing relies on `title/question/outcomes` to detect symbol, timeframe,
strike, and expiry. If your markets format differs, adjust `market_parser.py`.

Pagination uses the `first` + `after` query params from the API docs. You can
adjust `PREDICTFUN_MAX_PAGES` and `PREDICTFUN_MARKETS_PAGE_SIZE` in `.env`.

To avoid rate limits, the bot can sleep between pages (`PREDICTFUN_PAGE_SLEEP_SEC`)
and will stop pagination after repeated errors (`PREDICTFUN_MAX_PAGE_ERRORS`).

If the API supports it, you can request only specific market statuses with:
```
PREDICTFUN_MARKETS_STATUSES=UNPAUSED,PRICE_PROPOSED,REGISTERED,PAUSED
```
The client will fall back gracefully if the status filter is not supported.

The web app appears to use GraphQL for open markets. You can switch to GraphQL
listing with:
```
PREDICTFUN_MARKETS_SOURCE=graphql
PREDICTFUN_GRAPHQL_URL=https://graphql.predict.fun/graphql
PREDICTFUN_GRAPHQL_IS_RESOLVED=false
```

The bot caches the last pagination cursor to avoid scanning the entire archive
every loop. Control this with:
```
MARKETS_CURSOR_TTL_SEC=900
```

Orderbook reads can be retried automatically:
```
ORDERBOOK_RETRY_COUNT=2
ORDERBOOK_RETRY_SLEEP_SEC=0.6
```

To use WebSocket orderbooks (recommended for farming points):
```
ORDERBOOK_SOURCE=ws
WS_ORDERBOOK_URL=wss://ws.predict.fun/ws
WS_ORDERBOOK_SNAPSHOT_SEC=6
WS_ORDERBOOK_MAX_TOPICS=50
WS_ORDERBOOK_BATCH_SLEEP_SEC=0.2
```
