import logging
from aiohttp import ClientSession
import xml.etree.ElementTree as ET
from base64 import encodebytes, b64encode

import qrcode
from Crypto.Cipher import AES


BASE_URL = "https://mobileid.kw.ac.kr"

# Add a session variable at module level
_session = None


class LibraryLoginError(Exception):
    """The library service rejected the login itself (non-200, bad XML, ...).

    __str__ is deliberately built here rather than left to the default
    Exception formatting, so a bare `logging.error(e)` on this never repeats
    the full response body (student id, phone number) that mobileid.kw.ac.kr
    puts in it.
    """


class LibraryLoginRejected(LibraryLoginError):
    """The library service understood the request and refused it.

    `result_msg` is the student's own data reflected back by KLAS (it may
    contain their phone number) - safe to show to that same user, but never
    log it: only `result_code` goes into `str(self)`.
    """

    def __init__(self, result_code: str, result_msg: str):
        self.result_code = result_code
        self.result_msg = result_msg
        super().__init__(f"Library login rejected (result_code={result_code})")


async def get_session():
    global _session
    if _session is None or _session.closed:
        _session = ClientSession()
    return _session


async def close_session():
    """Close the shared session; called on shutdown to avoid a dangling session."""
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


def encode(msg: str):
    return b64encode(msg.encode(encoding="utf-8")).decode()


def encrypt(msg, secret):
    iv = bytearray([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    cipher = AES.new(secret.encode(encoding="utf-8"), AES.MODE_CBC, iv)

    def fill_padding(msg):
        padding_sz = 16
        pad = lambda s: s + (padding_sz - len(s) % padding_sz) * chr(
            padding_sz - len(s) % padding_sz
        )
        return pad(msg).encode()

    return encodebytes(cipher.encrypt(fill_padding(msg))).decode().strip()


async def get_secret_key(real_id: str) -> str:
    url = f"{BASE_URL}/mobile/MA/xml_user_key.php"
    data = {"user_id": encode(real_id)}

    session = await get_session()
    async with session.post(url, data=data) as response:
        if response.status != 200:
            raise Exception(f"Failed to fetch secret key: {response.status}")

        response_data = await response.text()
        return parse_xml_response(response_data, "sec_key")


async def library_login(std_number: str, phone: str, password: str, secret: str) -> str:
    """Log in to the library service and return the auth key.

    Raises `LibraryLoginRejected` when the server understood the request and
    refused it (e.g. result_code=1, "phone number not registered to this
    account") and `LibraryLoginError` for anything else that kept an auth key
    from coming back (bad status, unparseable XML). Never returns None -
    callers that used to check `if not auth_key` should catch these instead.
    """
    url = f"{BASE_URL}/mobile/MA/xml_login_and.php"
    encrypted_password = encrypt(password, secret)
    data = {
        "real_id": encode("0" + std_number),
        "rid": encode(std_number),
        "device_gb": "A",
        "tel_no": phone,
        "pass_wd": encrypted_password,
    }

    session = await get_session()
    async with session.post(url, data=data) as response:
        if response.status != 200:
            logging.warning(f"Library login failed: HTTP {response.status}")
            raise LibraryLoginError(f"http_status:{response.status}")

        # The service replies in UTF-8 (its own XML declaration says so); the
        # iso-8859-1 this used to hardcode mangled every non-ASCII byte,
        # turning result_msg's Korean into mojibake.
        response_data = await response.text(encoding="utf-8")
        try:
            auth_key = parse_xml_response(response_data, "auth_key")
        except ET.ParseError:
            logging.warning("Library login response was not valid XML")
            raise LibraryLoginError("invalid_xml") from None

        if not auth_key:
            # Log only the code - the full body carries the student's id and
            # phone number back in plain text.
            result_code = parse_xml_response(response_data, "result_code")
            result_msg = parse_xml_response(response_data, "result_msg")
            logging.warning(
                f"Library login rejected (result_code={result_code})"
            )
            raise LibraryLoginRejected(result_code, result_msg)

        return auth_key


async def get_qr_code(real_id: str, auth_key: str) -> dict:
    url = f"{BASE_URL}/mobile/MA/xml_userInfo_auth.php"
    data = {"real_id": encode(real_id), "auth_key": auth_key, "new_check": "Y"}

    session = await get_session()
    async with session.post(url, data=data) as response:
        if response.status != 200:
            raise Exception(f"Failed to get QR code: {response.status}")

        response_data = await response.text(encoding="utf-8")
        try:
            root = ET.fromstring(response_data)
            return {
                "qr_code": (
                    root.find(".//qr_code").text
                    if root.find(".//qr_code") is not None
                    else None
                ),
                # "user_name": (
                #     root.find(".//user_name").text
                #     if root.find(".//user_name") is not None
                #     else None
                # ),
                # "user_code": (
                #     root.find(".//user_code").text
                #     if root.find(".//user_code") is not None
                #     else None
                # ),
                # "user_deptName": (
                #     root.find(".//user_deptName").text
                #     if root.find(".//user_deptName") is not None
                #     else None
                # ),
            }
        except ET.ParseError as e:
            # Not logging response_data here either - same body, same PII.
            logging.error(f"Failed to parse QR XML response: {e!r}")
            raise Exception(f"Failed to parse XML response: {e}") from e


def parse_xml_response(xml_string: str, tag: str) -> str:
    root = ET.fromstring(xml_string)
    for element in root.iter(tag):
        # Extract the text inside the CDATA section if present
        return element.text.strip() if element.text else None
    return None


async def generate_qr_code(qr_data: str, path: str):
    if "qr_code" not in qr_data or len(qr_data["qr_code"]) < 5:
        logging.error("Error: Invalid QR code data.")
        return

    qr_value = qr_data["qr_code"]

    # Optimized QR parameters
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=8,  # Reduced box size
        border=4,  # Reduced border
    )

    qr.add_data(qr_value)
    qr.make(fit=True)

    img = qr.make_image(fill="black", back_color="white")
    img.save(path, format="PNG", optimize=True)  # Add optimization for PNG

    return path


async def get_qr(std_number: str, phone_number: str, password: str, path: str):
    real_id = "0" + std_number
    try:
        secret = await get_secret_key(real_id)
        auth_key = await library_login(std_number, phone_number, password, secret)
        qr_data = await get_qr_code(real_id, auth_key)
        qr_path = await generate_qr_code(qr_data, path=path)
        return qr_path
    except Exception as e:
        logging.error(f"Error in get_qr: {e}")
        return None


