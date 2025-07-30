import os
import json
import hashlib
import random
import logging
import socket
import threading
from decimal import Decimal
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, session, url_for, flash

# Blockchain libs
from web3 import Web3
from tronpy import Tron
from tronpy.providers import HTTPProvider
from tronpy.keys import PrivateKey

# --- CONFIG & SETUP ---

app = Flask(__name__)
app.secret_key = 'rutland_secret_key_8583'
logging.basicConfig(level=logging.INFO)

# User login
USERNAME = "blackrock"
PASSWORD_FILE = "password.json"
CONFIG_FILE = "config.json"

# ISO8583 TCP Server config
ISO_SERVER_HOST = "0.0.0.0"
ISO_SERVER_PORT = 8583

# DEBUG Mode - skip real blockchain payout (set env DEBUG_MODE=true to enable)
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"

# --- PASSWORD MANAGEMENT ---

if not os.path.exists(PASSWORD_FILE):
    with open(PASSWORD_FILE, "w") as f:
        hashed = hashlib.sha256("Br_3339".encode()).hexdigest()
        json.dump({"password": hashed}, f)

def check_password(raw):
    with open(PASSWORD_FILE) as f:
        stored = json.load(f)['password']
    return hashlib.sha256(raw.encode()).hexdigest() == stored

def set_password(newpass):
    with open(PASSWORD_FILE, "w") as f:
        hashed = hashlib.sha256(newpass.encode()).hexdigest()
        json.dump({"password": hashed}, f)

# --- LOGIN DECORATOR ---

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            flash("You must be logged in.")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

PROTOCOLS = {
    "POS Terminal -101.1 (4-digit approval)": 4,
    "POS Terminal -101.4 (6-digit approval)": 6,
    "POS Terminal -201.1 (6-digit approval)": 6,
    "POS Terminal -101.7 (4-digit approval)": 4,
    "POS Terminal -101.8 (PIN-LESS transaction)": 4,
    "POS Terminal -201.3 (6-digit approval)": 6,
    "POS Terminal -201.5 (6-digit approval)": 6
}

FIELD_39_RESPONSES = {
    "00": "Approved",
    "05": "Do Not Honor",
    "54": "Expired Card",
    "82": "Invalid CVV",
    "91": "Issuer Inoperative",
    "92": "Invalid Terminal Protocol",
    "99": "System Error"
}

# --- LOAD CONFIG ---

def load_config():
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w") as f:
            json.dump({
                "erc20_wallet": "0x1234567890abcdef1234567890abcdef12345678",
                "trc20_wallet": "TXYZ1234567890abcdefghijklmnopqrs"
            }, f)
    with open(CONFIG_FILE) as f:
        return json.load(f)

CONFIG = load_config()

# --- ISO8583 TCP SERVER (Background Thread) ---

def iso8583_server_thread(host=ISO_SERVER_HOST, port=ISO_SERVER_PORT):
    def server():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, port))
            s.listen()
            logging.info(f"ISO8583 Server listening on {host}:{port}")
            while True:
                conn, addr = s.accept()
                with conn:
                    logging.info(f"ISO8583 client connected from {addr}")
                    while True:
                        data = conn.recv(2048)
                        if not data:
                            break
                        logging.info(f"Received ISO8583 data: {data}")

                        # Simulate ISO8583 Authorization Response
                        try:
                            msg = json.loads(data.decode(errors='ignore'))
                            pan = msg.get("pan")
                            expiry = msg.get("expiry")
                            cvv = msg.get("cvv")
                            amount = msg.get("amount")

                            # Dummy auth logic: check PAN in DUMMY_CARDS and match expiry/cvv
                            card = DUMMY_CARDS.get(pan)
                            if not card:
                                response = {"approved": False, "field39": "05"}  # Do Not Honor
                            elif card["expiry"] != expiry:
                                response = {"approved": False, "field39": "54"}  # Expired Card
                            elif card["cvv"] != cvv:
                                response = {"approved": False, "field39": "82"}  # Invalid CVV
                            else:
                                response = {"approved": True, "field39": "00"}

                        except Exception as e:
                            logging.error(f"Error parsing ISO8583 request: {e}")
                            response = {"approved": False, "field39": "99"}

                        response_raw = json.dumps(response).encode()
                        conn.sendall(response_raw)
    threading.Thread(target=server, daemon=True).start()

iso8583_server_thread()

# --- CRYPTO PAYOUT FUNCTIONS ---

def erc20_abi():
    return [
        {
            "constant": False,
            "inputs": [{"name": "_to", "type": "address"}, {"name": "_value", "type": "uint256"}],
            "name": "transfer",
            "outputs": [{"name": "", "type": "bool"}],
            "type": "function"
        },
        {
            "constant": True,
            "inputs": [],
            "name": "decimals",
            "outputs": [{"name": "", "type": "uint8"}],
            "type": "function"
        }
    ]

