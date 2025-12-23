# [BitcoinSimple](https://bitcoinsimple.info/)
The Swiss Army knife for BTC data.

## Endpoints

### GET /price
Why clever: The one everyone bookmarks first—current BTC price in USD, with 24h change. No fiat param needed for the default; it's the "quick check" endpoint devs embed in apps.
Params: None.
Sample Response:
```
{
  "price_usd": 67234.56,
  "change_24h_percent": 2.34,
  "timestamp": "2025-10-21T12:00:00Z",
  "source": "coingecko"
}
```

### GET /price/{fiat}
Why clever: Extends #1 seamlessly (e.g., /price/eur)—handles any fiat without extra setup. Perfect for global apps; devs wish fiat conversion was this plug-and-play.
Params: {fiat} (e.g., eur, jpy; defaults to usd).
Sample Response (for /price/eur):
```
{
  "price_eur": 62345.78,
  "change_24h_percent": 2.34,
  "timestamp": "2025-10-21T12:00:00Z"
}
```

### GET /balance/{address}
Why clever: Instant wallet snapshot with tx count—devs use this for portfolio trackers or alerts. Simple path makes it feel like querying a database row, not a blockchain.
Params: {address} (Bitcoin address).
Sample Response:
```
{
  "address": "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",
  "balance_btc": 0.005,
  "balance_usd": 336.17,
  "tx_count": 42,
  "last_tx": "2025-10-20T10:30:00Z"
}
```

### GET /tx/{txid}
Why clever: Full tx decode in one call—inputs/outputs, fees, confirmations. Essential for explorers or verifiers; the "detective tool" devs reach for without API hunting.
Params: {txid} (transaction ID).
Sample Response:
```
{
  "txid": "a1075db55d416d3ca199f55b6084e2115b9345e16c5cf302fc80e9d5fbf5d48d",
  "block_height": 862000,
  "confirmations": 6,
  "fee_btc": 0.0001,
  "value_btc": 1.5,
  "timestamp": "2025-10-21T11:45:00Z"
}
```

### GET /block/{height}
Why clever: Block summary with miner, reward, tx count—great for chain analysis or dashboards. Height-based lookup is intuitive (vs. hash); devs love the "time machine" feel.
Params: {height} (block number; or use /block/{hash} as alias).
Sample Response:
```
{
  "height": 862001,
  "hash": "0000000000000000000123abc...",
  "timestamp": "2025-10-21T12:05:00Z",
  "miner": "AntPool",
  "tx_count": 2850,
  "reward_btc": 3.125
}
```

### GET /stats
Why clever: One-stop network pulse—hashrate, difficulty, supply, mempool size. Aggregates what devs query across 5 endpoints elsewhere; it's the "health check" that saves API calls.
Params: None.
Sample Response:
```
{
  "hashrate_th_s": 650000,
  "difficulty": 85000000000,
  "circulating_supply_btc": 19750000,
  "mempool_size_mb": 45.2,
  "timestamp": "2025-10-21T12:00:00Z"
}
```

### GET /historical/price
Why clever: Date-based price history (daily close)—no ranges or intervals to fuss with. Ideal for charts or backtesting; simple enough that devs build time-series apps in minutes.
Params: ?date=YYYY-MM-DD (single date; extend to range later if needed).
Sample Response (for ?date=2025-10-01):
```
{
  "date": "2025-10-01",
  "price_usd": 65890.12,
  "volume_24h_usd": 25000000000,
  "market_cap_usd": 1300000000000
}
```

### GET /mempool
(Bonus for edge)
Why clever: Live fee estimates and queue stats—vital for tx builders during congestion. It's the "traffic report" devs didn't know they needed until fees spike.
Params: None (or ?fast=true for urgent fee).
Sample Response:
```
{
  "size_tx": 120000,
  "size_mb": 45.2,
  "fee_per_kb_sat": 25,
  "timestamp": "2025-10-21T12:00:00Z"
}
```

---------------------------------------------------
These focus on high-impact gaps: forward-looking events (halving) and transaction optimization (fees/confirmation times).

### GET /halving
Why Clever & High-Impact: Provides next halving details (date, blocks left, reward drop)—a "crystal ball" for miners/traders. Devs building dashboards or alerts love this; it's forward-looking info not in your current set, yet simple (no params). Ties into Bitcoin's economics, which is ~20% of API queries in analytics tools.
Provider: Calculate via Blockstream's /blocks/tip/height (current height) + halving math (every 210k blocks).
Params: None (or ?next=true for just the next one).
Sample Response:
```{
  "next_halving_height": 1050000,
  "blocks_remaining": 180000,
  "estimated_date": "2028-04-15T00:00:00Z",
  "current_reward_btc": 3.125,
  "next_reward_btc": 1.5625,
  "total_halvings": 4,
  "timestamp": "2025-10-22T12:00:00Z"
}
```

### GET /fees
Why Clever & High-Impact: Dynamic fee estimates (fast/medium/slow) with expected confirmation times—builds on /mempool but focuses on actionable tx building. Devs hate overpaying fees; this is a "wallet optimizer" endpoint, common in APIs like Blockcypher's /v1/btc/main fees. It's practical for apps during volatility (e.g., congestion alerts), covering confirmation time without a separate endpoint.
Provider: Blockstream's /fee-estimates.
Params: None (or ?priority=fast for single).
Sample Response:
```
{
  "fast_sat_per_byte": 50,
  "fast_confirm_min": 10,
  "medium_sat_per_byte": 25,
  "medium_confirm_min": 30,
  "slow_sat_per_byte": 10,
  "slow_confirm_min": 120,
  "mempool_size_mb": 45.2,
  "timestamp": "2025-10-22T12:00:00Z"
}

```
