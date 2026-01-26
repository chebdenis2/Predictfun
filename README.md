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
