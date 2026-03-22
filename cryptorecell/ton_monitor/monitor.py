"""Основная логика мониторинга и оповещений."""

import argparse
import html
import logging
import sys
import time
from dataclasses import dataclass
from typing import Optional

from ton_monitor.api import MarketSnapshot, SellOffer, fetch_sell_offers
from ton_monitor.config import MonitorConfig
from ton_monitor.notifier import InlineButton, Notifier, SentMessage, TelegramNotifier

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
logging.basicConfig(stream=sys.stdout, level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrackedOfferMessage:
    offer: SellOffer
    sent_message: SentMessage


def _compute_threshold(capture_price: float, config: MonitorConfig) -> tuple[float, str]:
    if config.drop_threshold_amount and config.drop_threshold_amount > 0:
        threshold_price = max(capture_price - config.drop_threshold_amount, 0.0)
        description = f"падение ≥ {config.drop_threshold_amount:.2f} {config.fiat_currency}"
    else:
        threshold_price = capture_price * (1 - config.drop_threshold_pct / 100)
        description = f"падение ≥ {config.drop_threshold_pct:.2f}%"
    return threshold_price, description


def _offer_key(offer: SellOffer) -> str:
    if offer.url:
        return offer.url
    return "|".join(
        (
            offer.lot_number or "",
            offer.lot_id or "",
            offer.nickname or "",
            f"{offer.price:.8f}",
            "" if offer.min_amount is None else f"{offer.min_amount:.2f}",
            "" if offer.max_amount is None else f"{offer.max_amount:.2f}",
            ",".join(offer.payment_methods),
        )
    )


def _build_offer_buttons(offers: tuple[SellOffer, ...], limit: int) -> list[InlineButton]:
    buttons: list[InlineButton] = []
    for idx, offer in enumerate(offers[: limit or 1], start=1):
        if offer.url:
            buttons.append(InlineButton(text=f"Лот {idx}", kind="url", value=offer.url))
    return buttons


def _format_lot_range(config: MonitorConfig) -> str:
    return f"{config.min_lot_limit:.2f}–{config.max_lot_limit:.2f} {config.fiat_currency}"


def _describe_offers(offers: tuple[SellOffer, ...], limit: int, fiat: str) -> str:
    if not offers:
        return "нет доступных лотов."

    description_lines = []
    for idx, offer in enumerate(offers[: limit or 1], start=1):
        min_amount = f"{offer.min_amount:.2f}" if offer.min_amount is not None else "?"
        max_amount = f"{offer.max_amount:.2f}" if offer.max_amount is not None else "?"
        payments = ", ".join(offer.payment_methods) if offer.payment_methods else "любая"
        nickname = offer.nickname or f"лот #{idx}"
        description_lines.append(f"{idx}. {nickname} • {offer.price:.2f} {fiat} • {payments}")
        description_lines.append(f"   {min_amount}–{max_amount} {fiat}")
        if offer.trade_limits:
            description_lines.append(f"   Лимиты: {offer.trade_limits}")
    return "\n".join(description_lines)


def _send_alert(
    price: float,
    snapshot: MarketSnapshot,
    config: MonitorConfig,
    threshold_desc: str,
    threshold_price: float,
    notifier: Optional[Notifier],
) -> None:
    lots_text = _describe_offers(snapshot.offers, config.lot_report_limit, config.fiat_currency)
    buttons = _build_offer_buttons(snapshot.offers, config.lot_report_limit)
    message = (
        f"🔻 Низкая цена TON/{config.fiat_currency}: {price:.2f}.\n"
        f"Порог: {threshold_desc} (≤ {threshold_price:.2f}).\n"
        "Надо закупаться?\n"
        f"Лучшие лоты в диапазоне {_format_lot_range(config)}:\n"
        f"{lots_text}"
    )
    logger.warning(
        "Порог %s достигнут: %.2f %s (порог %.2f). Лоты:\n%s",
        threshold_desc,
        price,
        config.fiat_currency,
        threshold_price,
        lots_text,
    )
    if notifier:
        notifier.notify(message, buttons=buttons)


def _send_new_offer(
    offer: SellOffer,
    config: MonitorConfig,
    notifier: Optional[Notifier],
) -> Optional[TrackedOfferMessage]:
    offer_text = _describe_offers((offer,), 1, config.fiat_currency)
    buttons = _build_offer_buttons((offer,), 1)
    message = (
        f"Новый лот в диапазоне {_format_lot_range(config)}:\n"
        f"{offer_text}"
    )
    logger.info("Обнаружен новый лот:\n%s", offer_text)
    if notifier:
        sent_message = notifier.notify(message, buttons=buttons)
        if sent_message:
            return TrackedOfferMessage(offer=offer, sent_message=sent_message)
    return None


def _format_sold_offer_message(offer: SellOffer, config: MonitorConfig) -> str:
    offer_text = _describe_offers((offer,), 1, config.fiat_currency)
    return (
        "Продано.\n"
        f"Лот больше не доступен в диапазоне {_format_lot_range(config)}.\n"
        f"{offer_text}"
    )


def _mark_offer_as_sold(
    tracked_offer: TrackedOfferMessage,
    config: MonitorConfig,
    notifier: Optional[Notifier],
) -> None:
    if not notifier:
        return
    notifier.edit(
        tracked_offer.sent_message,
        _format_sold_offer_message(tracked_offer.offer, config),
        buttons=None,
    )


def _format_snapshot_message(
    price: Optional[float],
    offers: tuple[SellOffer, ...],
    config: MonitorConfig,
    prefix: str,
    sold_offers: tuple[SellOffer, ...] = (),
) -> str:
    if offers:
        lots_text = _describe_offers(offers, config.lot_report_limit, config.fiat_currency)
    else:
        lots_text = "нет доступных лотов."

    message = (
        f"{prefix} цена TON/{config.fiat_currency}: "
        f"{price:.2f}.\n" if price is not None else f"{prefix} цена TON/{config.fiat_currency}: недоступна.\n"
    )
    message += (
        f"Лоты в диапазоне {_format_lot_range(config)}:\n"
        f"{lots_text}"
    )
    if sold_offers:
        sold_text = _describe_offers(sold_offers, config.lot_report_limit, config.fiat_currency)
        message += f"\n\nПродано:\n{sold_text}"
    return message


def _send_snapshot(
    snapshot: MarketSnapshot,
    config: MonitorConfig,
    notifier: Optional[Notifier],
    prefix: str,
    target_chat_id: Optional[str] = None,
    sold_offers: tuple[SellOffer, ...] = (),
    sent_message: Optional[SentMessage] = None,
) -> Optional[SentMessage]:
    price = snapshot.offers[0].price if snapshot.offers else snapshot.best_price
    message = _format_snapshot_message(price, snapshot.offers, config, prefix, sold_offers=sold_offers)
    buttons = _build_offer_buttons(snapshot.offers, config.lot_report_limit)
    logger.info(message)
    if not notifier:
        return sent_message
    if sent_message and notifier.edit(sent_message, message, buttons=buttons or None):
        return sent_message
    return notifier.notify(message, chat_id=target_chat_id, buttons=buttons or None)

def _filter_offers_by_limit(
    offers: tuple[SellOffer, ...],
    min_limit: float,
    max_limit: float,
) -> tuple[SellOffer, ...]:
    filtered_offers = []
    for offer in offers:
        if offer.min_amount is not None and offer.min_amount > max_limit:
            continue
        if offer.max_amount is not None and offer.max_amount < min_limit:
            continue
        filtered_offers.append(offer)
    return tuple(filtered_offers)


def run_monitor(config: MonitorConfig, notifier: Optional[Notifier] = None) -> None:
    """Запускает бесконечный цикл проверки цены."""
    logger.info(
        "Старт P2P мониторинга TON/%s (интервал %d сек, текущий отчёт: %d лота в диапазоне %s, порог %s).",
        config.fiat_currency,
        config.poll_interval,
        config.lot_report_limit,
        _format_lot_range(config),
        f"{config.drop_threshold_pct:.2f}% / {config.drop_threshold_amount or 'auto'}",
    )

    capture_price: Optional[float] = None
    previous_offer_keys: Optional[set[str]] = None
    tracked_offer_messages: dict[str, TrackedOfferMessage] = {}
    auto_report_message: Optional[SentMessage] = None
    previous_report_offers: dict[str, SellOffer] = {}

    while True:
        try:
            snapshot = fetch_sell_offers(
                config.api_key,
                config.crypto_currency,
                config.fiat_currency,
                page_size=config.page_size,
            )
        except Exception as exc:
            logger.error("Ошибка запроса P2P API: %s", exc)
            time.sleep(config.poll_interval)
            continue

        eligible_offers = _filter_offers_by_limit(
            snapshot.offers,
            config.min_lot_limit,
            config.max_lot_limit,
        )
        if not eligible_offers:
            for tracked_offer in tracked_offer_messages.values():
                _mark_offer_as_sold(tracked_offer, config, notifier)
            tracked_offer_messages.clear()
            previous_offer_keys = set()
            sold_offers = tuple(previous_report_offers.values())
            if config.report_every_cycle:
                auto_report_message = _send_snapshot(
                    MarketSnapshot(best_price=None, offers=()),
                    config,
                    notifier,
                    prefix="Текущий курс",
                    sold_offers=sold_offers,
                    sent_message=auto_report_message,
                )
            previous_report_offers = {}
            logger.warning(
                "Нет лотов в диапазоне %s, повтор через %d сек.",
                _format_lot_range(config),
                config.poll_interval,
            )
            time.sleep(config.poll_interval)
            continue

        price = eligible_offers[0].price
        if price is None:
            logger.info("Не удалось получить цену; повтор через %d сек.", config.poll_interval)
            time.sleep(config.poll_interval)
            continue

        current_offer_keys = {_offer_key(offer) for offer in eligible_offers}
        if previous_offer_keys is None:
            previous_offer_keys = current_offer_keys
        else:
            for offer in eligible_offers:
                offer_key = _offer_key(offer)
                if offer_key not in previous_offer_keys:
                    tracked_offer = _send_new_offer(offer, config, notifier)
                    if tracked_offer:
                        tracked_offer_messages[offer_key] = tracked_offer
            disappeared_offer_keys = set(tracked_offer_messages) - current_offer_keys
            for disappeared_offer_key in disappeared_offer_keys:
                tracked_offer = tracked_offer_messages.pop(disappeared_offer_key)
                _mark_offer_as_sold(tracked_offer, config, notifier)
            previous_offer_keys = current_offer_keys

        if capture_price is None:
            capture_price = price
            logger.info("Установлена стартовая цена %.2f %s.", price, config.fiat_currency)
        else:
            if price < capture_price:
                capture_price = price
            logger.info("Лучшая цена TON/%s: %.2f (эталон %.2f).", config.fiat_currency, price, capture_price)
            threshold_price, threshold_desc = _compute_threshold(capture_price, config)
            if price <= threshold_price:
                filtered_snapshot = MarketSnapshot(best_price=price, offers=eligible_offers)
                _send_alert(price, filtered_snapshot, config, threshold_desc, threshold_price, notifier)
                capture_price = price

        if config.report_every_cycle and notifier:
            report_offers = eligible_offers[: config.lot_report_limit]
            report_offer_keys = {_offer_key(offer): offer for offer in report_offers}
            sold_offers = tuple(
                previous_report_offers[offer_key]
                for offer_key in previous_report_offers
                if offer_key not in report_offer_keys
            )
            periodic_snapshot = MarketSnapshot(best_price=price, offers=report_offers)
            auto_report_message = _send_snapshot(
                periodic_snapshot,
                config,
                notifier,
                prefix="Текущий курс",
                sold_offers=sold_offers,
                sent_message=auto_report_message,
            )
            previous_report_offers = report_offer_keys

        if notifier:
            try:
                notifier.check_commands(
                    lambda chat_id: send_snapshot_now(
                        config,
                        notifier,
                        target_chat_id=chat_id,
                    )
                )
            except Exception as exc:
                logger.debug("Telegram: обработка команд завершилась ошибкой: %s", exc)
        time.sleep(config.poll_interval)


def create_notifier(config: MonitorConfig) -> Optional[Notifier]:
    if config.telegram_token and config.telegram_chat_id:
        return TelegramNotifier(config.telegram_token, config.telegram_chat_id)
    if config.telegram_token and not config.telegram_chat_id:
        logger.warning("Telegram не настроен: отсутствует TELEGRAM_CHAT_ID.")
    elif config.telegram_chat_id and not config.telegram_token:
        logger.warning("Telegram не настроен: отсутствует TELEGRAM_BOT_TOKEN.")
    else:
        logger.warning("Telegram не настроен: отсутствуют TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID.")
    return None


def send_snapshot_now(
    config: MonitorConfig,
    notifier: Optional[Notifier],
    target_chat_id: Optional[str] = None,
) -> None:
    try:
        snapshot = fetch_sell_offers(
            config.api_key,
            config.crypto_currency,
            config.fiat_currency,
            page_size=config.page_size,
        )
    except Exception as exc:
        logger.error("Ошибка получения лотов: %s", exc)
        return

    eligible_offers = _filter_offers_by_limit(
        snapshot.offers,
        config.min_lot_limit,
        config.max_lot_limit,
    )
    filtered_snapshot = MarketSnapshot(
        best_price=eligible_offers[0].price if eligible_offers else None,
        offers=eligible_offers,
    )
    _send_snapshot(
        filtered_snapshot,
        config,
        notifier,
        prefix="Срочный отчёт",
        target_chat_id=target_chat_id,
    )


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Мониторинг Toncoin P2P с уведомлениями")
    parser.add_argument(
        "--send-now",
        action="store_true",
        help="однократно отправить текущие лоты в Telegram/лог, не дожидаясь падения",
    )
    args = parser.parse_args(argv)

    try:
        config = MonitorConfig.from_env()
    except ValueError as exc:
        logger.error(exc)
        sys.exit(1)

    notifier = create_notifier(config)
    if args.send_now:
        send_snapshot_now(config, notifier)
        return

    run_monitor(config, notifier)