def send_erc20(to_address: str, amount: Decimal) -> str:
    infura_url = os.getenv("INFURA_URL")
    private_key = os.getenv("ERC20_PRIVATE_KEY")
    token_address = os.getenv("ERC20_CONTRACT_ADDRESS")

    web3 = Web3(Web3.HTTPProvider(infura_url))
    if not web3.is_connected():
        raise Exception("Failed to connect to Ethereum node")

    account = web3.eth.account.from_key(private_key)
    to_address = web3.to_checksum_address(to_address)
    token_address = web3.to_checksum_address(token_address)

    contract = web3.eth.contract(address=token_address, abi=erc20_abi())

    try:
        decimals = contract.functions.decimals().call()
    except Exception:
        decimals = 18

    amt = int(amount * (10 ** decimals))
    nonce = web3.eth.get_transaction_count(account.address)

    tx = contract.functions.transfer(to_address, amt).build_transaction({
        'chainId': web3.eth.chain_id,
        'gas': 100_000,
        'gasPrice': web3.eth.gas_price,
        'nonce': nonce
    })

    signed_tx = web3.eth.account.sign_transaction(tx, private_key)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)

    return web3.to_hex(tx_hash)

def send_tron(to_address: str, amount: Decimal) -> str:
    tron_private_key = os.getenv("TRC20_PRIVATE_KEY")
    token_contract = os.getenv("TRC20_CONTRACT_ADDRESS")
    tron_api_key = os.getenv("TRON_API_KEY")

    client = Tron(
        provider=HTTPProvider(endpoint_uri="https://api.trongrid.io", api_key=tron_api_key),
        network="mainnet"
    )

    pk = PrivateKey(bytes.fromhex(tron_private_key))
    contract = client.get_contract(token_contract)

    try:
        _ = client.get_account(to_address)
    except Exception:
        raise Exception(f"TRON payout failed: Destination account [{to_address}] does not exist.")

    try:
        decimals_func = contract.functions.decimals
        decimals = decimals_func() if callable(decimals_func) else decimals_func
    except Exception:
        decimals = 6

    amt = int(float(amount) * (10 ** decimals))

    txn = (
        contract.functions.transfer(to_address, amt)
        .with_owner(pk.public_key.to_base58check_address())
        .fee_limit(1_000_000)
        .build()
        .sign(pk)
    )

    result = txn.broadcast().wait()

    txid = result.get("id") or result.get("txid")
    if not txid:
        msg = result.get('message') or str(result)
        if "BANDWIDTH" in msg.upper():
            raise Exception("TRON payout failed: Bandwidth or Energy insufficient. Ensure sender wallet has enough TRX.")
        raise Exception(f"TRON payout failed: {msg}")

    return txid

def process_crypto_payout(wallet: str, amount: Decimal, currency: str, network: str) -> str:
    network = network.upper()

    def is_tron_address(addr: str) -> bool:
        return addr.startswith("T") and len(addr) >= 34

    def is_eth_address(addr: str) -> bool:
        return addr.startswith("0x") and len(addr) == 42

    if network == "TRC20":
        for var in ["TRC20_PRIVATE_KEY", "TRC20_CONTRACT_ADDRESS", "TRON_API_KEY"]:
            if not os.getenv(var):
                raise Exception(f"Missing environment variable: {var}")
        if not is_tron_address(wallet):
            raise Exception("Payout failed: Invalid TRC20 wallet address. Must start with 'T'.")
        return send_tron(wallet, amount)

    elif network == "ERC20":
        for var in ["INFURA_URL", "ERC20_PRIVATE_KEY", "ERC20_CONTRACT_ADDRESS"]:
            if not os.getenv(var):
                raise Exception(f"Missing environment variable: {var}")
        if not is_eth_address(wallet):
            raise Exception("Payout failed: Invalid ERC20 wallet address. Must start with '0x'.")
        return send_erc20(wallet, amount)

    else:
        raise Exception("Unsupported payout network")

# --- ISO8583 CLIENT FUNCTION ---

def send_iso_authorization(pan, expiry, cvv, amount_cents, host="127.0.0.1", port=ISO_SERVER_PORT):
    msg = {
        "mti": "0100",
        "pan": pan,
        "expiry": expiry,
        "cvv": cvv,
        "amount": amount_cents
    }
    logging.info(f"Connecting to ISO server at {host}:{port}")
    with socket.create_connection((host, port), timeout=10) as sock:
        raw = json.dumps(msg).encode()
        sock.sendall(raw)
        resp = sock.recv(4096)
        if not resp:
            raise Exception("No response from ISO server")
        decoded = json.loads(resp.decode())
        logging.info(f"ISO8583 Response: {decoded}")
        return decoded

# --- FLASK ROUTES ---

@app.route('/')
def home():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = request.form.get('username')
        passwd = request.form.get('password')
        if user == USERNAME and check_password(passwd):
            session['logged_in'] = True
            return redirect(url_for('protocol'))
        flash("Invalid username or password.")
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for('login'))

@app.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current = request.form.get('current')
        newpass = request.form.get('new')
        if not check_password(current):
            flash("Current password incorrect.")
            return render_template('change_password.html')
        set_password(newpass)
        flash("Password changed successfully.")
    return render_template('change_password.html')

