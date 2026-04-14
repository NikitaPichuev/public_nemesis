from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from decimal import Decimal
from decimal import InvalidOperation
from pathlib import Path
from typing import Any
from typing import Optional
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

from dotenv import load_dotenv
from eth_account import Account
from web3 import Web3


BASE_DIR = Path(__file__).resolve().parent


class InsufficientBalanceError(RuntimeError):
    pass

TOKENS = {
    "ETH": {
        "address": "native",
        "decimals": 18,
    },
    "WETH": {
        "address": "0x7b79995e5f793A07Bc00c21412e50Ecae098E7f9",
        "decimals": 18,
    },
    "DAI": {
        "address": "0xd67215fd6c0890493f34af3c5e4231ce98871fcb",
        "decimals": 18,
    },
    "USDC": {
        "address": "0x10279e6333f9d0EE103F4715b8aaEA75BE61464C",
        "decimals": 6,
    },
    "BTC": {
        "address": "0x2591230465a68d924fbcba5e3304c2eda0d52e5b",
        "decimals": 18,
    },
    "MINABO": {
        "address": "0xea4daaf49bd55021c23b70b194b4436f199a7606",
        "decimals": 18,
    },
    "NEMESIS": {
        "address": "0x47b7ed0e04edab477c46543bdf766acea155dd2f",
        "decimals": 18,
    },
    "NEM": {
        "address": "0x8d427943b850179300b372483aace7b887845bf3",
        "decimals": 18,
    },
    "ONE": {
        "address": "0x80d494d084087af738987f2e2807099e35867e10",
        "decimals": 18,
    },
}

SWAP_INTERMEDIATE_TOKENS = ["DAI", "USDC", "NEM", "NEMESIS", "MINABO", "ONE"]

PRESET_LIQUIDITY_PAIRS = [
    {"label": "BTC/MINABO", "token_a": "BTC", "token_b": "MINABO"},
    {"label": "BTC/NEMESIS", "token_a": "BTC", "token_b": "NEMESIS"},
    {"label": "BTC/NEM", "token_a": "BTC", "token_b": "NEM"},
    {"label": "BTC/ONE", "token_a": "BTC", "token_b": "ONE"},
]

POSITION_PAIRS = [
    {"label": "ETH/DAI", "base": "WETH", "quote": "DAI"},
    {"label": "ETH/USDC", "base": "WETH", "quote": "USDC"},
    {"label": "ETH/NEMESIS", "base": "WETH", "quote": "NEMESIS"},
    {"label": "ETH/NEM", "base": "WETH", "quote": "NEM"},
]

LP_POSITION_PAIRS = [
    {"label": "ETH/DAI", "token_a": "WETH", "token_b": "DAI", "use_eth": True},
    *[
        {"label": pair["label"], "token_a": pair["token_a"], "token_b": pair["token_b"], "use_eth": False}
        for pair in PRESET_LIQUIDITY_PAIRS
    ],
]

