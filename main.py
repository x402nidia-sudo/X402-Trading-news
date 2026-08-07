import base64
import json
import requests
import traceback
import time
import os 
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import FileResponse 
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os

load_dotenv()

app = FastAPI(title="x402 Trading news Engine API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "x402-payment-required",
        "payment-required",
        "Payment-Required",
        "PAYMENT-RESPONSE",
        "X-PAYMENT-RESPONSE",
        "x402-version",
        "x402-status",
        "Content-Type"
    ]
)

@app.get("/.well-known/x402.json")
async def x402_manifest():
    """Serve the x402 discovery manifest"""
    file_path = os.path.join(os.path.dirname(__file__), "x402.json")
    return FileResponse(file_path, media_type="application/json")

PAYTO_ADDRESS = "EH5BHWISPB7MEIITJIWF2VB3YFN2RZLJMWBRV6CBJV76FBAEAALL6XKSQE"
USDC_ASA_ID = "31566704"
ALGORAND_MAINNET_CAIP2 = "algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8="
PRICE = "100000"

import urllib.parse

def calculate_quant_signals(symbol: str):
    symbol_mapping = {
        "BTC": "Bitcoin",
        "ETH": "Ethereum",
        "USDT": "Tether USDT",
        "BNB": "Binance Coin",
        "XRP": "Ripple XRP",
        "SOL": "Solana blockchain",
        "STETH": "Ethereum staking",
        "ADA": "Cardano",
        "DOGE": "Dogecoin",
        "DOT": "Polkadot",
        "MATIC": "Polygon Matic",
        "LTC": "Litecoin",
        "BCH": "Bitcoin Cash",
        "LINK": "Chainlink",
        "UNI": "Uniswap",
        "WBTC": "Wrapped Bitcoin",
        "ATOM": "Cosmos ATOM",
        "XMR": "Monero",
        "XLM": "Stellar Lumens",
        "AVAX": "Avalanche",
        "ARB": "Arbitrum",
        "OP": "Optimism",
        "FIL": "Filecoin",
        "GRT": "The Graph",
        "APT": "Aptos",
        "HBAR": "Hedera",
        "FTM": "Fantom",
        "ALGO": "Algorand",
        "CRO": "Crypto.com",
        "MKR": "Maker MKR",
        "AAVE": "Aave",
        "CRV": "Curve",
        "ICP": "Internet Computer",
        "QNT": "Quant",
        "SUI": "Sui blockchain",
        "LDO": "Lido",
        "COMP": "Compound",
        "IMX": "Immutable X",
        "CVX": "Convex",
        "KAS": "Kaspa",
        "ZEC": "Zcash",
        "DASH": "Dash coin",
        "AXS": "Axie Infinity",
        "SAND": "The Sandbox",
        "MANA": "Decentraland",
        "YFI": "Yearn Finance",
        "CAKE": "PancakeSwap",
        "BAL": "Balancer",
        "SNX": "Synthetix",
    }
    
    guardian_api_key = os.getenv("GUARDIAN_API_KEY")
    if not guardian_api_key:
        return {"error": "GUARDIAN_API_KEY no configurada"}
    
    query = symbol_mapping.get(symbol.upper(), symbol)
    url = f"https://content.guardianapis.com/search?q={urllib.parse.quote(query)}&api-key={guardian_api_key}&show-fields=headline,bodyText,webPublicationDate&page-size=10"
    
    print("\n" + "="*70)
    print("DEBUG: GUARDIAN API REQUEST")
    print("="*70)
    print(f"URL: {url}")
    print(f"Symbol: {symbol}")
    print(f"Query: {query}")
    print(f"API Key (primeros 10 chars): {guardian_api_key[:10]}...")
    print("="*70)
    
    try:
        print(f"[DEBUG] Enviando petición a Guardian...")
        res = requests.get(url, timeout=10)
        
        print(f"[DEBUG] Status Code: {res.status_code}")
        print(f"[DEBUG] Headers respuesta:")
        for key, value in res.headers.items():
            print(f"       {key}: {value}")
        
        print(f"[DEBUG] Response body (primeros 500 chars):")
        print(f"       {res.text[:500]}")
        print("="*70 + "\n")
        
        if res.status_code == 429:
            print("[!] 429 - Too Many Requests")
            print(f"    Rate-Limit-Remaining: {res.headers.get('X-RateLimit-Remaining', 'N/A')}")
            print(f"    Retry-After: {res.headers.get('Retry-After', 'N/A')}")
            return {
                "error": f"Guardian API 429 - Rate Limited",
                "rate_limit_remaining": res.headers.get('X-RateLimit-Remaining'),
                "retry_after": res.headers.get('Retry-After')
            }
        
        if res.status_code == 403:
            print("[!] 403 - Forbidden")
            print(f"    Response: {res.text}")
            return {"error": "Guardian API 403 - Forbidden (posible IP bloqueada o API key inválida)"}
        
        if res.status_code == 401:
            print("[!] 401 - Unauthorized")
            return {"error": "GUARDIAN_API_KEY inválida o expirada"}
        
        if res.status_code == 400:
            print("[!] 400 - Bad Request")
            return {"error": f"Guardian API 400 - Bad Request: {res.text}"}
        
        if res.status_code != 200:
            print(f"[!] Error HTTP {res.status_code}")
            return {"error": f"Guardian API error: {res.status_code}"}
        
        data = res.json()
        results = data.get("response", {}).get("results", [])
        
        if not results:
            return {
                "error": f"No articles found for '{query}'",
                "article_count": 0,
                "recommendation": "N/A"
            }
        
        article_count = len(results)
        
        if article_count >= 8:
            signal = "BUY"
        elif article_count >= 5:
            signal = "HOLD"
        elif article_count >= 2:
            signal = "WAIT"
        else:
            signal = "SELL"
        
        top_article = results[0].get("webTitle", "N/A")
        
        return {
            "asset": f"{symbol.upper()}-NEWS",
            "article_count": article_count,
            "recommendation": signal,
            "top_article": top_article,
            "query_used": query,
            "timestamp": int(time.time())
        }
    
    except requests.exceptions.Timeout:
        print("[!] TIMEOUT - Guardian API no responde")
        return {"error": "Guardian API timeout (>10s)"}
    except requests.exceptions.ConnectionError as e:
        print(f"[!] CONNECTION ERROR - {str(e)}")
        return {"error": f"Cannot connect to Guardian API: {str(e)}"}
    except Exception as e:
        print(f"[!] EXCEPTION - {str(e)}")
        import traceback
        traceback.print_exc()
        return {"error": f"Error: {str(e)}"}