@app.route('/protocol', methods=['GET', 'POST'])
@login_required
def protocol():
    if request.method == 'POST':
        selected = request.form.get('protocol')
        if selected not in PROTOCOLS:
            return redirect(url_for('rejected', code="92", reason=FIELD_39_RESPONSES["92"]))
        session['protocol'] = selected
        session['code_length'] = PROTOCOLS[selected]
        return redirect(url_for('amount'))
    return render_template('protocol.html', protocols=PROTOCOLS.keys())

@app.route('/amount', methods=['GET', 'POST'])
@login_required
def amount():
    if request.method == 'POST':
        amt = request.form.get('amount')
        # basic validation could be added here
        session['amount'] = amt
        return redirect(url_for('payout'))
    return render_template('amount.html')

@app.route('/payout', methods=['GET', 'POST'])
@login_required
def payout():
    if request.method == 'POST':
        method = request.form.get('method')
        wallet = request.form.get(f'{method.lower()}_wallet', '').strip()

        if method == 'ERC20' and (not wallet.startswith("0x") or len(wallet) != 42):
            flash("Invalid ERC20 wallet address.")
            return redirect(url_for('payout'))
        if method == 'TRC20' and (not wallet.startswith("T") or len(wallet) < 34):
            flash("Invalid TRC20 wallet address.")
            return redirect(url_for('payout'))

        session['payout_type'] = method
        session['wallet'] = wallet
        return redirect(url_for('card'))
    return render_template('payout.html')

@app.route('/card', methods=['GET', 'POST'])
@login_required
def card():
    if request.method == 'POST':
        pan = request.form.get('pan')
        expiry = request.form.get('expiry')
        cvv = request.form.get('cvv')

        # Optionally validate here

        session['pan'] = pan
        session['expiry'] = expiry
        session['cvv'] = cvv
        return redirect(url_for('auth'))
    return render_template('card.html')

@app.route('/auth', methods=['GET', 'POST'])
@login_required
def auth():
    if request.method == 'POST':
        auth_code = request.form.get('auth_code')
        expected_len = session.get('code_length', 4)

        if not auth_code or len(auth_code) != expected_len:
            flash(f"Authorization code must be {expected_len} digits.")
            return redirect(url_for('auth'))

        session['auth_code'] = auth_code
        session['txn_id'] = f"TXN{random.randint(100000, 999999)}"
        session['timestamp'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Prepare ISO8583 authorization request
        pan = session['pan']
        expiry = session['expiry']
        cvv = session['cvv']
        amount = session['amount']
        wallet = session['wallet']
        payout_type = session['payout_type']

        amount_decimal = Decimal(amount)
        amount_cents = int(amount_decimal * 100)

        # 1) Send ISO8583 authorization
        try:
            auth_resp = send_iso_authorization(pan, expiry, cvv, amount_cents)
        except Exception as e:
            flash(f"ISO8583 server error: {str(e)}")
            return redirect(url_for('auth'))

        if not auth_resp.get('approved'):
            code = auth_resp.get('field39', '99')
            reason = FIELD_39_RESPONSES.get(code, "Unknown error")
            flash(f"Transaction rejected: {reason} (code {code})")
            return redirect(url_for('auth'))

        # 2) Process crypto payout if approved
        try:
            if DEBUG_MODE:
                tx_hash = "0xDEBUG_FAKE_TX_HASH"
            else:
                tx_hash = process_crypto_payout(wallet, amount_decimal, "USDT", payout_type)
            session['tx_hash'] = tx_hash
            return redirect(url_for('success'))
        except Exception as e:
            flash(f"Payout failed: {str(e)}")
            return redirect(url_for('auth'))

    return render_template('auth.html', code_length=session.get('code_length', 4))

@app.route('/success')
@login_required
def success():
    return render_template('success.html',
                           txn_id=session.get('txn_id'),
                           pan=session.get('pan'),
                           amount=session.get('amount'),
                           timestamp=session.get('timestamp'),
                           wallet=session.get('wallet'),
                           payout_type=session.get('payout_type'),
                           tx_hash=session.get('tx_hash'))

@app.route('/rejected/<code>/<reason>')
def rejected(code, reason):
    return render_template('rejected.html', code=code, reason=reason)

@app.route('/health')
def health_check():
    return "OK", 200

# --- PASSWORD RESET ---

@app.route('/reset-password-request', methods=['GET', 'POST'])
def reset_password_request():
    if request.method == 'POST':
        token = request.form.get('reset_token')
        if token == os.getenv("RESET_TOKEN", "adminreset123"):
            session['allow_reset'] = True
            return redirect(url_for('reset_password'))
        else:
            flash("Invalid reset token")
    return render_template('reset_password_request.html')

@app.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    if not session.get('allow_reset'):
        flash("Unauthorized")
        return redirect(url_for('reset_password_request'))

    if request.method == 'POST':
        newpass = request.form.get('new_password')
        set_password(newpass)
        session.pop('allow_reset', None)
        flash("Password reset successful")
        return redirect(url_for('login'))
    return render_template('reset_password.html')

# --- RUN APP ---

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=True)