WETH_ABI = [
    {
        "type": "function",
        "name": "deposit",
        "inputs": [],
        "outputs": [],
        "stateMutability": "payable",
    },
    {
        "type": "function",
        "name": "balanceOf",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
    {
        "type": "function",
        "name": "allowance",
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
    {
        "type": "function",
        "name": "approve",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
    },
]


def load_raw_value_file(path: Path, prefix: str = "0x") -> Optional[str]:
    if not path.exists():
        return None

    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return None

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line and line.startswith(prefix):
            return line

    return None


def load_private_keys_file(path: Path) -> list[str]:
    if not path.exists():
        return []

    private_keys = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith("#") or "=" in value:
            continue
        if re.fullmatch(r"0x[0-9a-fA-F]{64}", value):
            private_keys.append(value)
        if re.fullmatch(r"[0-9a-fA-F]{64}", value):
            private_keys.append("0x" + value)
    return private_keys


def load_wallet_index(path: Path, wallet_count: int) -> int:
    if wallet_count <= 0:
        return 0
    if not path.exists():
        return 0
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return 0
    try:
        return int(raw) % wallet_count
    except ValueError as exc:
        raise ValueError(f"{path.name} должен содержать только номер кошелька") from exc


def save_wallet_index(path: Path, index: int) -> None:
    path.write_text(str(index), encoding="utf-8")


def select_private_key_for_address(private_keys: list[str], wallet_address: Optional[str]) -> Optional[str]:
    if not private_keys:
        return None
    if not wallet_address:
        return private_keys[0]

    target = wallet_address.lower()
    for private_key in private_keys:
        if Account.from_key(private_key).address.lower() == target:
            return private_key
    raise ValueError("В .wallet.env нет приватника, который соответствует WALLET_ADDRESS из .addresses.env")


def normalize_proxy_url(proxy_url: str) -> str:
    value = proxy_url.strip()
    if not value:
        raise ValueError("Пустой proxy URL")
    if "://" not in value:
        value = "http://" + value
    return value


def load_proxy_list(path: Path) -> list[str]:
    if not path.exists():
        return []

    proxies = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        proxies.append(normalize_proxy_url(value))
    return proxies


def load_proxy_file(path: Path) -> Optional[str]:
    proxies = load_proxy_list(path)

    if not proxies:
        return None
    return random.choice(proxies)


def mask_proxy_url(proxy_url: Optional[str]) -> str:
    if not proxy_url:
        return "disabled"

    parts = urlsplit(proxy_url)
    if "@" not in parts.netloc:
        return proxy_url

    _, host = parts.netloc.rsplit("@", 1)
    return urlunsplit((parts.scheme, "***:***@" + host, parts.path, parts.query, parts.fragment))


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_str(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name, default)
    if value is None:
        return None
    value = value.strip()
    return value or default


def env_int(name: str, default: Optional[int] = None) -> Optional[int]:
    raw = env_str(name)
    if raw is None:
        return default
    return int(raw)


def env_decimal(name: str, default: str = "0") -> Decimal:
    return Decimal(env_str(name, default) or default)


def parse_decimal(value: str, field_name: str) -> Decimal:
    normalized = value.strip().replace(",", ".")
    try:
        return Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError(f"{field_name} должна быть числом, пример: 0.001") from exc


def random_decimal_between(min_value: Decimal, max_value: Decimal) -> Decimal:
    scale = max(-min_value.as_tuple().exponent, -max_value.as_tuple().exponent, 18)
    multiplier = Decimal(10) ** scale
    min_int = int(min_value * multiplier)
    max_int = int(max_value * multiplier)
    if min_int > max_int:
        raise ValueError("Минимум диапазона больше максимума")
    return Decimal(random.randint(min_int, max_int)) / multiplier


@dataclass
class PercentAmountRange:
    min_percent: Decimal
    max_percent: Decimal

    def select_percent(self) -> Decimal:
        return random_decimal_between(self.min_percent, self.max_percent)


def wait_wallet_pause() -> None:
    delay = random.uniform(4, 10)
    print(f"\n[wallet pause] {delay:.1f} сек перед следующим кошельком...")
    time.sleep(delay)


def select_decimal_range(field_name: str, default_value: Decimal) -> Decimal:
    raw_min = input(f"{field_name} минимум [{default_value}]: ").strip()
    min_value = parse_decimal(raw_min, f"{field_name} минимум") if raw_min else default_value
    raw_max = input(f"{field_name} максимум [{min_value}]: ").strip()
    max_value = parse_decimal(raw_max, f"{field_name} максимум") if raw_max else min_value
    selected = random_decimal_between(min_value, max_value)
    if min_value == max_value:
        print(f"{field_name}: {selected}")
    else:
        print(f"{field_name}: выбран случайный размер {selected} из диапазона {min_value}-{max_value}")
    return selected


def parse_percent(value: str, field_name: str) -> Decimal:
    raw = value.strip().replace(",", ".")
    if not raw.endswith("%"):
        raise ValueError(f"{field_name} должен быть процентом, пример: 10%")
    percent = parse_decimal(raw[:-1], field_name)
    if percent <= 0 or percent > 100:
        raise ValueError(f"{field_name} должен быть больше 0% и не больше 100%")
    return percent


def select_decimal_or_percent_range(field_name: str, default_value: Decimal) -> Decimal | PercentAmountRange:
    raw_min = input(f"{field_name} минимум [{default_value}, можно 10%]: ").strip()
    if raw_min.endswith("%"):
        min_percent = parse_percent(raw_min, f"{field_name} минимум")
        raw_max = input(f"{field_name} максимум [{raw_min}, можно %]: ").strip()
        max_percent = parse_percent(raw_max, f"{field_name} максимум") if raw_max else min_percent
        if min_percent > max_percent:
            raise ValueError("Минимум процента больше максимума")
        print(f"{field_name}: будет выбран процент от баланса в диапазоне {min_percent}%-{max_percent}%")
        return PercentAmountRange(min_percent, max_percent)

    min_value = parse_decimal(raw_min, f"{field_name} минимум") if raw_min else default_value
    raw_max = input(f"{field_name} максимум [{min_value}]: ").strip()
    if raw_max.endswith("%"):
        raise ValueError("Если минимум задан числом, максимум тоже должен быть числом")
    max_value = parse_decimal(raw_max, f"{field_name} максимум") if raw_max else min_value
    selected = random_decimal_between(min_value, max_value)
    if min_value == max_value:
        print(f"{field_name}: {selected}")
    else:
        print(f"{field_name}: выбран случайный размер {selected} из диапазона {min_value}-{max_value}")
    return selected


def select_decimal_range_step(field_name: str, default_value: Decimal, step: Decimal) -> Decimal:
    raw_min = input(f"{field_name} минимум [{default_value}]: ").strip()
    min_value = parse_decimal(raw_min, f"{field_name} минимум") if raw_min else default_value
    raw_max = input(f"{field_name} максимум [{min_value}]: ").strip()
    max_value = parse_decimal(raw_max, f"{field_name} максимум") if raw_max else min_value

    min_units = int(min_value / step)
    max_units = int(max_value / step)
    if Decimal(min_units) * step != min_value or Decimal(max_units) * step != max_value:
        raise ValueError(f"{field_name} поддерживается с шагом {step}, пример: 2 или 2.5")
    if min_units > max_units:
        raise ValueError("Минимум диапазона больше максимума")

    selected = Decimal(random.randint(min_units, max_units)) * step
    if min_value == max_value:
        print(f"{field_name}: {selected}")
    else:
        print(f"{field_name}: выбран случайный размер {selected} из диапазона {min_value}-{max_value} с шагом {step}")
    return selected


def token_amount_to_wei(amount: Decimal, decimals: int) -> int:
    return int(amount * (Decimal(10) ** decimals))


def wei_to_token_amount(amount_wei: int, decimals: int) -> Decimal:
    return Decimal(amount_wei) / (Decimal(10) ** decimals)


def load_json_array(name: str) -> list[Any]:
    raw = env_str(name, "[]") or "[]"
    value = json.loads(raw)
    if not isinstance(value, list):
        raise ValueError(f"{name} должен быть JSON-массивом")
    return value


def load_json_value(name: str, default: Any) -> Any:
    raw = env_str(name)
    if raw is None:
        return default
    return json.loads(raw)


@dataclass
class Config:
    rpc_url: str
    proxy_url: Optional[str]
    chain_id: int
    send_tx: bool
    wait_for_receipt: bool
    wallet_private_key: Optional[str]
    wallet_address: Optional[str]
    action_mode: str
    contract_address: Optional[str]
    contract_abi_path: Path
    function_name: Optional[str]
    function_args: list[Any]
    tx_value_eth: Decimal
    approve_enabled: bool
    approve_token_address: Optional[str]
    approve_token_abi_path: Path
    approve_spender_address: Optional[str]
    approve_amount_wei: Optional[int]
    approvals: list[dict[str, Any]]
    gas_limit: Optional[int]
    max_fee_per_gas_gwei: Optional[Decimal]
    max_priority_fee_per_gas_gwei: Optional[Decimal]
    menu_enabled: bool
    swap_default_amount: Decimal
    auto_liquidity_pair: Optional[dict[str, str]]
    auto_liquidity_eth_budget: Decimal
    auto_liquidity_params: Optional[dict[str, Any]]
    auto_swap_params: Optional[dict[str, Any]]
    auto_position_params: Optional[dict[str, Any]]
    check_trade_positions: bool
    close_position_params: Optional[dict[str, Any]]
    remove_liquidity_params: Optional[dict[str, Any]]
    wallet_rotate: bool
    wallet_index_file: Path
    wallet_current_index: Optional[int]
    wallet_next_index: Optional[int]
    wallet_count: int
    wallet_batch_count: int

    @classmethod
    def load(cls) -> "Config":
        load_dotenv(BASE_DIR / ".env", override=False)
        load_dotenv(BASE_DIR / ".wallet.env", override=False)
        load_dotenv(BASE_DIR / ".addresses.env", override=False)

        wallet_private_keys = load_private_keys_file(BASE_DIR / ".wallet.env")
        wallet_rotate = env_bool("WALLET_ROTATE", False)
        wallet_index_file = (BASE_DIR / (env_str("WALLET_INDEX_FILE", "wallet_index.txt") or "wallet_index.txt")).resolve()
        wallet_current_index: Optional[int] = None
        wallet_next_index: Optional[int] = None
        wallet_private_key = env_str("WALLET_PRIVATE_KEY")
        wallet_address = env_str("WALLET_ADDRESS")
        if wallet_rotate and wallet_private_keys:
            wallet_current_index = load_wallet_index(wallet_index_file, len(wallet_private_keys))
            wallet_next_index = (wallet_current_index + 1) % len(wallet_private_keys)
            wallet_private_key = wallet_private_keys[wallet_current_index]
            wallet_address = Account.from_key(wallet_private_key).address
        else:
            if not wallet_address:
                wallet_address = load_raw_value_file(BASE_DIR / ".addresses.env")
            if not wallet_private_key:
                wallet_private_key = select_private_key_for_address(
                    wallet_private_keys,
                    wallet_address,
                )
        if wallet_private_key and not wallet_address:
            wallet_address = Account.from_key(wallet_private_key).address
        swap_default_amount = Decimal(env_str("SWAP_DEFAULT_AMOUNT", env_str("SWAP_ETH_AMOUNT", "0.01") or "0.01") or "0.01")
        proxy_url = env_str("PROXY_URL")
        if proxy_url:
            proxy_url = normalize_proxy_url(proxy_url)
        else:
            proxies_file = BASE_DIR / (env_str("PROXIES_FILE", "proxies.txt") or "proxies.txt")
            proxies = load_proxy_list(proxies_file)
            if proxies and wallet_rotate and wallet_current_index is not None:
                proxy_url = proxies[wallet_current_index % len(proxies)]
            elif proxies:
                proxy_url = random.choice(proxies)
            else:
                proxy_url = None

        return cls(
            rpc_url=env_str("RPC_URL", "") or "",
            proxy_url=proxy_url,
            chain_id=env_int("CHAIN_ID", 11155111) or 11155111,
            send_tx=env_bool("SEND_TX", True),
            wait_for_receipt=env_bool("WAIT_FOR_RECEIPT", True),
            wallet_private_key=wallet_private_key,
            wallet_address=wallet_address,
            action_mode=(env_str("ACTION_MODE", "call") or "call").lower(),
            contract_address=env_str("CONTRACT_ADDRESS"),
            contract_abi_path=(BASE_DIR / (env_str("CONTRACT_ABI_PATH", "contracts/nemesis_router.abi.json") or "")).resolve(),
            function_name=env_str("FUNCTION_NAME"),
            function_args=load_json_array("FUNCTION_ARGS_JSON"),
            tx_value_eth=env_decimal("TX_VALUE_ETH", "0"),
            approve_enabled=env_bool("APPROVE_ENABLED", False),
            approve_token_address=env_str("APPROVE_TOKEN_ADDRESS"),
            approve_token_abi_path=(BASE_DIR / (env_str("APPROVE_TOKEN_ABI_PATH", "contracts/erc20.abi.json") or "")).resolve(),
            approve_spender_address=env_str("APPROVE_SPENDER_ADDRESS"),
            approve_amount_wei=env_int("APPROVE_AMOUNT_WEI"),
            approvals=load_json_value("APPROVALS_JSON", []),
            gas_limit=env_int("GAS_LIMIT"),
            max_fee_per_gas_gwei=Decimal(env_str("MAX_FEE_PER_GAS_GWEI", "0") or "0") or None,
            max_priority_fee_per_gas_gwei=Decimal(env_str("MAX_PRIORITY_FEE_PER_GAS_GWEI", "0") or "0") or None,
            menu_enabled=env_bool("MENU_ENABLED", True),
            swap_default_amount=swap_default_amount,
            auto_liquidity_pair=None,
            auto_liquidity_eth_budget=Decimal("0"),
            auto_liquidity_params=None,
            auto_swap_params=None,
            auto_position_params=None,
            check_trade_positions=False,
            close_position_params=None,
            remove_liquidity_params=None,
            wallet_rotate=wallet_rotate,
            wallet_index_file=wallet_index_file,
            wallet_current_index=wallet_current_index,
            wallet_next_index=wallet_next_index,
            wallet_count=len(wallet_private_keys),
            wallet_batch_count=env_int("WALLET_BATCH_COUNT", 1) or 1,
        )

    def commit_wallet_rotation(self) -> None:
        if not self.wallet_rotate or self.wallet_next_index is None:
            return
        save_wallet_index(self.wallet_index_file, self.wallet_next_index)
        print(f"Wallet rotation: следующий запуск возьмёт кошелёк #{self.wallet_next_index + 1}/{self.wallet_count}")

    def apply_menu_choice(
        self,
        choice: str,
        swap_params: Optional[dict[str, Any]] = None,
        liquidity_params: Optional[dict[str, Any]] = None,
        preset_liquidity_params: Optional[dict[str, Any]] = None,
        position_params: Optional[dict[str, Any]] = None,
        close_position_params: Optional[dict[str, Any]] = None,
        remove_liquidity_params: Optional[dict[str, Any]] = None,
    ) -> None:
        router = "0xa1f78bed1a79b9aec972e373e0e7f63d8cace4a8"

        self.action_mode = "send"
        self.contract_address = router
        self.contract_abi_path = (BASE_DIR / "contracts/nemesis_router.abi.json").resolve()

        if choice == "1":
            if not swap_params:
                raise ValueError("Не заданы параметры свапа")
            self.apply_swap_choice(swap_params, router)
            return

        if choice == "2":
            if not liquidity_params:
                raise ValueError("Не заданы параметры ликвидности")
            self.apply_liquidity_choice(liquidity_params, router)
            return

        if choice == "3":
            if not preset_liquidity_params:
                raise ValueError("Не заданы параметры готовой пары")
            self.apply_preset_liquidity_choice(preset_liquidity_params)
            return

        if choice == "4":
            self.function_name = "checkPositions"
            self.function_args = []
            self.action_mode = "call"
            self.approve_enabled = False
            self.approvals = []
            return

        if choice == "5":
            if not position_params:
                raise ValueError("Не заданы параметры позиции")
            self.apply_position_choice(position_params)
            return

        if choice == "6":
            self.function_name = "checkTradePositions"
            self.function_args = []
            self.action_mode = "call"
            self.contract_address = "0xbf301098692a47cf6861877e9acef55c9653ae50"
            self.contract_abi_path = (BASE_DIR / "contracts/nemesis_factory.abi.json").resolve()
            self.approve_enabled = False
            self.approvals = []
            self.check_trade_positions = True
            return

        if choice == "7":
            if not close_position_params:
                raise ValueError("Не заданы параметры закрытия позиции")
            self.function_name = "closePosition"
            self.function_args = []
            self.action_mode = "send"
            self.contract_address = "0xbf301098692a47cf6861877e9acef55c9653ae50"
            self.contract_abi_path = (BASE_DIR / "contracts/nemesis_factory.abi.json").resolve()
            self.tx_value_eth = Decimal("0")
            self.approve_enabled = False
            self.approvals = []
            self.close_position_params = close_position_params
            return

        if choice == "8":
            if not remove_liquidity_params:
                raise ValueError("Не заданы параметры вывода ликвидности")
            self.function_name = "removeLiquidity"
            self.function_args = []
            self.action_mode = "send"
            self.contract_address = router
            self.contract_abi_path = (BASE_DIR / "contracts/nemesis_router.abi.json").resolve()
            self.tx_value_eth = Decimal("0")
            self.approve_enabled = False
            self.approvals = []
            self.remove_liquidity_params = remove_liquidity_params
            return

        raise ValueError("Выбери 1, 2, 3, 4, 5, 6, 7, 8 или 9")

    def apply_position_choice(self, position_params: dict[str, Any]) -> None:
        self.function_name = "openPosition"
        self.function_args = []
        self.action_mode = "send"
        self.contract_address = "0xbf301098692a47cf6861877e9acef55c9653ae50"
        self.contract_abi_path = (BASE_DIR / "contracts/nemesis_factory.abi.json").resolve()
        self.tx_value_eth = Decimal("0")
        self.approve_enabled = False
        self.approvals = []
        self.auto_position_params = position_params

    def apply_preset_liquidity_choice(self, preset_liquidity_params: dict[str, Any]) -> None:
        self.function_name = "autoSwapAndAddLiquidity"
        self.function_args = []
        self.tx_value_eth = Decimal("0")
        self.approve_enabled = False
        self.approvals = []
        self.auto_liquidity_pair = preset_liquidity_params["pair"]
        self.auto_liquidity_eth_budget = preset_liquidity_params["eth_budget"]

    def apply_swap_choice(self, swap_params: dict[str, Any], router: str) -> None:
        from_symbol = swap_params["from_symbol"]
        to_symbol = swap_params["to_symbol"]
        amount = swap_params["amount"]
        from_token = TOKENS[from_symbol]
        to_token = TOKENS[to_symbol]

        if from_symbol == to_symbol:
            raise ValueError("Нельзя свапать токен сам в себя")

        if {from_symbol, to_symbol} == {"ETH", "WETH"}:
            raise ValueError("ETH <-> WETH это wrap/unwrap, не swap через router")

        if isinstance(amount, PercentAmountRange):
            self.function_name = "autoSwapPercent"
            self.function_args = []
            self.action_mode = "send"
            self.contract_address = router
            self.contract_abi_path = (BASE_DIR / "contracts/nemesis_router.abi.json").resolve()
            self.tx_value_eth = Decimal("0")
            self.approve_enabled = False
            self.approvals = []
            self.auto_swap_params = swap_params
            return

        amount_in = token_amount_to_wei(amount, int(from_token["decimals"]))
        self.tx_value_eth = Decimal("0")
        self.approve_enabled = False
        self.approvals = []

        if from_symbol == "ETH":
            self.function_name = "swapExactETHForTokens"
            self.function_args = [
                "$QUOTE_MIN_5PCT",
                [TOKENS["WETH"]["address"], to_token["address"]],
                "$WALLET",
                "$DEADLINE_20M",
            ]
            self.tx_value_eth = amount
            return

        if to_symbol == "ETH":
            self.function_name = "swapExactTokensForETH"
            self.function_args = [
                amount_in,
                "$QUOTE_MIN_5PCT",
                [from_token["address"], TOKENS["WETH"]["address"]],
                "$WALLET",
                "$DEADLINE_20M",
            ]
        else:
            self.function_name = "swapExactTokensForTokens"
            self.function_args = [
                amount_in,
                "$QUOTE_MIN_5PCT",
                [from_token["address"], to_token["address"]],
                "$WALLET",
                "$DEADLINE_20M",
            ]

        self.approve_enabled = True
        self.approvals = [
            {
                "token": from_token["address"],
                "spender": router,
                "amountWei": str(amount_in),
            },
        ]

    def apply_liquidity_choice(self, liquidity_params: dict[str, Any], router: str) -> None:
        token_a_symbol = liquidity_params["token_a_symbol"]
        token_b_symbol = liquidity_params["token_b_symbol"]
        amount_a = liquidity_params["amount_a"]
        amount_b = liquidity_params["amount_b"]

        if token_a_symbol == token_b_symbol:
            raise ValueError("Нельзя добавить ликвидность в пару из одинаковых токенов")

        if {token_a_symbol, token_b_symbol} == {"ETH", "WETH"}:
            raise ValueError("ETH/WETH не подходит для addLiquidityETH: это один и тот же актив")

        if isinstance(amount_a, PercentAmountRange):
            self.function_name = "autoLiquidityPercent"
            self.function_args = []
            self.action_mode = "send"
            self.contract_address = router
            self.contract_abi_path = (BASE_DIR / "contracts/nemesis_router.abi.json").resolve()
            self.tx_value_eth = Decimal("0")
            self.approve_enabled = False
            self.approvals = []
            self.auto_liquidity_params = liquidity_params
            return

        token_a = TOKENS[token_a_symbol]
        token_b = TOKENS[token_b_symbol]
        amount_a_wei = token_amount_to_wei(amount_a, int(token_a["decimals"]))
        amount_b_wei = token_amount_to_wei(amount_b, int(token_b["decimals"]))

        self.tx_value_eth = Decimal("0")
        self.approve_enabled = True
        self.approvals = []

        if token_a_symbol == "ETH" or token_b_symbol == "ETH":
            token_symbol = token_b_symbol if token_a_symbol == "ETH" else token_a_symbol
            token_amount_wei = amount_b_wei if token_a_symbol == "ETH" else amount_a_wei
            eth_amount = amount_a if token_a_symbol == "ETH" else "$QUOTE_ETH_FOR_TOKEN"
            if token_a_symbol == "ETH":
                token_amount_wei = "$QUOTE_TOKEN_FOR_ETH"

            self.function_name = "addLiquidityETH"
            self.function_args = [
                TOKENS[token_symbol]["address"],
                token_amount_wei,
                0,
                0,
                "$WALLET",
                "$DEADLINE_20M",
            ]
            self.tx_value_eth = eth_amount
            self.approvals = [
                {
                    "token": TOKENS[token_symbol]["address"],
                    "spender": router,
                    "amountWei": str(token_amount_wei),
                },
            ]
            return

        self.function_name = "addLiquidity"
        self.function_args = [
            token_a["address"],
            token_b["address"],
            amount_a_wei,
            amount_b_wei,
            0,
            0,
            "$WALLET",
            "$DEADLINE_20M",
        ]
        self.approvals = [
            {
                "token": token_a["address"],
                "spender": router,
                "amountWei": str(amount_a_wei),
            },
            {
                "token": token_b["address"],
                "spender": router,
                "amountWei": str(amount_b_wei),
            },
        ]


def select_menu_choice() -> str:
    print("\nВыбери действие:")
    print("1 - свапы")
    print("2 - добавление ликвидности")
    print("3 - готовые пары: swap + liquidity")
    print("4 - проверить мои позиции")
    print("5 - открыть лонг/шорт")
    print("6 - проверить long/short позиции")
    print("7 - закрыть long/short позицию")
    print("8 - вывести ликвидность")
    print("9 - рандомные круги: свап/лонг/ликвидность")
    return input("Введите 1, 2, 3, 4, 5, 6, 7, 8 или 9: ").strip()


def select_wallet_batch_count(default_count: int, wallet_count: int, current_index: Optional[int]) -> int:
    if wallet_count <= 1:
        return 1

    raw = input(f"Сколько кошельков пройти за запуск [{default_count}, 0 = все {wallet_count} с #1]: ").strip()
    count = int(raw) if raw else default_count
    if count == 0:
        return wallet_count
    if count < 1:
        raise ValueError(f"Количество кошельков должно быть больше 0 или 0 для всех {wallet_count}")
    return min(count, wallet_count)


def select_random_workflow_params(default_amount: Decimal) -> dict[str, Any]:
    raw_rounds = input("Сколько кругов выполнить [3]: ").strip() or "3"
    if not raw_rounds.isdigit():
        raise ValueError("Количество кругов должно быть целым числом")
    rounds = int(raw_rounds)
    if rounds <= 0:
        raise ValueError("Количество кругов должно быть больше 0")

    print("\nВыбери действия для рандомных кругов:")
    print("1 - свапы")
    print("2 - добавление ликвидности")
    print("5 - открыть лонг/шорт")
    raw_actions = input("Действия через запятую [1,2,5]: ").strip() or "1,2,5"
    actions = [item.strip() for item in raw_actions.split(",") if item.strip()]
    allowed_actions = {"1", "2", "5"}
    if not actions or any(action not in allowed_actions for action in actions):
        raise ValueError("Для рандомных кругов доступны только действия 1, 2 и 5")
    actions = list(dict.fromkeys(actions))
    if rounds < len(actions):
        print(f"Кругов меньше действий, ставлю {len(actions)}, чтобы каждый кошелёк прошёл все выбранные действия.")
        rounds = len(actions)

    action_params: dict[str, Any] = {}
    if "1" in actions:
        print("\nНастройка действия 1 - свапы")
        action_params["1"] = select_swap_params(default_amount)
    if "2" in actions:
        print("\nНастройка действия 2 - добавление ликвидности")
        action_params["2"] = select_liquidity_params(default_amount)
    if "5" in actions:
        print("\nНастройка действия 5 - открыть лонг/шорт")
        action_params["5"] = select_position_params(default_amount)

    return {
        "rounds": rounds,
        "actions": actions,
        "action_params": action_params,
    }


def select_swap_params(default_amount: Decimal) -> dict[str, Any]:
    token_symbols = list(TOKENS.keys())
    print("\nВыбери токен, который свапаем:")
    for idx, symbol in enumerate(token_symbols, start=1):
        print(f"{idx} - {symbol}")
    from_symbol = select_token_symbol(token_symbols, "Первый токен: ")

    print("\nВыбери токен, на который свапаем:")
    for idx, symbol in enumerate(token_symbols, start=1):
        print(f"{idx} - {symbol}")
    to_symbol = select_token_symbol(token_symbols, "Второй токен: ")

    amount = select_decimal_or_percent_range(f"Сумма {from_symbol}", default_amount)
    if not isinstance(amount, PercentAmountRange) and amount <= 0:
        raise ValueError("Сумма свапа должна быть больше 0")

    return {
        "from_symbol": from_symbol,
        "to_symbol": to_symbol,
        "amount": amount,
    }


def select_liquidity_params(default_amount: Decimal) -> dict[str, Any]:
    token_symbols = list(TOKENS.keys())
    print("\nВыбери первый токен пары:")
    for idx, symbol in enumerate(token_symbols, start=1):
        print(f"{idx} - {symbol}")
    token_a_symbol = select_token_symbol(token_symbols, "Первый токен: ")

    print("\nВыбери второй токен пары:")
    for idx, symbol in enumerate(token_symbols, start=1):
        print(f"{idx} - {symbol}")
    token_b_symbol = select_token_symbol(token_symbols, "Второй токен: ")

    amount_a = select_decimal_or_percent_range(f"Сумма {token_a_symbol}", default_amount)
    if not isinstance(amount_a, PercentAmountRange) and amount_a <= 0:
        raise ValueError(f"Сумма {token_a_symbol} должна быть больше 0")

    amount_b = amount_a
    if "ETH" in {token_a_symbol, token_b_symbol}:
        token_symbol = token_b_symbol if token_a_symbol == "ETH" else token_a_symbol
        print(f"Сумма {token_symbol}: будет рассчитана автоматически по router quote")
    else:
        print(f"Сумма {token_b_symbol}: {amount_b} (автоматически как {token_a_symbol})")

    return {
        "token_a_symbol": token_a_symbol,
        "token_b_symbol": token_b_symbol,
        "amount_a": amount_a,
        "amount_b": amount_b,
    }


def select_preset_liquidity_params(default_eth_budget: Decimal) -> dict[str, Any]:
    print("\nВыбери готовую пару:")
    for idx, pair in enumerate(PRESET_LIQUIDITY_PAIRS, start=1):
        print(f"{idx} - {pair['label']}")

    raw = input("Пара: ").strip()
    if not raw.isdigit() or not (1 <= int(raw) <= len(PRESET_LIQUIDITY_PAIRS)):
        raise ValueError("Выбери пару из списка")
    pair = PRESET_LIQUIDITY_PAIRS[int(raw) - 1]

    eth_budget = select_decimal_range("ETH-бюджет на пару", default_eth_budget)
    if eth_budget <= 0:
        raise ValueError("ETH-бюджет должен быть больше 0")

    return {
        "pair": pair,
        "eth_budget": eth_budget,
    }


def select_position_params(default_amount: Decimal) -> dict[str, Any]:
    print("\nВыбери пару для позиции:")
    for idx, pair in enumerate(POSITION_PAIRS, start=1):
        print(f"{idx} - {pair['label']}")

    raw_pair = input("Пара [1]: ").strip() or "1"
    if not raw_pair.isdigit() or not (1 <= int(raw_pair) <= len(POSITION_PAIRS)):
        raise ValueError("Выбери пару из списка")
    pair = POSITION_PAIRS[int(raw_pair) - 1]

    print("\nВыбери направление:")
    print("1 - Long")
    print("2 - Short")
    raw_direction = input("Направление [1]: ").strip() or "1"
    if raw_direction not in {"1", "2"}:
        raise ValueError("Выбери 1 для Long или 2 для Short")
    is_long = raw_direction == "1"

    print("\nВыбери режим:")
    print("1 - same token")
    print("2 - pair tokens")
    raw_mode = input("Режим [1]: ").strip() or "1"
    if raw_mode not in {"1", "2"}:
        raise ValueError("Выбери 1 для same token или 2 для pair tokens")
    mode = "same" if raw_mode == "1" else "pair"

    collateral_symbol, borrow_symbol = resolve_position_tokens(pair, is_long, mode)
    collateral_label = "ETH" if collateral_symbol == "WETH" else collateral_symbol

    collateral_amount = select_decimal_range(f"Сумма {collateral_label} collateral", default_amount)
    if collateral_amount <= 0:
        raise ValueError(f"Сумма {collateral_label} collateral должна быть больше 0")

    leverage = select_decimal_range_step("Плечо x", Decimal("2"), Decimal("0.1"))
    if leverage < Decimal("1.1"):
        raise ValueError("Плечо должно быть больше 1x")
    leverage_x10 = int(leverage * Decimal("10"))
    if Decimal(leverage_x10) / Decimal("10") != leverage:
        raise ValueError("Плечо поддерживается с шагом 0.1, пример: 2 или 2.5")

    direction_label = "Long" if is_long else "Short"
    print(f"{direction_label}: {pair['label']}, mode={mode}, collateral={collateral_amount} {collateral_label}, leverage={leverage}x")
    print(f"Collateral token: {collateral_symbol}; borrow token: {borrow_symbol}")
    if collateral_symbol == "WETH":
        print("ETH будет использован как WETH")
    return {
        "pair": pair,
        "is_long": is_long,
        "mode": mode,
        "collateral_symbol": collateral_symbol,
        "borrow_symbol": borrow_symbol,
        "collateral_amount": collateral_amount,
        "leverage_x10": leverage_x10,
    }


def select_close_position_params() -> dict[str, Any]:
    raw_position_id = input("Position ID: ").strip()
    if not raw_position_id.isdigit():
        raise ValueError("Position ID должен быть целым числом")
    position_id = int(raw_position_id)

    raw_min_out = input("AmountOutMin [0]: ").strip()
    amount_out_min = int(raw_min_out) if raw_min_out else 0
    if amount_out_min < 0:
        raise ValueError("AmountOutMin не может быть меньше 0")

    return {
        "position_id": position_id,
        "amount_out_min": amount_out_min,
    }


def select_remove_liquidity_params(default_percent: Decimal = Decimal("100")) -> dict[str, Any]:
    print("\nВыбери LP-пару для вывода ликвидности:")
    for idx, pair in enumerate(LP_POSITION_PAIRS, start=1):
        print(f"{idx} - {pair['label']}")

    raw_pair = input("Пара: ").strip()
    if not raw_pair.isdigit() or not (1 <= int(raw_pair) <= len(LP_POSITION_PAIRS)):
        raise ValueError("Выбери LP-пару из списка")
    pair = LP_POSITION_PAIRS[int(raw_pair) - 1]

    percent = select_decimal_range("Процент LP для вывода", default_percent)
    if percent <= 0 or percent > 100:
        raise ValueError("Процент LP должен быть больше 0 и не больше 100")

    return {
        "pair": pair,
        "percent": percent,
    }


def resolve_position_tokens(pair: dict[str, str], is_long: bool, mode: str) -> tuple[str, str]:
    base = pair["base"]
    quote = pair["quote"]
    if mode == "same":
        token = base if is_long else quote
        return token, token
    if is_long:
        return base, quote
    return quote, base


def select_token_symbol(token_symbols: list[str], prompt: str) -> str:
    raw = input(prompt).strip().upper()
    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(token_symbols):
            return token_symbols[idx - 1]
    if raw in token_symbols:
        return raw
    raise ValueError("Выбери токен из списка: " + ", ".join(token_symbols))


class NemesisOnchainClient:
    def __init__(self, config: Config) -> None:
        self.config = config
        request_kwargs: dict[str, Any] = {"timeout": 60}
        if config.proxy_url:
            request_kwargs["proxies"] = {
                "http": config.proxy_url,
                "https": config.proxy_url,
            }
        self.w3 = Web3(Web3.HTTPProvider(config.rpc_url, request_kwargs=request_kwargs))

    def run(self) -> None:
        self._validate()
        self._print_context()
        self._check_connection()
        if self.config.function_name == "checkPositions":
            self._check_positions()
            return

        if self.config.check_trade_positions:
            self._check_trade_positions()
            return

        if self.config.auto_swap_params:
            self._run_auto_swap_percent()
            return

        if self.config.auto_liquidity_params:
            self._run_auto_liquidity_percent()
            return

        if self.config.auto_liquidity_pair:
            self._run_preset_swap_and_liquidity()
            return

        if self.config.auto_position_params:
            self._run_open_position()
            return

        if self.config.close_position_params:
            self._run_close_position()
            return

        if self.config.remove_liquidity_params:
            self._run_remove_liquidity()
            return

        self._run_configured_transaction()

    def _run_configured_transaction(self) -> None:
        self.config.function_args = self._normalize_function_args()
        self._auto_top_up_liquidity_tokens()
        self._check_native_value_balance()

        if self.config.approve_enabled:
            self._run_approvals()

        if self.config.action_mode == "call":
            result = self._run_call()
            print("Call result:")
            print(self._json_dump(result))
            return

        tx_hash = self._run_send()
        if tx_hash is None:
            return
        print(f"Sent tx: {tx_hash}")
        if self.config.wait_for_receipt:
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
            self._print_receipt(receipt)

    def _run_auto_swap_percent(self) -> None:
        params = dict(self.config.auto_swap_params or {})
        amount_range = params["amount"]
        if not isinstance(amount_range, PercentAmountRange):
            raise ValueError("Для autoSwapPercent нужен процентный диапазон")

        from_symbol = params["from_symbol"]
        percent = amount_range.select_percent()
        balance = self._token_balance_decimal(from_symbol)
        amount = balance * percent / Decimal("100")
        if amount <= 0:
            raise InsufficientBalanceError(f"Баланс {from_symbol} равен 0, процент={percent}%")

        print(f"\n[percent amount] {from_symbol}: balance={balance}, percent={percent}%, amount={amount}")
        params["amount"] = amount
        self.config.auto_swap_params = None
        self.config.apply_swap_choice(params, self.config.contract_address or "0xa1f78bed1a79b9aec972e373e0e7f63d8cace4a8")
        self._run_configured_transaction()

    def _run_auto_liquidity_percent(self) -> None:
        params = dict(self.config.auto_liquidity_params or {})
        amount_range = params["amount_a"]
        if not isinstance(amount_range, PercentAmountRange):
            raise ValueError("Для autoLiquidityPercent нужен процентный диапазон")

        token_a_symbol = params["token_a_symbol"]
        percent = amount_range.select_percent()
        balance = self._token_balance_decimal(token_a_symbol)
        amount_a = balance * percent / Decimal("100")
        if amount_a <= 0:
            raise InsufficientBalanceError(f"Баланс {token_a_symbol} равен 0, процент={percent}%")

        print(f"\n[percent amount] {token_a_symbol}: balance={balance}, percent={percent}%, amount={amount_a}")
        params["amount_a"] = amount_a
        params["amount_b"] = amount_a
        self.config.auto_liquidity_params = None
        self.config.apply_liquidity_choice(params, self.config.contract_address or "0xa1f78bed1a79b9aec972e373e0e7f63d8cace4a8")
        self._run_configured_transaction()

    def _token_balance_decimal(self, symbol: str) -> Decimal:
        token = TOKENS[symbol]
        decimals = int(token["decimals"])
        wallet = self._checksum(self.config.wallet_address)
        if symbol == "ETH":
            return wei_to_token_amount(self.w3.eth.get_balance(wallet), decimals)
        return wei_to_token_amount(self._erc20_balance(token["address"]), decimals)

    def _check_positions(self) -> None:
        factory = self._contract(
            "0xbf301098692a47cf6861877e9acef55c9653ae50",
            self._load_abi(BASE_DIR / "contracts/nemesis_factory.abi.json"),
        )
        pairs = [
            ("WETH/DAI", "WETH", "DAI"),
            *[(pair["label"], pair["token_a"], pair["token_b"]) for pair in PRESET_LIQUIDITY_PAIRS],
        ]
        found = False
        print("\nМои LP-позиции onchain:")
        for label, token_a_symbol, token_b_symbol in pairs:
            token_a = TOKENS[token_a_symbol]
            token_b = TOKENS[token_b_symbol]
            pool_address = factory.functions.getPool(
                self._checksum(token_a["address"]),
                self._checksum(token_b["address"]),
            ).call()
            if int(pool_address, 16) == 0:
                continue
            pool = self._contract(
                pool_address,
                self._load_abi(BASE_DIR / "contracts/nemesis_pool.abi.json"),
            )
            balance = pool.functions.balanceOf(self._checksum(self.config.wallet_address)).call()
            if balance <= 0:
                continue
            decimals = pool.functions.decimals().call()
            total_supply = pool.functions.totalSupply().call()
            share = (Decimal(balance) / Decimal(total_supply) * Decimal(100)) if total_supply else Decimal(0)
            print(self._json_dump({
                "pair": label,
                "pool": self._checksum(pool_address),
                "lpBalanceRaw": balance,
                "lpBalance": str(wei_to_token_amount(balance, decimals)),
                "sharePercent": str(share),
            }))
            found = True
        if not found:
            print("Позиции не найдены по известным пулам.")

    def _check_trade_positions(self) -> None:
        positions = self._load_trade_positions()
        print("\nМои long/short позиции onchain:")
        if not positions:
            print("Long/short позиции не найдены по известным managers.")
            return
        for position in positions:
            print(self._json_dump(position))

    def _load_trade_positions(self) -> list[dict[str, Any]]:
        positions: list[dict[str, Any]] = []
        wallet = self._checksum(self.config.wallet_address)
        for pair, pool_address, manager_address, manager in self._position_managers():
            position_ids = manager.functions.getUserPositions(wallet).call()
            for position_id in position_ids:
                data = manager.functions.getPosition(position_id).call()
                health_factor = manager.functions.getHealthFactor(position_id).call()
                debt_with_funding = manager.functions.previewPositionDebtWithFunding(position_id).call()
                collateral_symbol = self._symbol_for_address(data[2])
                positions.append({
                    "pair": pair["label"],
                    "pool": self._checksum(pool_address),
                    "manager": self._checksum(manager_address),
                    "positionId": position_id,
                    "side": "Long" if data[0] else "Short",
                    "user": self._checksum(data[1]),
                    "collateralToken": collateral_symbol,
                    "collateralAddress": self._checksum(data[2]),
                    "collateralAmountRaw": data[3],
                    "collateralAmount": str(wei_to_token_amount(data[3], int(TOKENS[collateral_symbol]["decimals"]))) if collateral_symbol in TOKENS else str(data[3]),
                    "debtAmountRaw": data[4],
                    "currentDebtRaw": data[5],
                    "currentDebtWithFundingRaw": debt_with_funding,
                    "healthFactorRaw": health_factor,
                    "healthFactorFromGetPositionRaw": data[6],
                })
        return positions

    def _position_managers(self) -> list[tuple[dict[str, str], str, str, Any]]:
        factory = self._contract(
            "0xbf301098692a47cf6861877e9acef55c9653ae50",
            self._load_abi(BASE_DIR / "contracts/nemesis_factory.abi.json"),
        )
        vault_abi = self._load_abi(BASE_DIR / "contracts/nemesis_vault.abi.json")
        managers = []
        for pair in POSITION_PAIRS:
            base = self._checksum(TOKENS[pair["base"]]["address"])
            quote = self._checksum(TOKENS[pair["quote"]]["address"])
            pool_address = factory.functions.getPool(base, quote).call()
            if int(pool_address, 16) == 0:
                continue
            manager_address = factory.functions.getManager(pool_address).call()
            if int(manager_address, 16) == 0:
                continue
            managers.append((pair, pool_address, manager_address, self._contract(manager_address, vault_abi)))
        return managers

    def _find_manager_for_position(self, position_id: int) -> tuple[dict[str, str], str, str, Any]:
        wallet = self._checksum(self.config.wallet_address)
        for pair, pool_address, manager_address, manager in self._position_managers():
            if position_id in manager.functions.getUserPositions(wallet).call():
                return pair, pool_address, manager_address, manager
        raise ValueError(f"Position ID {position_id} не найден среди твоих позиций")

    def _symbol_for_address(self, address: str) -> str:
        checksum = self._checksum(address).lower()
        for symbol, token in TOKENS.items():
            if token["address"] != "native" and self._checksum(token["address"]).lower() == checksum:
                return symbol
        return self._checksum(address)

    def _run_preset_swap_and_liquidity(self) -> None:
        pair = self.config.auto_liquidity_pair or {}
        token_a_symbol = pair["token_a"]
        token_b_symbol = pair["token_b"]
        token_a = TOKENS[token_a_symbol]
        token_b = TOKENS[token_b_symbol]
        half_budget = self.config.auto_liquidity_eth_budget / Decimal("2")

        print(f"\n[preset] {pair['label']}")
        print(f"ETH budget: {self.config.auto_liquidity_eth_budget}; per swap: {half_budget}")
        budget_wei = self.w3.to_wei(self.config.auto_liquidity_eth_budget, "ether")
        native_balance = self.w3.eth.get_balance(self._checksum(self.config.wallet_address))
        print(f"Native budget required (wei): {budget_wei}")
        if native_balance < budget_wei:
            raise InsufficientBalanceError(f"Недостаточно ETH для бюджета: balance={native_balance}, required={budget_wei}")

        balance_a_before = self._erc20_balance(token_a["address"])
        balance_b_before = self._erc20_balance(token_b["address"])

        amount_a = self._swap_eth_for_token(token_a_symbol, half_budget)
        self._action_pause()
        amount_b = self._swap_eth_for_token(token_b_symbol, half_budget)
        self._action_pause()

        if self.config.send_tx:
            amount_a = self._erc20_balance(token_a["address"]) - balance_a_before
            amount_b = self._erc20_balance(token_b["address"]) - balance_b_before

        if amount_a <= 0 or amount_b <= 0:
            raise ValueError(f"После swap нет токенов для addLiquidity: {token_a_symbol}={amount_a}, {token_b_symbol}={amount_b}")

        estimated_lp = self._estimate_lp_mint(token_a["address"], token_b["address"], amount_a, amount_b)
        print(f"Estimated LP mint raw: {estimated_lp}")
        if estimated_lp <= 0:
            raise ValueError("Слишком маленькие суммы для addLiquidity: будущий LP mint равен 0. Увеличь ETH-бюджет.")

        print(f"\n[addLiquidity] {token_a_symbol}/{token_b_symbol}")
        print(f"Amounts: {token_a_symbol}={amount_a}, {token_b_symbol}={amount_b}")
        self._run_approval(token_a["address"], self.config.contract_address, amount_a)
        self._action_pause()
        self._run_approval(token_b["address"], self.config.contract_address, amount_b)
        self._action_pause()

        contract = self._contract(
            self.config.contract_address,
            self._load_abi(self.config.contract_abi_path),
        )
        tx_hash = self._send_contract_transaction(
            contract=contract,
            function_name="addLiquidity",
            args=[
                self._checksum(token_a["address"]),
                self._checksum(token_b["address"]),
                amount_a,
                amount_b,
                0,
                0,
                self._checksum(self.config.wallet_address),
                int(time.time()) + 1200,
            ],
            value_wei=0,
        )
        if tx_hash is None:
            return
        print(f"Add liquidity tx: {tx_hash}")
        if self.config.wait_for_receipt:
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
            self._print_receipt(receipt)

    def _run_open_position(self) -> None:
        params = self.config.auto_position_params or {}
        pair = params["pair"]
        is_long = bool(params["is_long"])
        mode = params["mode"]
        collateral_symbol = params["collateral_symbol"]
        borrow_symbol = params["borrow_symbol"]
        collateral_amount = params["collateral_amount"]
        leverage_x10 = int(params["leverage_x10"])
        collateral_amount_wei = token_amount_to_wei(collateral_amount, int(TOKENS[collateral_symbol]["decimals"]))
        borrow_amount_wei, amount_out_min = self._position_borrow_and_min_out(
            collateral_symbol=collateral_symbol,
            borrow_symbol=borrow_symbol,
            collateral_amount_wei=collateral_amount_wei,
            leverage_x10=leverage_x10,
            mode=mode,
        )
        if borrow_amount_wei <= 0:
            raise ValueError("Borrow amount получился 0. Увеличь плечо.")

        factory = self._contract(
            "0xbf301098692a47cf6861877e9acef55c9653ae50",
            self._load_abi(BASE_DIR / "contracts/nemesis_factory.abi.json"),
        )
        base = self._checksum(TOKENS[pair["base"]]["address"])
        quote = self._checksum(TOKENS[pair["quote"]]["address"])
        pool_address = factory.functions.getPool(base, quote).call()
        if int(pool_address, 16) == 0:
            raise ValueError(f"Pool не найден для {pair['label']}")
        manager_address = factory.functions.getManager(pool_address).call()
        if int(manager_address, 16) == 0:
            raise ValueError(f"Manager не найден для pool {pool_address}")
        pool = self._contract(
            pool_address,
            self._load_abi(BASE_DIR / "contracts/nemesis_pool.abi.json"),
        )
        token0 = self._checksum(pool.functions.token0().call())
        contract_is_long = is_long if token0.lower() == base.lower() else not is_long

        direction_label = "Long" if is_long else "Short"
        print(f"\n[position] {direction_label} {pair['label']} mode={mode}")
        print(f"Pool: {self._checksum(pool_address)}")
        print(f"Manager: {self._checksum(manager_address)}")
        if contract_is_long != is_long:
            print(f"Contract direction adjusted by pool token0: isLong={contract_is_long}")
        print(f"Collateral: {collateral_amount_wei} wei {collateral_symbol}")
        print(f"Borrow: {borrow_amount_wei} wei {borrow_symbol}")
        print(f"AmountOutMin: {amount_out_min}")
        print(f"LeverageX10: {leverage_x10}")

        self._prepare_position_collateral(collateral_symbol, collateral_amount_wei)
        self._action_pause()
        collateral_token = self._checksum(TOKENS[collateral_symbol]["address"])
        self._run_approval(collateral_token, manager_address, collateral_amount_wei)
        self._action_pause()

        manager = self._contract(
            manager_address,
            self._load_abi(BASE_DIR / "contracts/nemesis_vault.abi.json"),
        )
        tx_hash = self._send_contract_transaction(
            contract=manager,
            function_name="openPosition",
            args=[
                contract_is_long,
                collateral_token,
                collateral_amount_wei,
                borrow_amount_wei,
                leverage_x10,
                amount_out_min,
                int(time.time()) + 1200,
            ],
            value_wei=0,
        )
        if tx_hash is None:
            return
        print(f"Open {direction_label.lower()} tx: {tx_hash}")
        if self.config.wait_for_receipt:
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
            self._print_receipt(receipt)

    def _position_borrow_and_min_out(
        self,
        collateral_symbol: str,
        borrow_symbol: str,
        collateral_amount_wei: int,
        leverage_x10: int,
        mode: str,
    ) -> tuple[int, int]:
        leverage_extra_x10 = leverage_x10 - 10
        if leverage_extra_x10 <= 0:
            return 0, 0
        if mode == "same" or collateral_symbol == borrow_symbol:
            return collateral_amount_wei * leverage_extra_x10 // 10, 0

        router = self._contract(
            "0xa1f78bed1a79b9aec972e373e0e7f63d8cace4a8",
            self._load_abi(BASE_DIR / "contracts/nemesis_router.abi.json"),
        )
        collateral = self._checksum(TOKENS[collateral_symbol]["address"])
        borrow = self._checksum(TOKENS[borrow_symbol]["address"])
        borrow_quote = router.functions.getAmountsOut(collateral_amount_wei, [collateral, borrow]).call()[-1]
        borrow_amount_wei = int(borrow_quote * leverage_extra_x10 // 10)
        min_out_quote = router.functions.getAmountsOut(borrow_amount_wei, [borrow, collateral]).call()[-1]
        amount_out_min = int(min_out_quote * 95 // 100)
        print(f"Pair-token borrow quote: {borrow_quote} {borrow_symbol}; amountOutMin 5% slippage: {amount_out_min}")
        return borrow_amount_wei, amount_out_min

    def _prepare_position_collateral(self, collateral_symbol: str, required_amount_wei: int) -> None:
        if collateral_symbol == "WETH":
            self._wrap_eth_for_weth(required_amount_wei)
            return
        self._check_erc20_balance(self._checksum(TOKENS[collateral_symbol]["address"]), required_amount_wei)

    def _run_close_position(self) -> None:
        params = self.config.close_position_params or {}
        position_id = int(params["position_id"])
        amount_out_min = int(params["amount_out_min"])
        pair, pool_address, manager_address, manager = self._find_manager_for_position(position_id)
        print(f"\n[closePosition] id={position_id}, pair={pair['label']}")
        print(f"Pool: {self._checksum(pool_address)}")
        print(f"Manager: {self._checksum(manager_address)}")
        print(f"AmountOutMin: {amount_out_min}")

        tx_hash = self._send_contract_transaction(
            contract=manager,
            function_name="closePosition",
            args=[
                position_id,
                amount_out_min,
                int(time.time()) + 1200,
            ],
            value_wei=0,
        )
        if tx_hash is None:
            return
        print(f"Close position tx: {tx_hash}")
        if self.config.wait_for_receipt:
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
            self._print_receipt(receipt)

    def _run_remove_liquidity(self) -> None:
        params = self.config.remove_liquidity_params or {}
        pair = params["pair"]
        percent = params["percent"]
        token_a_symbol = pair["token_a"]
        token_b_symbol = pair["token_b"]
        token_a = self._checksum(TOKENS[token_a_symbol]["address"])
        token_b = self._checksum(TOKENS[token_b_symbol]["address"])
        pool_address = self._get_pool_address(token_a, token_b)
        pool = self._contract(
            pool_address,
            self._load_abi(BASE_DIR / "contracts/nemesis_pool.abi.json"),
        )
        wallet = self._checksum(self.config.wallet_address)
        lp_balance = pool.functions.balanceOf(wallet).call()
        liquidity = int(Decimal(lp_balance) * percent / Decimal("100"))
        if liquidity <= 0:
            raise InsufficientBalanceError(f"Недостаточно LP-баланса: balance={lp_balance}, percent={percent}, calculated={liquidity}")

        print(f"\n[removeLiquidity] {pair['label']}")
        print(f"Pool: {self._checksum(pool_address)}")
        print(f"LP balance: {lp_balance}")
        print(f"Remove percent: {percent}")
        print(f"Liquidity to remove: {liquidity}")

        router_address = "0xa1f78bed1a79b9aec972e373e0e7f63d8cace4a8"
        self._run_approval(pool_address, router_address, liquidity)
        self._action_pause()

        router = self._contract(
            router_address,
            self._load_abi(BASE_DIR / "contracts/nemesis_router.abi.json"),
        )
        deadline = int(time.time()) + 1200
        if pair.get("use_eth"):
            token_symbol = token_b_symbol if token_a_symbol == "WETH" else token_a_symbol
            function_name = "removeLiquidityETH"
            args = [
                self._checksum(TOKENS[token_symbol]["address"]),
                liquidity,
                0,
                0,
                wallet,
                deadline,
            ]
        else:
            function_name = "removeLiquidity"
            args = [
                token_a,
                token_b,
                liquidity,
                0,
                0,
                wallet,
                deadline,
            ]

        tx_hash = self._send_contract_transaction(
            contract=router,
            function_name=function_name,
            args=args,
            value_wei=0,
        )
        if tx_hash is None:
            return
        print(f"Remove liquidity tx: {tx_hash}")
        if self.config.wait_for_receipt:
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
            self._print_receipt(receipt)

    def _get_pool_address(self, token_a: str, token_b: str) -> str:
        factory = self._contract(
            "0xbf301098692a47cf6861877e9acef55c9653ae50",
            self._load_abi(BASE_DIR / "contracts/nemesis_factory.abi.json"),
        )
        pool_address = factory.functions.getPool(self._checksum(token_a), self._checksum(token_b)).call()
        if int(pool_address, 16) == 0:
            raise ValueError(f"Pool не найден для {token_a}/{token_b}")
        return self._checksum(pool_address)

    def _wrap_eth_for_weth(self, required_weth_wei: int) -> None:
        weth = self._contract(TOKENS["WETH"]["address"], WETH_ABI)
        wallet = self._checksum(self.config.wallet_address)
        current_weth = weth.functions.balanceOf(wallet).call()
        missing_weth = max(required_weth_wei - current_weth, 0)
        print(f"\n[wrap] WETH balance={current_weth}, required={required_weth_wei}, missing={missing_weth}")
        if missing_weth <= 0:
            print("Wrap не нужен.")
            return

        native_balance = self.w3.eth.get_balance(wallet)
        if native_balance < missing_weth:
            raise InsufficientBalanceError(f"Недостаточно ETH для wrap: balance={native_balance}, required={missing_weth}")

        tx_hash = self._send_contract_transaction(
            contract=weth,
            function_name="deposit",
            args=[],
            value_wei=missing_weth,
        )
        if tx_hash is None:
            return
        print(f"Wrap tx: {tx_hash}")
        if self.config.wait_for_receipt:
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
            self._print_receipt(receipt)

    def _action_pause(self) -> None:
        delay = random.uniform(2, 5)
        print(f"\n[pause] {delay:.1f} сек перед следующим действием...")
        time.sleep(delay)

    def _validate(self) -> None:
        if not self.config.rpc_url:
            raise ValueError("RPC_URL не задан в .env")

        if not self.config.wallet_address:
            raise ValueError("Не задан адрес кошелька в .addresses.env или через приватный ключ")

        if self.config.action_mode not in {"call", "send"}:
            raise ValueError("ACTION_MODE должен быть call или send")

        if not self.config.contract_address:
            raise ValueError("CONTRACT_ADDRESS не задан")

        if not self.config.function_name:
            raise ValueError("FUNCTION_NAME не задан")

        if not self.config.contract_abi_path.exists():
            raise ValueError(f"ABI не найден: {self.config.contract_abi_path}")

        if self.config.action_mode == "send" and self.config.send_tx and not self.config.wallet_private_key:
            raise ValueError("Для отправки транзакции нужен приватный ключ в .wallet.env")

        if self.config.approve_enabled and self.config.approvals:
            for idx, approval in enumerate(self.config.approvals):
                missing = [
                    name
                    for name in ("token", "spender", "amountWei")
                    if not approval.get(name)
                ]
                if missing:
                    raise ValueError(f"APPROVALS_JSON[{idx}] не хватает: " + ", ".join(missing))
            if not self.config.approve_token_abi_path.exists():
                raise ValueError(f"ABI approve-токена не найден: {self.config.approve_token_abi_path}")
        elif self.config.approve_enabled:
            missing = [
                name
                for name, value in {
                    "APPROVE_TOKEN_ADDRESS": self.config.approve_token_address,
                    "APPROVE_SPENDER_ADDRESS": self.config.approve_spender_address,
                    "APPROVE_AMOUNT_WEI": self.config.approve_amount_wei,
                }.items()
                if not value
            ]
            if missing:
                raise ValueError("Для approve не хватает: " + ", ".join(missing))
            if not self.config.approve_token_abi_path.exists():
                raise ValueError(f"ABI approve-токена не найден: {self.config.approve_token_abi_path}")

    def _run_approvals(self) -> None:
        if self.config.approvals:
            for approval in self.config.approvals:
                self._run_approval(
                    token_address=approval["token"],
                    spender_address=approval["spender"],
                    amount=int(approval["amountWei"]),
                )
            return

        self._run_approval(
            token_address=self.config.approve_token_address,
            spender_address=self.config.approve_spender_address,
            amount=int(self.config.approve_amount_wei or 0),
        )

    def _print_context(self) -> None:
        print(f"Wallet address: {self.config.wallet_address}")
        if self.config.wallet_rotate and self.config.wallet_current_index is not None:
            print(f"Wallet rotation: кошелёк #{self.config.wallet_current_index + 1}/{self.config.wallet_count}")
        print(f"RPC URL: {self.config.rpc_url}")
        print(f"Proxy: {mask_proxy_url(self.config.proxy_url)}")
        print(f"Chain ID: {self.config.chain_id}")
        print(f"Action mode: {self.config.action_mode}")
        print(f"Contract: {self.config.contract_address}")
        print(f"Function: {self.config.function_name}")
        print(f"Args: {self._json_dump(self.config.function_args)}")

    def _check_connection(self) -> None:
        if not self.w3.is_connected():
            raise RuntimeError("Не удалось подключиться к RPC")

        remote_chain_id = int(self.w3.eth.chain_id)
        if remote_chain_id != self.config.chain_id:
            raise RuntimeError(
                f"Chain mismatch: RPC вернул {remote_chain_id}, в конфиге указано {self.config.chain_id}"
            )

        balance = self.w3.eth.get_balance(self._checksum(self.config.wallet_address))
        print(f"Native balance (wei): {balance}")

    def _check_native_value_balance(self) -> None:
        if self.config.action_mode != "send":
            return
        value_wei = self.w3.to_wei(self.config.tx_value_eth, "ether")
        if value_wei <= 0:
            return
        wallet = self._checksum(self.config.wallet_address)
        balance = self.w3.eth.get_balance(wallet)
        print(f"Native value required (wei): {value_wei}")
        if balance < value_wei:
            raise InsufficientBalanceError(f"Недостаточно ETH: balance={balance}, required={value_wei}")

    def _check_erc20_balance(self, token_address: str, amount: int) -> None:
        balance = self._erc20_balance(token_address)
        print(f"Token balance: token={token_address}, balance={balance}, required={amount}")
        if balance < amount:
            raise InsufficientBalanceError(f"Недостаточно ERC-20 баланса: token={token_address}, balance={balance}, required={amount}")

    def _erc20_balance(self, token_address: str) -> int:
        token = self._contract(
            token_address,
            self._load_abi(self.config.approve_token_abi_path),
        )
        owner = self._checksum(self.config.wallet_address)
        return token.functions.balanceOf(owner).call()

    def _swap_eth_for_token(self, token_symbol: str, eth_amount: Decimal) -> int:
        contract = self._contract(
            self.config.contract_address,
            self._load_abi(self.config.contract_abi_path),
        )
        value_wei = self.w3.to_wei(eth_amount, "ether")
        path, quoted_amounts = self._best_eth_swap_path(contract, token_symbol, value_wei)
        amount_out_min = int(quoted_amounts[-1] * 95 // 100)
        print(f"\n[swap] ETH -> {token_symbol}: value={value_wei} wei, path={self._path_symbols(path)}, quote={quoted_amounts[-1]}, minOut={amount_out_min}")
        tx_hash = self._send_contract_transaction(
            contract=contract,
            function_name="swapExactETHForTokens",
            args=[
                amount_out_min,
                path,
                self._checksum(self.config.wallet_address),
                int(time.time()) + 1200,
            ],
            value_wei=value_wei,
        )
        if tx_hash is None:
            return amount_out_min
        print(f"Swap tx: {tx_hash}")
        if self.config.wait_for_receipt:
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
            self._print_receipt(receipt)
        return amount_out_min

    def _auto_top_up_liquidity_tokens(self) -> None:
        if self.config.function_name not in {"addLiquidity", "addLiquidityETH"}:
            return
        if self.config.action_mode != "send" or not self.config.approvals:
            return
        if not self.config.send_tx:
            print("\n[auto top-up] SEND_TX=false, докупка токенов пропущена.")
            return

        reserved_native_wei = self.w3.to_wei(self.config.tx_value_eth, "ether")

        for approval in self.config.approvals:
            token_address = self._checksum(approval["token"])
            required = int(approval["amountWei"])
            if required <= 0:
                continue

            balance = self._erc20_balance(token_address)
            if balance >= required:
                continue

            missing = required - balance
            token_symbol = self._symbol_for_address(token_address)
            print(
                f"\n[auto top-up] не хватает {token_symbol}: "
                f"balance={balance}, required={required}, missing={missing}"
            )

            if token_symbol == "WETH":
                self._wrap_eth_for_weth(required)
            elif token_symbol in TOKENS:
                self._swap_eth_for_exact_token_topup(token_symbol, missing, reserved_native_wei)
            else:
                raise ValueError(f"Неизвестный токен для auto top-up: {token_address}")

            self._action_pause()

    def _swap_eth_for_exact_token_topup(
        self,
        token_symbol: str,
        missing_token_wei: int,
        reserved_native_wei: int = 0,
    ) -> int:
        contract = self._contract(
            self.config.contract_address,
            self._load_abi(self.config.contract_abi_path),
        )
        path, quoted_amounts = self._best_eth_input_path(contract, token_symbol, missing_token_wei)
        value_wei = int(quoted_amounts[0] * 105 // 100)
        wallet = self._checksum(self.config.wallet_address)
        native_balance = self.w3.eth.get_balance(wallet)
        total_required = value_wei + reserved_native_wei
        if native_balance < total_required:
            raise InsufficientBalanceError(
                f"Недостаточно ETH для auto top-up {token_symbol}: "
                f"balance={native_balance}, required={total_required} "
                f"(top-up={value_wei}, reserved_for_liquidity={reserved_native_wei})"
            )

        print(
            f"[auto top-up] ETH -> {token_symbol}: value={value_wei} wei, "
            f"path={self._path_symbols(path)}, needOut={missing_token_wei}, quoteIn={quoted_amounts[0]}"
        )
        tx_hash = self._send_contract_transaction(
            contract=contract,
            function_name="swapExactETHForTokens",
            args=[
                missing_token_wei,
                path,
                wallet,
                int(time.time()) + 1200,
            ],
            value_wei=value_wei,
        )
        if tx_hash is None:
            return missing_token_wei
        print(f"Auto top-up tx: {tx_hash}")
        if self.config.wait_for_receipt:
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
            self._print_receipt(receipt)
        return missing_token_wei

    def _estimate_lp_mint(self, token_a: str, token_b: str, amount_a: int, amount_b: int) -> int:
        factory = self._contract(
            "0xbf301098692a47cf6861877e9acef55c9653ae50",
            self._load_abi(BASE_DIR / "contracts/nemesis_factory.abi.json"),
        )
        pool_address = factory.functions.getPool(self._checksum(token_a), self._checksum(token_b)).call()
        pool = self._contract(
            pool_address,
            self._load_abi(BASE_DIR / "contracts/nemesis_pool.abi.json"),
        )
        reserve0, reserve1, _ = pool.functions.getReserves().call()
        total_supply = pool.functions.totalSupply().call()
        token0 = pool.functions.token0().call()
        if self._checksum(token_a).lower() == token0.lower():
            reserve_a, reserve_b = reserve0, reserve1
        else:
            reserve_a, reserve_b = reserve1, reserve0
        if reserve_a <= 0 or reserve_b <= 0 or total_supply <= 0:
            return 0
        return min(amount_a * total_supply // reserve_a, amount_b * total_supply // reserve_b)

    def _best_eth_swap_path(self, contract: Any, token_symbol: str, value_wei: int) -> tuple[list[str], list[int]]:
        target = self._checksum(TOKENS[token_symbol]["address"])
        weth = self._checksum(TOKENS["WETH"]["address"])
        candidate_paths = [[weth, target]]

        for intermediate_symbol in SWAP_INTERMEDIATE_TOKENS:
            if intermediate_symbol == token_symbol:
                continue
            intermediate = self._checksum(TOKENS[intermediate_symbol]["address"])
            if intermediate in {weth, target}:
                continue
            candidate_paths.append([weth, intermediate, target])

        best_path: Optional[list[str]] = None
        best_amounts: Optional[list[int]] = None
        for path in candidate_paths:
            try:
                amounts = contract.functions.getAmountsOut(value_wei, path).call()
            except Exception:
                continue
            if best_amounts is None or amounts[-1] > best_amounts[-1]:
                best_path = path
                best_amounts = amounts

        if best_path is None or best_amounts is None:
            raise ValueError(f"Не найден маршрут swap ETH -> {token_symbol}")
        return best_path, best_amounts

    def _best_eth_input_path(self, contract: Any, token_symbol: str, amount_out_wei: int) -> tuple[list[str], list[int]]:
        target = self._checksum(TOKENS[token_symbol]["address"])
        weth = self._checksum(TOKENS["WETH"]["address"])
        candidate_paths = [[weth, target]]

        for intermediate_symbol in SWAP_INTERMEDIATE_TOKENS:
            if intermediate_symbol == token_symbol:
                continue
            intermediate = self._checksum(TOKENS[intermediate_symbol]["address"])
            if intermediate in {weth, target}:
                continue
            candidate_paths.append([weth, intermediate, target])

        best_path: Optional[list[str]] = None
        best_amounts: Optional[list[int]] = None
        for path in candidate_paths:
            try:
                amounts = contract.functions.getAmountsIn(amount_out_wei, path).call()
            except Exception:
                continue
            if best_amounts is None or amounts[0] < best_amounts[0]:
                best_path = path
                best_amounts = amounts

        if best_path is None or best_amounts is None:
            raise ValueError(f"Не найден маршрут auto top-up ETH -> {token_symbol}")
        return best_path, best_amounts

    def _path_symbols(self, path: list[str]) -> str:
        by_address = {
            self._checksum(token["address"]).lower(): symbol
            for symbol, token in TOKENS.items()
            if token["address"] != "native"
        }
        return " -> ".join(by_address.get(address.lower(), address) for address in path)

    def _run_approval(self, token_address: Optional[str], spender_address: Optional[str], amount: int) -> None:
        print(f"\n[approve] token={token_address}, spender={spender_address}, amount={amount}")
        if not self.config.send_tx:
            print("SEND_TX=false, approve пропущен.")
            return

        self._check_erc20_balance(self._checksum(token_address), amount)
        token = self._contract(
            token_address,
            self._load_abi(self.config.approve_token_abi_path),
        )
        owner = self._checksum(self.config.wallet_address)
        spender = self._checksum(spender_address)

        current_allowance = token.functions.allowance(owner, spender).call()
        print(f"Current allowance: {current_allowance}")
        if current_allowance >= amount:
            print("Approve не нужен.")
            return

        tx_hash = self._send_contract_transaction(
            contract=token,
            function_name="approve",
            args=[spender, amount],
            value_wei=0,
        )
        print(f"Approve tx: {tx_hash}")
        if self.config.wait_for_receipt:
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
            self._print_receipt(receipt)

    def _run_call(self) -> Any:
        contract = self._contract(
            self.config.contract_address,
            self._load_abi(self.config.contract_abi_path),
        )
        function = getattr(contract.functions, self.config.function_name)(*self._normalize_function_args())
        return function.call({"from": self._checksum(self.config.wallet_address)})

    def _run_send(self) -> Optional[str]:
        contract = self._contract(
            self.config.contract_address,
            self._load_abi(self.config.contract_abi_path),
        )
        return self._send_contract_transaction(
            contract=contract,
            function_name=self.config.function_name or "",
            args=self._normalize_function_args(),
            value_wei=self.w3.to_wei(self.config.tx_value_eth, "ether"),
        )

    def _send_contract_transaction(
        self,
        contract: Any,
        function_name: str,
        args: list[Any],
        value_wei: int,
    ) -> Optional[str]:
        wallet = self._checksum(self.config.wallet_address)
        function = getattr(contract.functions, function_name)(*args)
        nonce = self.w3.eth.get_transaction_count(wallet, "pending")

        tx: dict[str, Any] = {
            "from": wallet,
            "nonce": nonce,
            "chainId": self.config.chain_id,
            "value": value_wei,
        }

        if not self.config.send_tx:
            preview = {
                **tx,
                "to": contract.address,
                "data": function._encode_transaction_data(),
                "function": function_name,
                "args": args,
            }
            print("\n[DRY-RUN] SEND_TX=false, транзакция НЕ отправлена.")
            print("Чтобы отправлять реальные транзакции, поставь SEND_TX=true в .env.")
            print("Dry-run tx:")
            print(self._json_dump(preview))
            return None

        base_tx = tx.copy()
        for attempt in range(3):
            tx = base_tx.copy()
            tx["nonce"] = self.w3.eth.get_transaction_count(wallet, "pending")

            if self.config.gas_limit:
                tx["gas"] = self.config.gas_limit

            if self.config.max_fee_per_gas_gwei:
                tx["maxFeePerGas"] = self.w3.to_wei(self.config.max_fee_per_gas_gwei, "gwei")

            if self.config.max_priority_fee_per_gas_gwei:
                tx["maxPriorityFeePerGas"] = self.w3.to_wei(self.config.max_priority_fee_per_gas_gwei, "gwei")

            if attempt > 0:
                gas_price = int(self.w3.eth.gas_price)
                bump = Decimal("1.25") ** attempt
                tx["maxPriorityFeePerGas"] = max(int(Decimal(gas_price // 10 or 1) * bump), 1_000_000)
                tx["maxFeePerGas"] = max(int(Decimal(gas_price * 2) * bump), tx["maxPriorityFeePerGas"] * 2)
                print(f"Retry tx after underpriced error: attempt={attempt + 1}, nonce={tx['nonce']}")

            if "gas" not in tx:
                estimated = function.estimate_gas({"from": wallet, "value": value_wei})
                tx["gas"] = int(estimated * 1.15)

            built_tx = function.build_transaction(tx)
            signed = self.w3.eth.account.sign_transaction(built_tx, self.config.wallet_private_key)
            try:
                tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
                return self._hex(tx_hash)
            except ValueError as exc:
                retryable_error = (
                    "replacement transaction underpriced" in str(exc)
                    or "could not replace existing tx" in str(exc)
                )
                if retryable_error and attempt < 2:
                    wait_random_pause()
                    continue
                raise

        return None

    def _print_receipt(self, receipt: Any) -> None:
        status = int(receipt.get("status", 0))
        print("Receipt:")
        print(self._json_dump({
            "status": status,
            "ok": status == 1,
            "transactionHash": self._hex(receipt.get("transactionHash")),
            "blockNumber": receipt.get("blockNumber"),
            "gasUsed": receipt.get("gasUsed"),
            "effectiveGasPrice": receipt.get("effectiveGasPrice"),
            "from": receipt.get("from"),
            "to": receipt.get("to"),
        }))

    def _hex(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if hasattr(value, "hex"):
            raw = value.hex()
            return raw if raw.startswith("0x") else "0x" + raw
        raw = str(value)
        return raw if raw.startswith("0x") else "0x" + raw

    def _contract(self, address: Optional[str], abi: list[Any]) -> Any:
        if not address:
            raise ValueError("Адрес контракта пустой")
        return self.w3.eth.contract(address=self._checksum(address), abi=abi)

    def _load_abi(self, path: Path) -> list[Any]:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, list):
            raise ValueError(f"ABI должен быть JSON-массивом: {path}")
        return data

    def _checksum(self, address: Optional[str]) -> str:
        if not address:
            raise ValueError("Пустой адрес")
        return self.w3.to_checksum_address(address)

    def _normalize_args(self, value: Any) -> Any:
        if value == "$WALLET":
            return self._checksum(self.config.wallet_address)
        if value == "$DEADLINE_20M":
            return int(time.time()) + 1200
        if isinstance(value, str) and Web3.is_address(value):
            return self._checksum(value)
        if isinstance(value, list):
            return [self._normalize_args(item) for item in value]
        if isinstance(value, dict):
            return {key: self._normalize_args(item) for key, item in value.items()}
        return value

    def _normalize_function_args(self) -> list[Any]:
        args = self._normalize_args(self.config.function_args)
        if self.config.function_name == "addLiquidityETH" and self.config.tx_value_eth == "$QUOTE_ETH_FOR_TOKEN":
            router = self._contract(
                self.config.contract_address,
                self._load_abi(self.config.contract_abi_path),
            )
            path = [TOKENS["WETH"]["address"], args[0]]
            quoted_amounts = router.functions.getAmountsIn(args[1], path).call()
            eth_amount_wei = int(quoted_amounts[0] * 105 // 100)
            self.config.tx_value_eth = wei_to_token_amount(eth_amount_wei, int(TOKENS["ETH"]["decimals"]))
            print(f"ETH для ликвидности по quote +5%: {eth_amount_wei} wei ({self.config.tx_value_eth} ETH)")

        if self.config.function_name == "addLiquidityETH" and args and args[1] == "$QUOTE_TOKEN_FOR_ETH":
            router = self._contract(
                self.config.contract_address,
                self._load_abi(self.config.contract_abi_path),
            )
            eth_amount_wei = self.w3.to_wei(self.config.tx_value_eth, "ether")
            quoted_amounts = router.functions.getAmountsOut(eth_amount_wei, [TOKENS["WETH"]["address"], args[0]]).call()
            token_amount_wei = int(quoted_amounts[-1] * 105 // 100)
            args[1] = token_amount_wei
            if self.config.approvals:
                self.config.approvals[0]["amountWei"] = str(token_amount_wei)
            print(f"Token для ликвидности по quote +5%: {token_amount_wei} wei")

        quote_config = {
            "swapExactETHForTokens": (0, 1, lambda values: self.w3.to_wei(self.config.tx_value_eth, "ether")),
            "swapExactTokensForETH": (1, 2, lambda values: values[0]),
            "swapExactTokensForTokens": (1, 2, lambda values: values[0]),
        }
        quote_indexes = quote_config.get(self.config.function_name or "")
        if quote_indexes and args and args[quote_indexes[0]] == "$QUOTE_MIN_5PCT":
            min_out_idx, path_idx, amount_in_getter = quote_indexes
            path = args[path_idx]
            value_wei = amount_in_getter(args)
            router = self._contract(
                self.config.contract_address,
                self._load_abi(self.config.contract_abi_path),
            )
            quoted_amounts = router.functions.getAmountsOut(value_wei, path).call()
            args[min_out_idx] = int(quoted_amounts[-1] * 95 // 100)
            print(f"Quote out: {quoted_amounts[-1]} wei, min out 5% slippage: {args[min_out_idx]} wei")
        return args

    def _json_dump(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def apply_random_workflow_action(config: Config, action: str, action_params: dict[str, Any]) -> None:
    config.apply_menu_choice(
        action,
        swap_params=action_params.get("1") if action == "1" else None,
        liquidity_params=action_params.get("2") if action == "2" else None,
        position_params=action_params.get("5") if action == "5" else None,
    )


def run_random_workflow(config: Config, workflow_params: dict[str, Any], wallet_batch_count: int) -> int:
    if not config.wallet_rotate:
        raise ValueError("Рандомные круги требуют WALLET_ROTATE=true")
    if config.wallet_count <= 0 or config.wallet_current_index is None:
        raise ValueError("Нет кошельков для ротации")

    rounds = int(workflow_params["rounds"])
    actions = list(workflow_params["actions"])
    action_params = dict(workflow_params["action_params"])
    start_index = config.wallet_current_index
    wallet_indices = [(start_index + offset) % config.wallet_count for offset in range(wallet_batch_count)]
    wallet_action_history: dict[int, list[str]] = {idx: [] for idx in wallet_indices}
    errors: list[str] = []
    total_steps = rounds * len(wallet_indices)
    step = 0

    print(f"\n[random workflow] wallets={len(wallet_indices)}, rounds={rounds}, actions={','.join(actions)}")
    for round_index in range(rounds):
        print(f"\n=== Random workflow круг {round_index + 1}/{rounds} ===")
        for wallet_index in wallet_indices:
            if step > 0:
                wait_wallet_pause()
            step += 1

            save_wallet_index(config.wallet_index_file, wallet_index)
            step_config = Config.load()
            history = wallet_action_history[wallet_index]
            unused_actions = [action for action in actions if action not in history]
            if not unused_actions:
                history.clear()
                unused_actions = actions[:]
            action = random.choice(unused_actions)
            history.append(action)

            print(f"\n=== Random step {step}/{total_steps}: кошелёк #{wallet_index + 1}/{step_config.wallet_count}, действие {action} ===")
            try:
                apply_random_workflow_action(step_config, action, action_params)
                NemesisOnchainClient(step_config).run()
            except InsufficientBalanceError as exc:
                wallet_label = step_config.wallet_address or "unknown"
                print("\nИтог: не хватает баланса.")
                print(f"Детали: {exc}")
                errors.append(f"{wallet_label}: действие {action}: не хватает баланса: {exc}")
            except Exception as exc:
                wallet_label = step_config.wallet_address or "unknown"
                print(f"Ошибка: {exc}")
                errors.append(f"{wallet_label}: действие {action}: {exc}")

    next_index = (start_index + wallet_batch_count) % config.wallet_count
    save_wallet_index(config.wallet_index_file, next_index)
    print(f"Wallet rotation: следующий запуск возьмёт кошелёк #{next_index + 1}/{config.wallet_count}")

    if errors:
        print("\nИтоговые ошибки:")
        for error in errors:
            print(f"- {error}")
        return 1
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        config = Config.load()
        choice: Optional[str] = None
        swap_params = None
        liquidity_params = None
        preset_liquidity_params = None
        position_params = None
        close_position_params = None
        remove_liquidity_params = None
        random_workflow_params = None
        wallet_batch_count = 1
        if config.menu_enabled:
            choice = select_menu_choice()
            swap_params = select_swap_params(config.swap_default_amount) if choice == "1" else None
            liquidity_params = select_liquidity_params(config.swap_default_amount) if choice == "2" else None
            preset_liquidity_params = select_preset_liquidity_params(config.swap_default_amount) if choice == "3" else None
            position_params = select_position_params(config.swap_default_amount) if choice == "5" else None
            close_position_params = select_close_position_params() if choice == "7" else None
            remove_liquidity_params = select_remove_liquidity_params() if choice == "8" else None
            random_workflow_params = select_random_workflow_params(config.swap_default_amount) if choice == "9" else None
            if config.wallet_rotate:
                wallet_batch_count = select_wallet_batch_count(
                    config.wallet_batch_count,
                    config.wallet_count,
                    config.wallet_current_index,
                )
                if wallet_batch_count == config.wallet_count:
                    save_wallet_index(config.wallet_index_file, 0)
                    config = Config.load()

        if random_workflow_params:
            return run_random_workflow(config, random_workflow_params, wallet_batch_count)

        errors = []
        for wallet_run_index in range(wallet_batch_count):
            if wallet_run_index > 0:
                wait_wallet_pause()
            print(f"\n=== Batch step {wallet_run_index + 1}/{wallet_batch_count} ===")
            if wallet_run_index > 0:
                config = Config.load()
            if config.menu_enabled and choice is not None:
                config.apply_menu_choice(
                    choice,
                    swap_params,
                    liquidity_params,
                    preset_liquidity_params,
                    position_params,
                    close_position_params,
                    remove_liquidity_params,
                )
            try:
                NemesisOnchainClient(config).run()
            except InsufficientBalanceError as exc:
                wallet_label = config.wallet_address or "unknown"
                print("\nИтог: не хватает баланса.")
                print(f"Детали: {exc}")
                errors.append(f"{wallet_label}: не хватает баланса: {exc}")
            except Exception as exc:
                wallet_label = config.wallet_address or "unknown"
                print(f"Ошибка: {exc}")
                errors.append(f"{wallet_label}: {exc}")
            finally:
                if wallet_batch_count > 1:
                    config.commit_wallet_rotation()

        if wallet_batch_count == 1 and not errors:
            config.commit_wallet_rotation()

        if errors:
            print("\nИтоговые ошибки:")
            for error in errors:
                print(f"- {error}")
            return 1
        return 0
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")
        return 130
    except Exception as exc:
        print(f"Ошибка: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
