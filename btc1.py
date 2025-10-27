from fastapi import FastAPI, HTTPException, Query, Request
# from fastapi.staticfiles import StaticFiles
# from fastapi.openapi.docs import get_swagger_ui_html
# from starlette.responses import HTMLResponse
from pydantic import BaseModel
from datetime import datetime, timedelta
from pycoingecko import CoinGeckoAPI
import requests
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from redis import Redis
import json
import os
from dotenv import load_dotenv

load_dotenv()  # Load environment variables

app = FastAPI(
    title="BitcoinSimple API",
    description="The Swiss Army knife for BTC data.",
    # swagger_ui_parameters={
    #     "defaultModelsExpandDepth": -1,  # Hide schemas for cleaner UI
    #     "tryItOutEnabled": True,  # Enable "Try it out" by default
    #     # "customCSSUrl": "/static/swagger.css"  # Link to custom CSS
    #     "customCSS": "./static/custom_swagger.css"  # Link to custom CSS
    # }
)

# Run app with command: uvicorn btc1:app --reload

# Mount static files for serving CSS
# app.mount("/static", StaticFiles(directory="static"), name="static")

# Rate limiter (in-memory or Redis-backed)
limiter = Limiter(key_func=get_remote_address, default_limits=["100/15minutes"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Initialize Redis client for external Redis server
redis_client = Redis(
    host=os.getenv('REDIS_HOST'),
    port=int(os.getenv('REDIS_PORT')),
    password=os.getenv('REDIS_PASSWORD'),
    decode_responses=True,  # Returns strings instead of bytes for easier handling
    ssl=False, # Enable SSL if required by your Redis provider (e.g., Redis Enterprise Cloud)
    # ssl_cert_reqs='required',  # Enforce certificate validation
    # ssl_min_version=ssl.TLSVersion.TLSv1_2  # Force TLS 1.2
)

cg = CoinGeckoAPI()
HALVING_INTERVAL = 210000
BLOCK_TIME_MIN = 10

def iso_now(): return datetime.utcnow().isoformat() + "Z"

# @app.get("/docs", include_in_schema=False)
# async def custom_swagger_ui_html() -> HTMLResponse:
#     return get_swagger_ui_html(
#         openapi_url=app.openapi_url,
#         title=app.title + " - Swagger UI",
#         swagger_css_url="/static/custom_swagger.css",  # Your custom CSS file
#     )

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
async def get_price_default(request: Request):
    return await get_price("usd", request)

@app.get("/price/{fiat}")
@limiter.limit("100/15minutes")
async def get_price(fiat: str, request: Request):
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
async def get_balance(address: str, request: Request):
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
async def get_tx(txid: str, request: Request):
    try:
        resp = requests.get(f"https://blockstream.info/api/tx/{txid}", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        status = data["status"]
        if status["confirmed"]:
            tip_resp = requests.get("https://blockstream.info/api/blocks/tip/height", timeout=5)
            tip_resp.raise_for_status()
            tip_height = int(tip_resp.text)
            confirmations = tip_height - status["block_height"] + 1
        else:
            confirmations = 0
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
async def get_block(height: int, request: Request):
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
async def get_block_by_hash(hash: str, request: Request):
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
async def get_stats(request: Request):
    cache_key = "stats"
    cached = redis_client.get(cache_key)
    if cached:
        return json.loads(cached)
    try:
        coin_data = cg.get_coin_by_id('bitcoin', localization=False, tickers=False, market_data=True, community_data=False, developer_data=False, sparkline=False)
        hr_resp = requests.get("https://api.blockchain.info/charts/hash-rate", timeout=5)
        diff_resp = requests.get("https://api.blockchain.info/charts/difficulty", timeout=5)
        hr_resp.raise_for_status()
        diff_resp.raise_for_status()
        hr_data = hr_resp.json()
        diff_data = diff_resp.json()
        hashrate = hr_data['values'][-1]['y']
        difficulty = diff_data['values'][-1]['y']
        circulating_supply = coin_data['market_data']['circulating_supply']
        mempool_resp = requests.get("https://blockstream.info/api/mempool", timeout=5)
        mempool_resp.raise_for_status()
        mempool_data = mempool_resp.json()
        mempool_size_mb = mempool_data['vsize'] / 1e6
        response = StatsResponse(
            hashrate_th_s=hashrate,
            difficulty=difficulty,
            circulating_supply_btc=circulating_supply,
            mempool_size_mb=mempool_size_mb,
            timestamp=iso_now()
        )
        redis_client.setex(cache_key, 60, json.dumps(response.dict()))
        return response
    except requests.RequestException:
        raise HTTPException(500, "Error fetching stats")

# Historical Price
class HistoricalPriceResponse(BaseModel):
    date: str
    price_usd: float
    market_cap_usd: float
    volume_usd: float

@app.get("/historical/price", response_model=HistoricalPriceResponse)
@limiter.limit("100/15minutes")
async def get_historical_price(request: Request, date: str = Query(..., description="Date in YYYY-MM-DD")):  # Reordered parameters
    try:
        dt = datetime.fromisoformat(date)
        date_cg = dt.strftime("%d-%m-%Y")
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD")
    cache_key = f"historical_price:{date}"
    cached = redis_client.get(cache_key)
    if cached:
        return json.loads(cached)
    try:
        data = cg.get_coin_history_by_id('bitcoin', date=date_cg, localization=False)
        md = data['market_data']
        response = HistoricalPriceResponse(
            date=date,
            price_usd=md['current_price']['usd'],
            market_cap_usd=md['market_cap']['usd'],
            volume_usd=md['total_volume']['usd']
        )
        redis_client.setex(cache_key, 86400, json.dumps(response.dict()))
        return response
    except Exception as e:
        raise HTTPException(500, f"Error fetching historical data: {str(e)}")

# Mempool
class FeeHistogramEntry(BaseModel):
    fee_rate: float
    vsize: int

class MempoolResponse(BaseModel):
    count: int
    vsize: int
    total_fee_btc: float
    fee_histogram: list[FeeHistogramEntry]

@app.get("/mempool", response_model=MempoolResponse)
@limiter.limit("100/15minutes")
async def get_mempool(request: Request):
    cache_key = "mempool"
    cached = redis_client.get(cache_key)
    if cached:
        return json.loads(cached)
    try:
        resp = requests.get("https://blockstream.info/api/mempool", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        response = MempoolResponse(
            count=data['count'],
            vsize=data['vsize'],
            total_fee_btc=data['total_fee'] / 1e8,
            fee_histogram=[FeeHistogramEntry(fee_rate=entry[0], vsize=entry[1]) for entry in data.get('fee_histogram', [])]
        )
        redis_client.setex(cache_key, 10, json.dumps(response.dict()))
        return response
    except requests.RequestException:
        raise HTTPException(500, "Error fetching mempool data")

# Halving
class HalvingResponse(BaseModel):
    current_reward_btc: float
    next_halving_block: int
    blocks_remaining: int
    estimated_date: str

@app.get("/halving", response_model=HalvingResponse)
@limiter.limit("100/15minutes")
async def get_halving(request: Request):
    cache_key = "halving"
    cached = redis_client.get(cache_key)
    if cached:
        return json.loads(cached)
    try:
        tip_resp = requests.get("https://blockstream.info/api/blocks/tip/height", timeout=5)
        tip_resp.raise_for_status()
        current_height = int(tip_resp.text)
        epoch = current_height // HALVING_INTERVAL
        current_reward = 50 / (2 ** epoch)
        next_halving = (epoch + 1) * HALVING_INTERVAL
        blocks_remaining = next_halving - current_height
        estimated_eta = datetime.utcnow() + timedelta(minutes=blocks_remaining * BLOCK_TIME_MIN)
        response = HalvingResponse(
            current_reward_btc=current_reward,
            next_halving_block=next_halving,
            blocks_remaining=blocks_remaining,
            estimated_date=estimated_eta.isoformat() + "Z"
        )
        redis_client.setex(cache_key, 600, json.dumps(response.dict()))
        return response
    except requests.RequestException:
        raise HTTPException(500, "Error fetching halving data")

# Fees
class FeesResponse(BaseModel):
    fastest_sat_vb: float
    half_hour_sat_vb: float
    hour_sat_vb: float
    minimum_sat_vb: float

@app.get("/fees", response_model=FeesResponse)
@limiter.limit("100/15minutes")
async def get_fees(request: Request):
    cache_key = "fees"
    cached = redis_client.get(cache_key)
    if cached:
        return json.loads(cached)
    try:
        resp = requests.get("https://blockstream.info/api/fee-estimates", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        response = FeesResponse(
            fastest_sat_vb=data.get("1", 0),
            half_hour_sat_vb=data.get("3", 0),
            hour_sat_vb=data.get("6", 0),
            minimum_sat_vb=data.get("144", 0)
        )
        redis_client.setex(cache_key, 60, json.dumps(response.dict()))
        return response
    except requests.RequestException:
        raise HTTPException(500, "Error fetching fee estimates")