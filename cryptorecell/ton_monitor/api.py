"""Работа с P2P интеграционным API."""

import base64
import logging
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

import requests

API_ENDPOINT = "https://p2p.walletbot.me/p2p/integration-api/v1/item/online"
WALLET_DEEP_LINK = "https://t.me/wallet/start?startapp=v2-{target}"

logger = logging.getLogger(__name__)


def _safe_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_payments(payments: Optional[Iterable]) -> Tuple[str, ...]:
    if not payments:
        return ()
    normalized = []
    for payment in payments:
        if isinstance(payment, dict):
            normalized.append(payment.get("name") or payment.get("title") or str(payment))
        else:
            normalized.append(str(payment))
    return tuple(normalized)


@dataclass(frozen=True)
class SellOffer:
    price: float
    min_amount: Optional[float]
    max_amount: Optional[float]
    lot_id: Optional[str]
    lot_number: Optional[str]
    nickname: Optional[str]
    payment_methods: Tuple[str, ...]
    trade_limits: Optional[str]
    url: Optional[str]
    has_direct_url: bool


@dataclass(frozen=True)
class MarketSnapshot:
    best_price: Optional[float]
    offers: Tuple[SellOffer, ...]


def _build_offer_deeplink(ad: Dict) -> Optional[str]:
    offer_id = ad.get("id")
    user_id = ad.get("userId")
    if offer_id is None or user_id is None:
        return None
    payload = f"operation=offerid_{offer_id}_{user_id}"
    encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
    return WALLET_DEEP_LINK.format(target=encoded)


def _extract_url(ad: Dict) -> tuple[Optional[str], bool]:
    for key in ("url", "advertisementUrl", "link", "adUrl", "href"):
        value = ad.get(key)
        if isinstance(value, str) and value:
            return value, True
    deeplink = _build_offer_deeplink(ad)
    if deeplink:
        return deeplink, True
    return None, False


def _build_offer(ad: Dict) -> Optional[SellOffer]:
    price = _safe_float(ad.get("price"))
    if price is None:
        return None
    min_amount = _safe_float(ad.get("minAmount"))
    max_amount = _safe_float(ad.get("maxAmount"))
    lot_id = str(ad.get("id")).strip() if ad.get("id") is not None else None
    lot_number = str(ad.get("number")).strip() if ad.get("number") is not None else None
    nickname = ad.get("nickname") or ad.get("userName") or ad.get("accountName")
    payment_methods = _normalize_payments(ad.get("payments"))
    trade_limits = ad.get("tradeLimits")
    if not trade_limits:
        trade_limits = ad.get("limits")
    url, has_direct_url = _extract_url(ad)
    return SellOffer(
        price=price,
        min_amount=min_amount,
        max_amount=max_amount,
        lot_id=lot_id,
        lot_number=lot_number,
        nickname=nickname,
        payment_methods=payment_methods,
        trade_limits=trade_limits,
        url=url,
        has_direct_url=has_direct_url,
    )


def fetch_sell_offers(
    api_key: str,
    crypto: str,
    fiat: str,
    page_size: int = 20,
    timeout: int = 15,
) -> MarketSnapshot:
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "cryptoCurrency": crypto,
        "fiatCurrency": fiat,
        "side": "SELL",
        "page": 1,
        "pageSize": page_size,
    }

    try:
        response = requests.post(API_ENDPOINT, json=payload, headers=headers, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.debug("Ошибка запроса P2P API: %s", exc)
        raise

    body = response.json()
    ads = body.get("data") or []
    offers: List[SellOffer] = []
    for ad in ads:
        offer = _build_offer(ad)
        if offer:
            offers.append(offer)

    offers.sort(key=lambda offer: offer.price)
    best_price = offers[0].price if offers else None
    return MarketSnapshot(best_price=best_price, offers=tuple(offers))
