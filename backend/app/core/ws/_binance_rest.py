"""Binance Futures REST helpers for listen key. No execution, no orders."""

import hashlib
import hmac
import time
import urllib.parse

import requests


def _sign(api_secret: str, params: dict) -> str:
    query = urllib.parse.urlencode(params)
    sig = hmac.new(
        api_secret.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return query + "&signature=" + sig


def create_listen_key(api_key: str, api_secret: str, base_url: str) -> str:
    """POST /fapi/v1/listenKey. Returns listen key string."""
    params = {"timestamp": int(time.time() * 1000), "recvWindow": 5000}
    signed = _sign(api_secret, params)
    url = f"{base_url}/fapi/v1/listenKey?{signed}"
    headers = {"X-MBX-APIKEY": api_key}
    resp = requests.post(url, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"listenKey create failed: {resp.status_code} {resp.text}")
    data = resp.json()
    key = data.get("listenKey")
    if not key:
        raise RuntimeError("listenKey response missing listenKey")
    return str(key)


def keepalive_listen_key(
    api_key: str,
    api_secret: str,
    base_url: str,
    listen_key: str,
) -> None:
    """PUT /fapi/v1/listenKey. Extends validity for given listenKey."""
    params = {
        "listenKey": listen_key,
        "timestamp": int(time.time() * 1000),
        "recvWindow": 5000,
    }
    signed = _sign(api_secret, params)
    url = f"{base_url}/fapi/v1/listenKey?{signed}"
    headers = {"X-MBX-APIKEY": api_key}
    resp = requests.put(url, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"listenKey keepalive failed: {resp.status_code} {resp.text}")


def fetch_account_equity(
    api_key: str, api_secret: str, base_url: str
) -> float:
    """
    GET /fapi/v2/account.
    Returns totalMarginBalance (wallet + unrealized PnL) as float.
    Falls back to totalWalletBalance if totalMarginBalance missing.
    """
    params = {"timestamp": int(time.time() * 1000), "recvWindow": 5000}
    signed = _sign(api_secret, params)
    url = f"{base_url}/fapi/v2/account?{signed}"
    headers = {"X-MBX-APIKEY": api_key}
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"account fetch failed: {resp.status_code} {resp.text}")
    data = resp.json()
    bal = data.get("totalMarginBalance") or data.get("totalWalletBalance", "0")
    try:
        return float(bal)
    except (TypeError, ValueError):
        return 0.0
