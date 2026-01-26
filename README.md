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

If `p_model - p_market >= 0.04` the bot proposes a YES trade. If
`p_market - p_model >= 0.04` it proposes a NO trade. Volume and expiry filters
apply.

## Quick start

1. Configure environment variables (see `.env.example`).
2. Run the bot:

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

## Predict.fun API mapping

The client expects Predict.fun markets to be returned in a normalized JSON
shape with keys like:

```
{
  "id": "...",
  "symbol": "BTCUSDT",
  "title": "...",
  "yes_price": 0.52,
  "no_price": 0.48,
  "volume_usd": 123456,
  "expiry_ts": 1700000000,
  "resolution_minutes": 15,
  "kind": "UPDOWN"
}
```

If your API uses different field names, update `predictfun_client.py` to map
them correctly.
