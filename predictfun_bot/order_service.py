from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import math

from .config import Config
from .models import Position, TradeCandidate


class OrderServiceError(RuntimeError):
    pass


class OrderService:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._builder = None
        self._approvals_set = False

    def ensure_approvals(self) -> None:
        if not self._config.predictfun_set_approvals:
            return
        if self._approvals_set:
            return
        builder = self._get_builder()
        result = builder.set_approvals()
        if not getattr(result, "success", False):
            raise OrderServiceError("Failed to set approvals.")
        self._approvals_set = True

    def build_entry_order(
        self, candidate: TradeCandidate, size_usd: float
    ) -> tuple[dict, int, int]:
        builder = self._get_builder()
        price_per_share = _round_price(candidate.price, candidate.decimal_precision)
        price_per_share_wei = _price_to_wei(price_per_share, candidate.decimal_precision)
        if price_per_share_wei <= 0:
            raise OrderServiceError("Invalid price per share.")
        quantity = size_usd / price_per_share if price_per_share > 0 else 0.0
        quantity_wei = _to_wei(quantity)
        quantity_wei = _quantize_quantity_wei(quantity_wei, price_per_share_wei)
        if quantity_wei <= 0:
            raise OrderServiceError("Order size too small after precision rounding.")
        amounts, quantity_wei = _limit_amounts(
            builder, "buy", price_per_share_wei, quantity_wei
        )
        order = builder.build_order(
            "LIMIT",
            _build_input(
                side="buy",
                token_id=candidate.token_id,
                maker_amount=amounts.maker_amount,
                taker_amount=amounts.taker_amount,
                fee_rate_bps=candidate.fee_rate_bps,
                expires_at=_expires_at(self._config.order_expiry_minutes),
            ),
        )
        payload = _build_payload(
            builder,
            order,
            price_per_share_wei=amounts.price_per_share,
            is_neg_risk=candidate.is_neg_risk,
            is_yield_bearing=candidate.is_yield_bearing,
        )
        return payload, int(amounts.taker_amount), int(amounts.price_per_share)

    def build_exit_order(
        self, position: Position, price: float
    ) -> tuple[dict, int, int]:
        builder = self._get_builder()
        price_per_share = _round_price(price, position.decimal_precision)
        price_per_share_wei = _price_to_wei(price_per_share, position.decimal_precision)
        if price_per_share_wei <= 0:
            raise OrderServiceError("Invalid exit price per share.")
        quantity_wei = _quantize_quantity_wei(int(position.quantity_wei), price_per_share_wei)
        if quantity_wei <= 0:
            raise OrderServiceError("Exit size too small after precision rounding.")
        amounts, quantity_wei = _limit_amounts(
            builder, "sell", price_per_share_wei, quantity_wei
        )
        order = builder.build_order(
            "LIMIT",
            _build_input(
                side="sell",
                token_id=position.token_id,
                maker_amount=amounts.maker_amount,
                taker_amount=amounts.taker_amount,
                fee_rate_bps=position.fee_rate_bps,
                expires_at=_expires_at(self._config.order_expiry_minutes),
            ),
        )
        payload = _build_payload(
            builder,
            order,
            price_per_share_wei=amounts.price_per_share,
            is_neg_risk=position.is_neg_risk,
            is_yield_bearing=position.is_yield_bearing,
        )
        return payload, int(quantity_wei), int(amounts.price_per_share)

    def build_limit_order(
        self,
        *,
        side: str,
        token_id: str,
        price: float,
        size_usd: float,
        fee_rate_bps: int,
        decimal_precision: int,
        is_neg_risk: bool,
        is_yield_bearing: bool,
        expiry_minutes: int | None = None,
    ) -> tuple[dict, int, int, str]:
        builder = self._get_builder()
        price_per_share = _round_price(price, decimal_precision)
        price_per_share_wei = _price_to_wei(price_per_share, decimal_precision)
        if price_per_share_wei <= 0:
            raise OrderServiceError("Invalid price per share.")
        quantity = size_usd / price_per_share if price_per_share > 0 else 0.0
        quantity_wei = _to_wei(quantity)
        quantity_wei = _quantize_quantity_wei(quantity_wei, price_per_share_wei)
        if quantity_wei <= 0:
            raise OrderServiceError("Order size too small after precision rounding.")
        amounts, quantity_wei = _limit_amounts(
            builder,
            "buy" if side.lower() == "buy" else "sell",
            price_per_share_wei,
            quantity_wei,
        )
        order = builder.build_order(
            "LIMIT",
            _build_input(
                side="buy" if side.lower() == "buy" else "sell",
                token_id=token_id,
                maker_amount=amounts.maker_amount,
                taker_amount=amounts.taker_amount,
                fee_rate_bps=fee_rate_bps,
                expires_at=_expires_at(expiry_minutes or self._config.order_expiry_minutes),
            ),
        )
        typed_data = builder.build_typed_data(order, is_neg_risk=is_neg_risk, is_yield_bearing=is_yield_bearing)
        signed = builder.sign_typed_data_order(typed_data)
        order_hash = builder.build_typed_data_hash(typed_data)
        order_payload = _signed_order_payload(signed, order_hash)
        payload = {
            "data": {
                "order": order_payload,
                "pricePerShare": str(amounts.price_per_share),
                "strategy": "LIMIT",
            }
        }
        return payload, int(amounts.taker_amount), int(amounts.price_per_share), order_hash

    def estimate_quantity_wei(self, price: float, size_usd: float, decimal_precision: int) -> int:
        price_per_share = _round_price(price, decimal_precision)
        price_per_share_wei = _price_to_wei(price_per_share, decimal_precision)
        if price_per_share_wei <= 0 or price_per_share <= 0:
            return 0
        quantity = size_usd / price_per_share
        quantity_wei = _to_wei(quantity)
        return _quantize_quantity_wei(quantity_wei, price_per_share_wei)

    def _get_builder(self):
        if self._builder is not None:
            return self._builder
        try:
            from predict_sdk import ChainId, OrderBuilder, OrderBuilderOptions
        except ImportError as exc:
            raise OrderServiceError("Missing predict-sdk dependency. Install predict-sdk.") from exc
        chain_id = ChainId.BNB_TESTNET if self._config.predictfun_chain_id == 97 else ChainId.BNB_MAINNET
        auth_mode = self._config.predictfun_auth_mode.lower().strip()
        if auth_mode == "predict":
            if not self._config.predictfun_privy_wallet_private_key:
                raise OrderServiceError("PREDICTFUN_PRIVY_WALLET_PRIVATE_KEY is required.")
            if not self._config.predictfun_predict_account_address:
                raise OrderServiceError("PREDICTFUN_PREDICT_ACCOUNT_ADDRESS is required.")
            options = OrderBuilderOptions(predict_account=self._config.predictfun_predict_account_address)
            self._builder = OrderBuilder.make(
                chain_id,
                self._config.predictfun_privy_wallet_private_key,
                options,
            )
        elif auth_mode == "eoa":
            if not self._config.predictfun_wallet_private_key:
                raise OrderServiceError("PREDICTFUN_WALLET_PRIVATE_KEY is required.")
            self._builder = OrderBuilder.make(chain_id, self._config.predictfun_wallet_private_key)
        else:
            raise OrderServiceError(f"Unsupported auth mode: {self._config.predictfun_auth_mode}")
        return self._builder


