# server.py — Secure ISO8583 Card + Crypto Gateway

from flask import Flask, request, jsonify
from decimal import Decimal
import uuid
import random
import logging
import os

from dotenv import load_dotenv
from iso_client import send_iso_authorization
from iso8583_crypto import process_crypto_payout

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# Enable this for test mode (skips real crypto payouts)
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"

@app.route('/')
def home():
    return "BLACK ROCK Server is up and running"

@app.route('/process_payment', methods=['POST'])
def process_payment():
    try:
        data = request.get_json()
        required_fields = ['pan', 'expiry', 'cvv', 'amount', 'currency', 'wallet', 'payout_type']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    "status": "rejected",
                    "message": f"Missing field: {field}",
                    "field39": "99"
                })

        if not data['pan'].startswith(('4', '5', '3')):
            return jsonify({
                "status": "rejected",
                "message": "Card not supported (not Visa/MasterCard/Amex)",
                "field39": "05"
            })

        amount = Decimal(data['amount'])
        amount_cents = int(amount * 100)

        # Step 1: ISO 8583 Authorization to card issuer
        auth_response = send_iso_authorization(
            host=os.getenv("ISO_SERVER_HOST", "127.0.0.1"),
            port=int(os.getenv("ISO_SERVER_PORT", 8583)),
            pan=data['pan'],
            expiry=data['expiry'],
            cvv=data['cvv'],
            amount_cents=amount_cents
        )

        if not auth_response.get("approved"):
            return jsonify({
                "status": "rejected",
                "message": f"Card authorization failed (field39={auth_response.get('field39')})",
                "field39": auth_response.get("field39", "96")
            })

        # Step 2: Crypto payout (if approved)
        transaction_id = str(uuid.uuid4())
        arn = f"ARN{random.randint(10**11, 10**12)}"

        try:
            if DEBUG_MODE:
                payout_tx_hash = "0xDEBUG_FAKE_TX_HASH"
            else:
                payout_tx_hash = process_crypto_payout(
                    wallet=data['wallet'],
                    amount=amount,
                    currency=data['currency'],
                    network=data['payout_type']
                )

            return jsonify({
                "status": "approved",
                "message": "Transaction approved and payout sent",
                "transaction_id": transaction_id,
                "arn": arn,
                "payout_tx_hash": payout_tx_hash,
                "field39": "00"
            })

        except Exception as e:
            error_msg = str(e)
            if 'BANDWIDTH' in error_msg.upper() or 'does not exist' in error_msg:
                error_msg += " — TRON wallet likely has no energy/bandwidth or has not been activated. Try sending 0.1 TRX."

            logging.warning(f"Payout failed: {error_msg}")
            return jsonify({
                "status": "pending_payout_failed",
                "message": f"Card accepted, but payout failed: {error_msg}",
                "transaction_id": transaction_id,
                "arn": arn,
                "payout_tx_hash": None,
                "field39": "91"
            })

    except Exception as ex:
        logging.exception("General error during processing")
        return jsonify({
            "status": "rejected",
            "message": f"Unexpected error: {str(ex)}",
            "field39": "99"
        })


if __name__ == '__main__':
    from waitress import serve
    serve(app, host='0.0.0.0', port=5000)
