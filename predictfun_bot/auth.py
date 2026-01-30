from __future__ import annotations

from .config import Config
from .predictfun_client import PredictFunClient
from .state import StateStore


class AuthError(RuntimeError):
    pass


class AuthManager:
    def __init__(self, config: Config, client: PredictFunClient, state: StateStore) -> None:
        self._config = config
        self._client = client
        self._state = state
        self._builder = None

    def ensure_jwt(self, force_refresh: bool = False) -> str | None:
        if self._config.predictfun_jwt:
            self._client.set_jwt(self._config.predictfun_jwt)
            return self._config.predictfun_jwt
        cached = None if force_refresh else self._state.get_auth_jwt()
        if cached:
            self._client.set_jwt(cached)
            return cached
        token = self._fetch_jwt()
        if token:
            self._state.set_auth_jwt(token)
            self._client.set_jwt(token)
        return token

    def _fetch_jwt(self) -> str:
        if not self._config.predictfun_api_key:
            raise AuthError("PREDICTFUN_API_KEY is required to fetch JWT.")
        auth_mode = self._config.predictfun_auth_mode.lower().strip()
        message = self._client.get_auth_message()
        if auth_mode == "predict":
            return self._sign_predict_account(message)
        if auth_mode == "eoa":
            return self._sign_eoa(message)
        raise AuthError(f"Unknown PREDICTFUN_AUTH_MODE: {self._config.predictfun_auth_mode}")

    def _sign_predict_account(self, message: str) -> str:
        if not self._config.predictfun_privy_wallet_private_key:
            raise AuthError("PREDICTFUN_PRIVY_WALLET_PRIVATE_KEY is required.")
        if not self._config.predictfun_predict_account_address:
            raise AuthError("PREDICTFUN_PREDICT_ACCOUNT_ADDRESS is required.")
        builder = self._get_predict_builder()
        signature = builder.sign_predict_account_message(message)
        payload = {
            "signer": self._config.predictfun_predict_account_address,
            "message": message,
            "signature": signature,
        }
        return self._client.post_auth(payload)

    def _sign_eoa(self, message: str) -> str:
        if not self._config.predictfun_wallet_private_key:
            raise AuthError("PREDICTFUN_WALLET_PRIVATE_KEY is required.")
        try:
            from eth_account import Account
            from eth_account.messages import encode_defunct
        except ImportError as exc:
            raise AuthError("Missing eth-account dependency. Install eth-account.") from exc
        signer = Account.from_key(self._config.predictfun_wallet_private_key)
        signable = encode_defunct(text=message)
        signed = signer.sign_message(signable)
        payload = {
            "signer": signer.address,
            "message": message,
            "signature": signed.signature.hex(),
        }
        return self._client.post_auth(payload)

    def _get_predict_builder(self):
        if self._builder is not None:
            return self._builder
        try:
            from predict_sdk import ChainId, OrderBuilder, OrderBuilderOptions
        except ImportError as exc:
            raise AuthError("Missing predict-sdk dependency. Install predict-sdk.") from exc
        chain_id = self._resolve_chain_id()
        options = OrderBuilderOptions(predict_account=self._config.predictfun_predict_account_address)
        self._builder = OrderBuilder.make(
            chain_id,
            self._config.predictfun_privy_wallet_private_key,
            options,
        )
        return self._builder

    def _resolve_chain_id(self):
        from predict_sdk import ChainId

        if self._config.predictfun_chain_id == 97:
            return ChainId.BNB_TESTNET
        return ChainId.BNB_MAINNET
