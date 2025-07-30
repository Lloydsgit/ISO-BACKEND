# -*- coding: utf-8 -*-
"""crypto_utils.py - Crypto Payout Utilities (Production Ready)

Handles ERC20 (Ethereum) and TRC20 (Tron) token payouts.

Dependencies:
- web3
- tronpy
"""

import os
import logging
from datetime import datetime
from decimal import Decimal

from web3 import Web3
from tronpy import Tron
from tronpy.keys import PrivateKey
from tronpy.exceptions import TronError

logging.basicConfig(level=logging.INFO)

# --- ERC20 ABI (simplified) ---
def erc20_abi():
    return [
        {"constant": False, "inputs": [
            {"name": "_to", "type": "address"},
            {"name": "_value", "type": "uint256"}
        ], "name": "transfer", "outputs": [
            {"name": "", "type": "bool"}
        ], "type": "function"},
        {"constant": True, "inputs": [], "name": "decimals", "outputs": [
            {"name": "", "type": "uint8"}
        ], "type": "function"}
    ]

def send_erc20_payout(private_key, to_address, amount, contract_address, infura_url):
    logging.info(f"[{datetime.now()}] Initiating ERC20 payout to {to_address} for {amount}")

    if not all([private_key, to_address, amount, contract_address, infura_url]):
        raise ValueError("Missing ERC20 payout parameters.")

    try:
        web3 = Web3(Web3.HTTPProvider(infura_url))
        if not web3.is_connected():
            raise ConnectionError("Failed to connect to Ethereum node.")

        acct = web3.eth.account.from_key(private_key)
        contract = web3.eth.contract(address=web3.to_checksum_address(contract_address), abi=erc20_abi())

        decimals = contract.functions.decimals().call()
        amt_wei = int(Decimal(str(amount)) * (10 ** decimals))
        nonce = web3.eth.get_transaction_count(acct.address)

        tx = contract.functions.transfer(
            web3.to_checksum_address(to_address), amt_wei
        ).build_transaction({
            'chainId': web3.eth.chain_id,
            'gas': 90000,
            'gasPrice': web3.eth.gas_price,
            'nonce': nonce,
        })

        signed_tx = acct.sign_transaction(tx)
        tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
        return web3.to_hex(tx_hash)

    except Exception as e:
        logging.error(f"[{datetime.now()}] ERC20 payout failed: {e}", exc_info=True)
        raise

def send_trc20_payout(tron_private_key, to_address, amount, contract_address):
    logging.info(f"[{datetime.now()}] Initiating TRC20 payout to {to_address} for {amount}")

    if not all([tron_private_key, to_address, amount, contract_address]):
        raise ValueError("Missing TRC20 payout parameters.")

    try:
        client = Tron()
        priv_key = PrivateKey(bytes.fromhex(tron_private_key))
        contract = client.get_contract(contract_address)

        decimals = contract.functions.decimals()
        if callable(decimals):
            decimals = decimals()

        amt = int(Decimal(str(amount)) * (10 ** decimals))

        txn = (
            contract.functions.transfer(to_address, amt)
            .with_owner(priv_key.public_key.to_base58check_address())
            .fee_limit(2_000_000)
            .build()
            .sign(priv_key)
        )

        result = txn.broadcast()
        txid = result.get('txid')

        if not txid:
            msg = result.get('message', str(result))
            raise TronError(f"TRC20 broadcast failed: {msg}")

        return txid

    except Exception as e:
        logging.error(f"[{datetime.now()}] TRC20 payout failed: {e}", exc_info=True)
        raise

def process_crypto_payout(wallet: str, amount, currency: str, network: str) -> str:
    """
    Unified interface for triggering ERC20 or TRC20 payouts.
    """
    network = network.upper()
    amount = Decimal(str(amount))

    if network == "ERC20":
        return send_erc20_payout(
            private_key=os.getenv("ERC20_PRIVATE_KEY"),
            to_address=wallet,
            amount=amount,
            contract_address=os.getenv("ERC20_CONTRACT_ADDRESS"),
            infura_url=os.getenv("INFURA_URL")
        )

    elif network == "TRC20":
        return send_trc20_payout(
            tron_private_key=os.getenv("TRC20_PRIVATE_KEY"),
            to_address=wallet,
            amount=amount,
            contract_address=os.getenv("TRC20_CONTRACT_ADDRESS")
        )

    else:
        raise ValueError("Unsupported network: must be ERC20 or TRC20")
