from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from datetime import datetime, timedelta
from pycoingecko import CoinGeckoAPI
import requests
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

app = FastAPI(title="Simple BTC API", description="Dead simple Bitcoin data—prices, balances, txs, and more.")

# Rate limiter (in-memory)
limiter = Limiter(key_func=get_remote_address, default_limits=["100/15minutes"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

cg = CoinGeckoAPI()

HALVING_INTERVAL = 210000
BLOCK_TIME_MIN = 10  # Average minutes per block

# Root endpoint for menu
@app.get("/")
def root():
    return {
        "endpoints": [
            "/price",
            "/price/{fiat}",
            "/balance/{address}",
            "/tx/{txid}",
            "/block/{height}",
            "/stats",
            "/historical/price?date=YYYY-MM-DD",
            "/mempool",
            "/halving",
            "/fees"
        ]
    }

# Price models (using dict for flexibility)
@app.get("/price")
@limiter.limit("100/15minutes")
async def get_price_default():
    return await get_price("usd")

@app.get("/price/{fiat}")
@limiter.limit("100/15minutes")
async def get_price(fiat: str):
    try:
        data = cg.get_price(ids='bitcoin', vs_currencies=fiat, include_24hr_change=True)
        price_key = fiat.lower()
        price = data['bitcoin'][price_key]
        change = data['bitcoin'][f'{price_key}_24h_change']
        return {
            f"price_{price_key}": price,
            "change_24h_percent": change,
            "timestamp": datetime.utcnow().isoformat() + 'Z',
            "source": "coingecko"
        }
    except KeyError:
        raise HTTPException(status_code=400, detail="Invalid fiat currency")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Balance
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
        url = f"https://blockstream.info/api/address/{address}"
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        balance_sats = data["chain_stats"]["funded_txo_sum"] - data["chain_stats"]["spent_txo_sum"]
        # Fetch USD price
        price_data = cg.get_price(ids='bitcoin', vs_currencies='usd')
        price = price_data['bitcoin']['usd']
        return BalanceResponse(
            address=address,
            balance_btc=balance_sats / 1e8,
            balance_usd=(balance_sats / 1e8) * price,
            tx_count=data["chain_stats"]["tx_count"],
            last_tx=datetime.fromtimestamp(data["chain_stats"].get("last_tx_timestamp", 0)).isoformat() + 'Z' if data["chain_stats"].get("last_tx_timestamp") else "N/A"
        )
    except requests.RequestException:
        raise HTTPException(status_code=400, detail="Invalid address or API error")

# Tx
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
        url = f"https://blockstream.info/api/tx/{txid}"
        resp = requests.get(url, timeout=5)
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
        raise HTTPException(status_code=400, detail="Invalid txid or API error")

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
    try:
        url = f"https://blockstream.info/api/block-height/{height}"
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        block_hash = resp.text
        url = f"https://blockstream.info/api/block/{block_hash}"
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        # Reward: dynamic based on height
        halvings = height // HALVING_INTERVAL
        reward = 50 / (2 ** halvings)
        return BlockResponse(
            height=height,
            hash=block_hash,
            timestamp=datetime.fromtimestamp(data["timestamp"]).isoformat() + "Z",
            miner=data.get("extras", {}).get("pool_name", "Unknown"),
            tx_count=data["tx_count"],
            reward_btc=reward
        )
    except requests.RequestException:
        raise HTTPException(status_code=400, detail="Invalid block height or API error")

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
    try:
        # Circulating supply from CoinGecko
        coin_data = cg.get_coin_by_id('bitcoin', localization=False, tickers=False, market_data=True, community_data=False, developer_data=False, sparkline=False)
        supply = coin_data['market_data']['circulating_supply']

        # Hashrate from Blockchain.com (last 24h average in TH/s)
        hr_url = "https://api.blockchain.info/charts/hash-rate?timespan=1days&format=json"
        hr_resp = requests.get(hr_url, timeout=5)
        hr_data = hr_resp.json()
        hashrate = hr_data['values'][-1]['y']

        # Difficulty
        diff_url = "https://api.blockchain.info/charts/difficulty?timespan=1days&format=json"
        diff_resp = requests.get(diff_url, timeout=5)
        diff_data = diff_resp.json()
        difficulty = diff_data['values'][-1]['y']

        # Mempool from Blockstream
        mem_url = "https://blockstream.info/api/mempool"
        mem_resp = requests.get(mem_url, timeout=5)
        mem_data = mem_resp.json()
        mem_size = mem_data['vsize'] / 1e6

        return StatsResponse(
            hashrate_th_s=hashrate,
            difficulty=difficulty,
            circulating_supply_btc=supply,
            mempool_size_mb=mem_size,
            timestamp=datetime.utcnow().isoformat() + 'Z'
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Historical Price
class HistoricalPriceResponse(BaseModel):
    date: str
    price_usd: float
    volume_24h_usd: float
    market_cap_usd: float

@app.get("/historical/price", response_model=HistoricalPriceResponse)
@limiter.limit("100/15minutes")
async def get_historical_price(date: str):
    try:
        # Convert YYYY-MM-DD to dd-mm-yyyy
        y, m, d = date.split('-')
        cg_date = f"{d}-{m}-{y}"
        hist_data = cg.get_coin_history_by_id(id='bitcoin', date=cg_date, localization=False)
        market_data = hist_data['market_data']
        return HistoricalPriceResponse(
            date=date,
            price_usd=market_data['current_price']['usd'],
            volume_24h_usd=market_data['total_volume']['usd'],
            market_cap_usd=market_data['market_cap']['usd']
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format (use YYYY-MM-DD)")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Mempool
class MempoolResponse(BaseModel):
    size_tx: int
    size_mb: float
    fee_per_kb_sat: float
    timestamp: str

@app.get("/mempool", response_model=MempoolResponse)
@limiter.limit("100/15minutes")
async def get_mempool():
    try:
        url = "https://blockstream.info/api/mempool"
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        # Fee: use fee-estimates for medium
        fee_url = "https://blockstream.info/api/fee-estimates"
        fee_resp = requests.get(fee_url, timeout=5)
        fee_data = fee_resp.json()
        fee_per_kb = fee_data.get("6", 25)  # ~1 hour target
        return MempoolResponse(
            size_tx=data["count"],
            size_mb=data["vsize"] / 1e6,
            fee_per_kb_sat=fee_per_kb,
            timestamp=datetime.utcnow().isoformat() + 'Z'
        )
    except requests.RequestException:
        raise HTTPException(status_code=500, detail="API error")

# Halving
class HalvingResponse(BaseModel):
    next_halving_height: int
    blocks_remaining: int
    estimated_date: str
    current_reward_btc: float
    next_reward_btc: float
    total_halvings: int
    timestamp: str

@app.get("/halving", response_model=HalvingResponse)
@limiter.limit("100/15minutes")
async def get_halving():
    try:
        url = "https://blockstream.info/api/blocks/tip/height"
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        current_height = int(resp.text)
        halvings_so_far = current_height // HALVING_INTERVAL
        next_height = (halvings_so_far + 1) * HALVING_INTERVAL
        blocks_left = next_height - current_height
        est_minutes = blocks_left * BLOCK_TIME_MIN
        est_date = (datetime.utcnow() + timedelta(minutes=est_minutes)).isoformat() + "Z"
        current_reward = 50 / (2 ** halvings_so_far)
        next_reward = current_reward / 2
        return HalvingResponse(
            next_halving_height=next_height,
            blocks_remaining=blocks_left,
            estimated_date=est_date,
            current_reward_btc=current_reward,
            next_reward_btc=next_reward,
            total_halvings=halvings_so_far,
            timestamp=datetime.utcnow().isoformat() + 'Z'
        )
    except requests.RequestException:
        raise HTTPException(status_code=500, detail="API error")

# Fees
class FeesResponse(BaseModel):
    fast_sat_per_byte: int
    fast_confirm_min: int
    medium_sat_per_byte: int
    medium_confirm_min: int
    slow_sat_per_byte: int
    slow_confirm_min: int
    mempool_size_mb: float
    timestamp: str

@app.get("/fees", response_model=FeesResponse)
@limiter.limit("100/15minutes")
async def get_fees():
    try:
        url = "https://blockstream.info/api/fee-estimates"
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        fast = int(data.get("2", 50))  # Next block
        medium = int(data.get("6", 25))  # ~1 hour
        slow = int(data.get("144", 10))  # ~1 day
        mem_url = "https://blockstream.info/api/mempool"
        mem_resp = requests.get(mem_url, timeout=5)
        mem_data = mem_resp.json()
        return FeesResponse(
            fast_sat_per_byte=fast,
            fast_confirm_min=20,  # Approx
            medium_sat_per_byte=medium,
            medium_confirm_min=60,
            slow_sat_per_byte=slow,
            slow_confirm_min=1440,
            mempool_size_mb=mem_data["vsize"] / 1e6,
            timestamp=datetime.utcnow().isoformat() + 'Z'
        )
    except requests.RequestException:
        raise HTTPException(status_code=500, detail="API error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
