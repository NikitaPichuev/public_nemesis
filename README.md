# Nemesis Onchain Client

Python client for onchain interaction with Nemesis on Sepolia. The project does not use browser automation: all actions are sent through RPC and signed locally.

## Features

- Token swaps through the Nemesis router.
- Liquidity add/remove flows.
- Preset `swap + liquidity` flows for selected pairs.
- LP position checks through RPC.
- Long/short position open, check, and close flows.
- Multi-wallet rotation from `.wallet.env`.
- Optional proxy rotation from `proxies.txt`.
- Random workflow rounds across wallets.

## Important

- This client can send real transactions when `SEND_TX=true`.
- Private keys are loaded locally from `.wallet.env`.
- Do not publish `.env`, `.wallet.env`, `.addresses.env`, `proxies.txt`, or `wallet_index.txt`.
- The included `.gitignore` excludes those local files.
- Use at your own risk. Review amounts, routes, slippage, gas settings, and contract addresses before sending transactions.

## Install

Windows:

```bat
install.bat
```

Manual:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

## Required Files

Create these local files before running:

- `.env` copied from `.env.example`
- `.wallet.env` with one private key per line
- `.addresses.env` optionally with one wallet address per line
- `proxies.txt` optionally with one proxy per line

Private key file format:

```text
0xPRIVATE_KEY_1
0xPRIVATE_KEY_2
```

Address file format:

```text
0xWalletAddress1
0xWalletAddress2
```

Proxy file format:

```text
host:port
user:password@host:port
http://user:password@host:port
```

## Configuration

Main settings are in `.env`:

```env
RPC_URL=https://ethereum-sepolia-rpc.publicnode.com
PROXY_URL=
PROXIES_FILE=proxies.txt
CHAIN_ID=11155111
SEND_TX=true
WAIT_FOR_RECEIPT=true
MENU_ENABLED=true
SWAP_DEFAULT_AMOUNT=0.01
WALLET_ROTATE=true
WALLET_INDEX_FILE=wallet_index.txt
WALLET_BATCH_COUNT=1
```

If `WALLET_ROTATE=true`, the client reads private keys line by line from `.wallet.env`. If `PROXY_URL` is empty and `PROXIES_FILE` exists, proxies are matched to wallets by index.

To start wallet rotation from the first wallet, set `wallet_index.txt` to:

```text
0
```

## Run

Windows:

```bat
start.bat
```

Manual:

```powershell
.\.venv\Scripts\Activate.ps1
python main.py
```

## Menu

```text
1 - swaps
2 - add liquidity
3 - preset pairs: swap + liquidity
4 - check LP positions
5 - open long/short
6 - check long/short positions
7 - close long/short position
8 - remove liquidity
9 - random rounds: swap/long/liquidity
```

## Amounts

For supported flows, amounts can be entered as:

- exact amount: `0.001`
- comma decimal: `0,001`
- random range: min `0.001`, max `0.005`
- percentage of token balance: `10%`
- percentage range: min `5%`, max `20%`

Percent values are calculated per wallet during batch runs.

## Random Rounds

Menu item `9` runs selected actions across selected wallets in randomized order.

Example:

```text
Rounds: 3
Actions: 1,2,5
```

Each selected wallet will run all selected actions once across the 3 rounds, but in random order. If the number of rounds is lower than the number of selected actions, the client raises the round count so each wallet can complete every selected action.

## Supported Tokens

- ETH
- WETH
- DAI
- USDC
- BTC
- MINABO
- NEMESIS
- NEM
- ONE

## Position Pairs

Long/short menu currently supports:

- ETH/DAI
- ETH/USDC
- ETH/NEMESIS
- ETH/NEM

`ETH/MINABO` is not included in the public menu because it reverted during testing.

## Preset Liquidity Pairs

- BTC/MINABO
- BTC/NEMESIS
- BTC/NEM
- BTC/ONE

## Contracts

ABI files are stored in `contracts/`:

- `contracts/erc20.abi.json`
- `contracts/nemesis_factory.abi.json`
- `contracts/nemesis_pool.abi.json`
- `contracts/nemesis_router.abi.json`
- `contracts/nemesis_vault.abi.json`
- `contracts/nemesis_addresses.json`

Known Sepolia addresses:

- Factory: `0xbf301098692a47cf6861877e9acef55c9653ae50`
- Router: `0xa1f78bed1a79b9aec972e373e0e7f63d8cace4a8`
- WETH: `0x7b79995e5f793A07Bc00c21412e50Ecae098E7f9`
- DAI: `0xd67215fd6c0890493f34af3c5e4231ce98871fcb`
- USDC: `0x10279e6333f9d0EE103F4715b8aaEA75BE61464C`
- BTC: `0x2591230465a68d924fbcba5e3304c2eda0d52e5b`
- MINABO: `0xea4daaf49bd55021c23b70b194b4436f199a7606`
- NEMESIS: `0x47b7ed0e04edab477c46543bdf766acea155dd2f`
- NEM: `0x8d427943b850179300b372483aace7b887845bf3`
- ONE: `0x80d494d084087af738987f2e2807099e35867e10`

## Dry Run

To print transaction calldata without sending, set:

```env
SEND_TX=false
```

## Notes

- The client uses 5% slippage for router quotes where automatic quote calculation is implemented.
- Liquidity removal uses `amountAMin=0` and `amountBMin=0`.
- Some experimental pairs or modes may revert depending on pool/manager liquidity and contract rules.
