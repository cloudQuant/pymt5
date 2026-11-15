"""AES-CBC encryption helpers for MT5 Web Terminal command frames.

Messages use AES in CBC mode with a protocol-mandated all-zero 16-byte IV and
PKCS#7 padding over a 128-bit block size. Consequently, equal plaintext
blocks encrypted under the same key and IV produce equal ciphertext blocks;
this compatibility behavior is not intended as a general-purpose encryption
construction.
"""

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from pymt5.constants import INITIAL_KEY_OBFUSCATED
from pymt5.exceptions import ProtocolError
from pymt5.helpers import hex_to_bytes, obfuscation_decode


class AESCipher:
    """Encrypt and decrypt protocol payloads with AES-CBC and a zero IV.

    Every operation creates a fresh cipher context but uses the same all-zero
    IV required by the terminal protocol. PKCS#7 padding is always applied on
    encryption and removed on decryption.
    """

    def __init__(self, key: bytes):
        """Initialize a cipher with an AES-128, AES-192, or AES-256 key.

        Args:
            key: AES key containing exactly 16, 24, or 32 bytes.

        Raises:
            ProtocolError: If the key length is not valid for AES.

        """
        if len(key) not in (16, 24, 32):
            raise ProtocolError(f"invalid AES key length: {len(key)}")
        self._key = key
        self._iv = b"\x00" * 16

    @property
    def key(self) -> bytes:
        """Return the configured AES key bytes.

        Returns:
            The immutable key supplied at initialization.

        """
        return self._key

    def encrypt(self, data: bytes) -> bytes:
        """Pad and encrypt a protocol plaintext.

        PKCS#7 padding is added even when the plaintext is already block
        aligned, so aligned input gains a complete 16-byte padding block.

        Args:
            data: Plaintext bytes of any length, including empty input.

        Returns:
            AES-CBC ciphertext whose length is a positive multiple of 16 bytes.

        """
        padder = padding.PKCS7(128).padder()
        padded = padder.update(data) + padder.finalize()
        cipher = Cipher(algorithms.AES(self._key), modes.CBC(self._iv))
        encryptor = cipher.encryptor()
        return encryptor.update(padded) + encryptor.finalize()

    def decrypt(self, data: bytes) -> bytes:
        """Decrypt AES-CBC ciphertext and remove its PKCS#7 padding.

        Args:
            data: Ciphertext produced with the same key and an all-zero IV. Its
                length must be a nonzero multiple of the 16-byte AES block size.

        Returns:
            The unpadded plaintext bytes.

        Raises:
            ValueError: If the ciphertext length is invalid or its decrypted
                PKCS#7 padding is malformed.

        """
        cipher = Cipher(algorithms.AES(self._key), modes.CBC(self._iv))
        decryptor = cipher.decryptor()
        padded = decryptor.update(data) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return unpadder.update(padded) + unpadder.finalize()


def initial_key_bytes() -> bytes:
    """Derive the protocol's bootstrap AES key.

    Returns:
        Bytes obtained by deobfuscating ``INITIAL_KEY_OBFUSCATED`` and decoding
        the resulting hexadecimal text.

    """
    return hex_to_bytes(obfuscation_decode(INITIAL_KEY_OBFUSCATED))


def initial_cipher() -> AESCipher:
    """Create the cipher used before session-specific keys are negotiated.

    Returns:
        An :class:`AESCipher` initialized with :func:`initial_key_bytes`.

    """
    return AESCipher(initial_key_bytes())
