# client.py — Terminal ISO8583 Request Sender & Crypto Trigger

import socket
import json
import logging
import os
from decimal import Decimal
from iso8583_crypto import process_crypto_payout

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def send_iso_authorization(pan, expiry, cvv, amount_cents, host, port):
    """Send ISO 8583 MTI 0100 request to issuer server"""
    message = {
        "mti": "0100",
        "pan": pan,
        "expiry": expiry,
        "cvv": cvv,
        "amount": amount_cents
    }

    logger.info(f"Connecting to issuer server at {host}:{port}")
    with socket.create_connection((host, port), timeout=10) as sock:
        raw = json.dumps(message).encode()
        sock.sendall(raw)

        response = sock.recv(4096)
        if not response:
            raise Exception("No response from ISO server")

        decoded = json.loads(response.decode())
        logger.info(f"ISO 8583 Response: {decoded}")
        return decoded

def process_transaction(pan, expiry, cvv, amount, wallet, currency, payout_type):
    """Full flow: authorize card, then payout if approved"""
    amount_decimal = Decimal(amount)
    amount_cents = int(amount_decimal * 100)

    # ISO 8583 request
    response = send_iso_authorization(
        pan=pan,
        expiry=expiry,
        cvv=cvv,
        amount_cents=amount_cents,
        host=os.getenv("ISO_SERVER_HOST", "127.0.0.1"),
        port=int(os.getenv("ISO_SERVER_PORT", 8583))
    )

    if not response.get("approved"):
        logger.warning(f"Authorization failed: field39={response.get('field39')}")
        return {
            "status": "declined",
            "message": f"Card declined (field39={response.get('field39')})"
        }

    # Crypto payout
    try:
        tx_hash = process_crypto_payout(
            wallet=wallet,
            amount=amount_decimal,
            currency=currency,
            network=payout_type
        )

        return {
            "status": "approved",
            "tx_hash": tx_hash,
            "message": "Card approved and payout sent"
        }

    except Exception as e:
        logger.error(f"Payout failed: {e}")
        return {
            "status": "payout_failed",
            "message": str(e),
            "field39": "91"
        }

# Optional: CLI trigger for testing
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run ISO8583 client and trigger payout")
    parser.add_argument("--pan", required=True, help="Card number")
    parser.add_argument("--expiry", required=True, help="MMYY")
    parser.add_argument("--cvv", required=True, help="CVV")
    parser.add_argument("--amount", required=True, help="Amount in USD")
    parser.add_argument("--wallet", required=True, help="Crypto wallet address")
    parser.add_argument("--currency", required=True, help="Currency (e.g., USDT)")
    parser.add_argument("--payout_type", required=True, help="ERC20 or TRC20")

    args = parser.parse_args()
    result = process_transaction(
        pan=args.pan,
        expiry=args.expiry,
        cvv=args.cvv,
        amount=args.amount,
        wallet=args.wallet,
        currency=args.currency,
        payout_type=args.payout_type
    )
    print(json.dumps(result, indent=2))
