from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from pymt5.constants import INITIAL_KEY_OBFUSCATED
from pymt5.exceptions import ProtocolError
from pymt5.helpers import hex_to_bytes, obfuscation_decode


class AESCipher:
    def __init__(self, key: bytes):
        if len(key) not in (16, 24, 32):
            raise ProtocolError(f"invalid AES key length: {len(key)}")
        self._key = key
        self._iv = b"\x00" * 16

    @property
    def key(self) -> bytes:
        return self._key

    def encrypt(self, data: bytes) -> bytes:
        padder = padding.PKCS7(128).padder()
        padded = padder.update(data) + padder.finalize()
        cipher = Cipher(algorithms.AES(self._key), modes.CBC(self._iv))
        encryptor = cipher.encryptor()
        return encryptor.update(padded) + encryptor.finalize()

    def decrypt(self, data: bytes) -> bytes:
        cipher = Cipher(algorithms.AES(self._key), modes.CBC(self._iv))
        decryptor = cipher.decryptor()
        padded = decryptor.update(data) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return unpadder.update(padded) + unpadder.finalize()


def initial_key_bytes() -> bytes:
    return hex_to_bytes(obfuscation_decode(INITIAL_KEY_OBFUSCATED))


def initial_cipher() -> AESCipher:
    return AESCipher(initial_key_bytes())
