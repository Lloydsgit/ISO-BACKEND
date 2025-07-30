import socket
import struct
import datetime
import logging
from pyiso8583.iso8583 import Iso8583
from pyiso8583.specs import default_ascii as spec

logging.basicConfig(level=logging.INFO)

def send_iso_authorization(host, port, pan, expiry, cvv, amount_cents):
    try:
        iso = Iso8583(spec=spec)
        now = datetime.datetime.utcnow()

        iso.set_mti("0100")
        iso.set_bit(2, pan)
        iso.set_bit(3, "000000")
        iso.set_bit(4, f"{amount_cents:012}")
        iso.set_bit(7, now.strftime("%m%d%H%M%S"))
        iso.set_bit(11, "123456")
        iso.set_bit(14, expiry)
        iso.set_bit(18, "5999")
        iso.set_bit(22, "901")
        iso.set_bit(25, "00")
        iso.set_bit(35, f"{pan}D{expiry}{cvv}")
        iso.set_bit(41, "TERMID01")
        iso.set_bit(49, "840")

        msg = iso.get_network_message()

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect((host, port))

        msg_with_len = struct.pack("!H", len(msg)) + msg
        sock.sendall(msg_with_len)

        raw_len = sock.recv(2)
        if len(raw_len) < 2:
            raise Exception("Incomplete 2-byte header received")

        length = struct.unpack("!H", raw_len)[0]
        response = b''
        while len(response) < length:
            chunk = sock.recv(length - len(response))
            if not chunk:
                raise Exception("Socket connection broken")
            response += chunk
        sock.close()

        iso_response = Iso8583(spec=spec)
        iso_response.set_network_message(response)

        return {
            "approved": iso_response.get_bit(39) == "00",
            "field39": iso_response.get_bit(39),
            "mti": iso_response.get_mti(),
            "amount_received": iso_response.get_bit(4),
            "transaction_time": iso_response.get_bit(12)
        }

    except Exception as e:
        logging.exception("Error in ISO8583 communication")
        return {
            "approved": False,
            "error": str(e),
            "field39": "96"
        }