def _build_payload(builder, order, price_per_share_wei: int, is_neg_risk: bool, is_yield_bearing: bool) -> dict:
    typed_data = builder.build_typed_data(order, is_neg_risk=is_neg_risk, is_yield_bearing=is_yield_bearing)
    signed = builder.sign_typed_data_order(typed_data)
    order_hash = builder.build_typed_data_hash(typed_data)
    order_payload = _signed_order_payload(signed, order_hash)
    return {
        "data": {
            "order": order_payload,
            "pricePerShare": str(price_per_share_wei),
            "strategy": "LIMIT",
        }
    }


def _signed_order_payload(signed, order_hash: str) -> dict:
    return {
        "hash": order_hash,
        "salt": str(signed.salt),
        "maker": signed.maker,
        "signer": signed.signer,
        "taker": signed.taker,
        "tokenId": str(signed.token_id),
        "makerAmount": str(signed.maker_amount),
        "takerAmount": str(signed.taker_amount),
        "expiration": str(signed.expiration),
        "nonce": str(signed.nonce),
        "feeRateBps": str(signed.fee_rate_bps),
        "side": int(signed.side.value if hasattr(signed.side, "value") else signed.side),
        "signatureType": int(
            signed.signature_type.value if hasattr(signed.signature_type, "value") else signed.signature_type
        ),
        "signature": signed.signature,
    }