@app.get("/api/v1/debug-guardian/{symbol}")
async def debug_guardian(symbol: str):
    """Ruta para debuggear Guardian directamente (SIN pago requerido)"""
    print(f"\n[DEBUG] Testeo directo de Guardian para: {symbol}")
    
    result = calculate_quant_signals(symbol)
    
    return {
        "symbol": symbol,
        "debug_result": result,
        "timestamp": int(time.time())
    }


@app.get("/api/v1/check-ip")
async def check_ip(request: Request):
    """Ruta para verificar tu IP actual"""
    client_ip = request.client.host if request.client else "unknown"
    forwarded_for = request.headers.get("x-forwarded-for", "none")
    
    return {
        "client_ip": client_ip,
        "x-forwarded-for": forwarded_for,
        "all_headers": dict(request.headers)
    }


@app.get("/api/v1/market-signal/{symbol}")
async def get_market_signal(request: Request, response: Response, symbol: str):
    
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "x402-quant-signals.onrender.com"
    proto = request.headers.get("x-forwarded-proto") or "https"
    public_url = f"{proto}://{host}{request.url.path}"

    auth_header = (
        request.headers.get("Authorization") or 
        request.headers.get("PAYMENT-SIGNATURE") or 
        request.headers.get("X-PAYMENT") or
        request.headers.get("payment-signature")
    )

    requirement_item = {
        "scheme": "exact",
        "network": ALGORAND_MAINNET_CAIP2,
        "asset": USDC_ASA_ID,
        "amount": PRICE,
        "payTo": PAYTO_ADDRESS,
        "maxTimeoutSeconds": 300,
        "extra": {
            "decimals": 6,
            "tag": "x402-global-challenge"
        }
    }

    if not auth_header:
        print("-> Petición sin pago: Enviando 402 Challenge con Bazaar Discovery")
        
        bazaar_extension = {
            "info": {
                "symbol": "string (BTC, ETH, ALGO, etc.)"
            },
            "schema": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Cryptocurrency symbol for market signal",
                        "examples": ["BTC", "ETH", "ALGO"]
                    }
                },
                "required": ["symbol"]
            }
        }
        
        payment_challenge = {
            "x402Version": 2,
            "resource": {
                "title": "AlphaSync Quant Engine",
                "name": "AlphaSync Quant Engine",
                "url": public_url,
                "description": "Real-time Market News & Crypto Analysis",
                "mimeType": "application/json"
            },
            "accepts": [requirement_item],
            "extensions": {
                "bazaar": bazaar_extension
            }
        }
        
        req_json = json.dumps(payment_challenge, separators=(',', ':'))
        encoded_req = base64.urlsafe_b64encode(req_json.encode()).decode().rstrip("=")
        
        response.status_code = 402
        response.headers["x402-payment-required"] = encoded_req
        response.headers["payment-required"] = encoded_req
        response.headers["x402-version"] = "2"
        response.headers["x402-status"] = "payment-required"
        response.headers["Content-Type"] = "application/json"
        
        return payment_challenge

    print("\n=== NUEVO INTENTO DE PAGO RECIBIDO ===")
    
    try:
        token = auth_header.replace("x402 ", "").replace("Bearer ", "").strip()
        padded_token = token + "=" * ((4 - len(token) % 4) % 4)
        
        try:
            decoded_bytes = base64.urlsafe_b64decode(padded_token)
        except:
            decoded_bytes = base64.b64decode(padded_token)
            
        x402_data = json.loads(decoded_bytes)
        
        facilitator_payload = {
            "paymentPayload": x402_data, 
            "paymentRequirements": requirement_item,
            "resource": public_url,
            "description": "AlphaSync Quant Engine Market Signals"
        }
        
        verify_url = "https://facilitator.goplausible.xyz/verify"
        facilitator_res = requests.post(verify_url, json=facilitator_payload)
        
        if facilitator_res.status_code != 200:
            raise HTTPException(status_code=502, detail="Error de comunicación con GoPlausible")
            
        verify_result = facilitator_res.json()
        
        if not verify_result.get("isValid"):
            print(f"-> VERIFICACIÓN FALLIDA: {verify_result.get('invalidReason')}")
            raise HTTPException(status_code=403, detail=f"Pago inválido: {verify_result.get('invalidReason')}")

        print("-> VERIFICACIÓN OK. Procediendo a hacer SETTLE...")
        
        settle_url = "https://facilitator.goplausible.xyz/settle"
        settle_res = requests.post(settle_url, json=facilitator_payload)
        
        if settle_res.status_code == 200:
            print(f"-> SETTLE COMPLETADO")

        data = calculate_quant_signals(symbol)
        
        return {
            "symbol": symbol,
            "status": "success",
            "message": "Transacción liquidada e indexada en el x402 Global Challenge.",
            "data": data
        }
        
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        print("ERROR INTERNO CRÍTICO DETECTADO:")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")

@app.get("/health")
async def health_check():
    """Endpoint de salud (sin pago requerido)"""
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
