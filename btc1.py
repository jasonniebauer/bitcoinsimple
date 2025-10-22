from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta
from pycoingecko import CoinGeckoAPI
import requests
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from redislite import Redis
import json

app = FastAPI(title="Simple BTC API", description="Dead simple Bitcoin data—prices, balances, txs, and more.")

# Rate limiter (in-memory)
limiter = Limiter(key_func=get_remote_address, default_limits=["100/15minutes"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Initialize RedisLite
redis_client = Redis('/home/yourusername/redis.db')  # Adjust for PythonAnywhere

cg = CoinGeckoAPI()
HALVING_INTERVAL = 210000
BLOCK_TIME_MIN = 10

def iso_now(): return datetime.utcnow().isoformat() + "Z"

@app.get("/")
def root():
    return {
        "endpoints": [
            "/price", "/price/{fiat}", "/balance/{address}", "/tx/{txid}",
            "/block/{height}", "/block/{hash}", "/stats", "/historical/price?date=YYYY-MM-DD",
            "/mempool", "/halving", "/fees"
        ]
    }

# Price
@app.get("/price")
@limiter.limit("100/15minutes")
async def get_price_default():
    return await get_price("usd")

@app.get("/price/{fiat}")
@limiter.limit("100/15minutes")
async def get_price(fiat: str):
    cache_key = f"price:{fiat.lower()}"
    cached = redis_client.get(cache_key)
    if cached:
        return json.loads(cached)
    try:
        data = cg.get_price(ids='bitcoin', vs_currencies=fiat, include_24hr_change=True)
        price_key = fiat.lower()
        response = {
            f"price_{price_key}": data['bitcoin'][price_key],
            "change_24h_percent": data['bitcoin'][f'{price_key}_24h_change'],
            "timestamp": iso_now(),
            "source": "coingecko"
        }
        redis_client.setex(cache_key, 10, json.dumps(response))
        return response
    except KeyError:
        raise HTTPException(400, "Invalid fiat currency")

# Balance (no caching)
class BalanceResponse(BaseModel):
    address: str
    balance_btc: float
    balance_usd: float
    tx_count: int
    last_tx: str

@app.get("/balance/{address}", response_model=BalanceResponse)
@limiter.limit("100/15minutes")
async def get_balance(address: str):
    try:
        resp = requests.get(f"https://blockstream.info/api/address/{address}", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        balance_sats = data["chain_stats"]["funded_txo_sum"] - data["chain_stats"]["spent_txo_sum"]
        price = cg.get_price(ids='bitcoin', vs_currencies='usd')['bitcoin']['usd']
        return BalanceResponse(
            address=address,
            balance_btc=balance_sats / 1e8,
            balance_usd=(balance_sats / 1e8) * price,
            tx_count=data["chain_stats"]["tx_count"],
            last_tx=datetime.fromtimestamp(data["chain_stats"].get("last_tx_timestamp", 0)).isoformat() + 'Z' if data["chain_stats"].get("last_tx_timestamp") else "N/A"
        )
    except requests.RequestException:
        raise HTTPException(400, "Invalid address")

# Tx (no caching)
class TxResponse(BaseModel):
    txid: str
    block_height: int
    confirmations: int
    fee_btc: float
    value_btc: float
    timestamp: str

@app.get("/tx/{txid}", response_model=TxResponse)
@limiter.limit("100/15minutes")
async def get_tx(txid: str):
    try:
        resp = requests.get(f"https://blockstream.info/api/tx/{txid}", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        status = data["status"]
        confirmations = status.get("block_height", 0) - data.get("latest_height", status["block_height"]) + 1 if status["confirmed"] else 0
        value_sats = sum(vout["value"] for vout in data["vout"])
        return TxResponse(
            txid=txid,
            block_height=status.get("block_height", 0),
            confirmations=confirmations,
            fee_btc=data["fee"] / 1e8,
            value_btc=value_sats / 1e8,
            timestamp=datetime.fromtimestamp(status.get("block_time", 0)).isoformat() + 'Z' if status.get("block_time") else "N/A"
        )
    except requests.RequestException:
        raise HTTPException(400, "Invalid txid")

# Block
class BlockResponse(BaseModel):
    height: int
    hash: str
    timestamp: str
    miner: str
    tx_count: int
    reward_btc: float

@app.get("/block/{height}", response_model=BlockResponse)
@limiter.limit("100/15minutes")
async def get_block(height: int):
    cache_key = f"block:height:{height}"
    cached = redis_client.get(cache_key)
    if cached:
        return json.loads(cached)
    try:
        resp = requests.get(f"https://blockstream.info/api/block-height/{height}", timeout=5)
        resp.raise_for_status()
        block_hash = resp.text
        resp = requests.get(f"https://blockstream.info/api/block/{block_hash}", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        reward = 50 / (2 ** (height // HALVING_INTERVAL))
        response = BlockResponse(
            height=height,
            hash=block_hash,
            timestamp=datetime.fromtimestamp(data["timestamp"]).isoformat() + "Z",
            miner=data.get("extras", {}).get("pool_name", "Unknown"),
            tx_count=data["tx_count"],
            reward_btc=reward
        )
        redis_client.setex(cache_key, 3600, json.dumps(response.dict()))
        return response
    except requests.RequestException:
        raise HTTPException(400, "Invalid block height")

@app.get("/block/{hash}", response_model=BlockResponse)
@limiter.limit("100/15minutes")
async def get_block_by_hash(hash: str):
    cache_key = f"block:hash:{hash}"
    cached = redis_client.get(cache_key)
    if cached:
        return json.loads(cached)
    try:
        resp = requests.get(f"https://blockstream.info/api/block/{hash}", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        reward = 50 / (2 ** (data["height"] // HALVING_INTERVAL))
        response = BlockResponse(
            height=data["height"],
            hash=hash,
            timestamp=datetime.fromtimestamp(data["timestamp"]).isoformat() + "Z",
            miner=data.get("extras", {}).get("pool_name", "Unknown"),
            tx_count=data["tx_count"],
            reward_btc=reward
        )
        redis_client.setex(cache_key, 3600, json.dumps(response.dict()))
        return response
    except requests.RequestException:
        raise HTTPException(400, "Invalid block hash")

# Stats
class StatsResponse(BaseModel):
    hashrate_th_s: float
    difficulty: float
    circulating_supply_btc: float
    mempool_size_mb: float
    timestamp: str

@app.get("/stats", response_model=StatsResponse)
@limiter.limit("100/15minutes")
async def get_stats():
    cache_key = "stats"
    cached = redis_client.get(cache_key)
    if cached:
        return json.loads(cached)
    try:
        coin_data = cg.get_coin_by_id('bitcoin', localization=False, tickers=False, market_data=True, community_data=False, developer_data=False, sparkline=False)
        hr_resp = requests.get("https://api.blockchain.info/charts/hash-rate?timespan=1days&format=json", timeout=5)
        diff_resp = requests.get("https://api.blockchain.info/charts/difficulty?timespan=1days&format=json", timeout=