def _limit_input(*, side: str, price_per_share_wei: int, quantity_wei: int):
    from predict_sdk.types import LimitHelperInput, Side

    return LimitHelperInput(
        side=Side.BUY if side == "buy" else Side.SELL,
        price_per_share_wei=int(price_per_share_wei),
        quantity_wei=int(quantity_wei),
    )


def _build_input(
    *,
    side: str,
    token_id: str,
    maker_amount: int,
    taker_amount: int,
    fee_rate_bps: int,
    expires_at: datetime,
):
    from predict_sdk.types import BuildOrderInput, Side

    return BuildOrderInput(
        side=Side.BUY if side == "buy" else Side.SELL,
        token_id=str(token_id),
        maker_amount=int(maker_amount),
        taker_amount=int(taker_amount),
        fee_rate_bps=int(fee_rate_bps),
        expires_at=expires_at,
    )


def _expires_at(minutes: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


def _to_wei(value: float) -> int:
    return int(Decimal(str(value)) * Decimal("1e18"))


def _round_price(price: float, decimal_precision: int) -> float:
    precision = min(max(decimal_precision, 0), 5)
    return round(price, precision)


def _price_to_wei(price: float, decimal_precision: int) -> int:
    precision = min(max(decimal_precision, 0), 5)
    price_str = f"{price:.{precision}f}"
    return int(Decimal(price_str) * Decimal("1e18"))


def _quantize_quantity_wei(quantity_wei: int, price_per_share_wei: int) -> int:
    if quantity_wei <= 0:
        return 0
    step = _quantity_step(price_per_share_wei)
    if step <= 0:
        return quantity_wei
    return (quantity_wei // step) * step


def _quantity_step(price_per_share_wei: int) -> int:
    base = 10**13
    if price_per_share_wei <= 0:
        return base
    price_units = price_per_share_wei // base
    if price_units <= 0:
        return base
    step_multiplier = 100000 // math.gcd(price_units, 100000)
    return base * step_multiplier


def _amounts_ok(amounts: object) -> bool:
    maker = int(getattr(amounts, "maker_amount", 0))
    taker = int(getattr(amounts, "taker_amount", 0))
    return maker % (10**13) == 0 and taker % (10**13) == 0


def _limit_amounts(builder, side: str, price_per_share_wei: int, quantity_wei: int):
    step = _quantity_step(price_per_share_wei)
    attempts = 0
    while quantity_wei > 0:
        amounts = builder.get_limit_order_amounts(
            _limit_input(
                side=side,
                price_per_share_wei=price_per_share_wei,
                quantity_wei=quantity_wei,
            )
        )
        if _amounts_ok(amounts):
            return amounts, quantity_wei
        quantity_wei -= step
        attempts += 1
        if attempts >= 5:
            break
    raise OrderServiceError("Order size too small after precision rounding.")
