"""Конфигурация окружения мониторинга."""

import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


def _coerce_int(value: Optional[str], default: int, name: str) -> int:
    if not value:
        return default

    try:
        return int(value)
    except ValueError:
        logger.warning("Переменная %s=%r не целое число, используется %d", name, value, default)
        return default


def _coerce_float(value: Optional[str], default: float, name: str) -> float:
    if not value:
        return default

    try:
        return float(value)
    except ValueError:
        logger.warning("Переменная %s=%r не число с плавающей точкой, используется %.2f", name, value, default)
        return default


def _coerce_optional_float(value: Optional[str], name: str) -> Optional[float]:
    if not value:
        return None

    try:
        return float(value)
    except ValueError:
        logger.warning("Переменная %s=%r должна быть числом, игнорируется", name, value)
        return None


@dataclass(frozen=True)
class MonitorConfig:
    api_key: str
    fiat_currency: str = "RUB"
    crypto_currency: str = "TON"
    poll_interval: int = 90
    drop_threshold_pct: float = 0.5
    drop_threshold_amount: Optional[float] = 500.0
    telegram_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    page_size: int = 20
    lot_report_limit: int = 3
    min_lot_limit: float = 500.0
    max_lot_limit: float = 5000.0
    report_every_cycle: bool = True

    @classmethod
    def from_env(cls) -> "MonitorConfig":
        api_key = os.environ.get("P2P_API_KEY")
        if not api_key:
            raise ValueError("Нужен P2P_API_KEY для доступа к интеграционному API.")

        return cls(
            api_key=api_key,
            fiat_currency=os.environ.get("P2P_FIAT", "RUB").upper(),
            crypto_currency=os.environ.get("P2P_CRYPTO", "TON").upper(),
            poll_interval=_coerce_int(os.environ.get("P2P_INTERVAL"), 90, "P2P_INTERVAL"),
            drop_threshold_pct=_coerce_float(os.environ.get("P2P_DROP_PCT"), 0.5, "P2P_DROP_PCT"),
            drop_threshold_amount=_coerce_optional_float(os.environ.get("P2P_DROP_AMOUNT"), "P2P_DROP_AMOUNT")
            or 500.0,
            telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID"),
            page_size=_coerce_int(os.environ.get("P2P_PAGE_SIZE"), 20, "P2P_PAGE_SIZE"),
            lot_report_limit=_coerce_int(os.environ.get("P2P_LOT_REPORT_LIMIT"), 3, "P2P_LOT_REPORT_LIMIT"),
            min_lot_limit=_coerce_float(os.environ.get("P2P_MIN_LOT_LIMIT"), 500.0, "P2P_MIN_LOT_LIMIT"),
            max_lot_limit=_coerce_float(os.environ.get("P2P_MAX_LOT_LIMIT"), 5000.0, "P2P_MAX_LOT_LIMIT"),
            report_every_cycle=bool(os.environ.get("P2P_REPORT_EVERY_CYCLE", "1") == "1"),
        )
