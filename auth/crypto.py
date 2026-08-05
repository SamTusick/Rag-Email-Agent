from cryptography.fernet import Fernet

import config


def encrypt(plaintext):
    return Fernet(config.TOKEN_ENCRYPTION_KEY).encrypt(plaintext.encode()).decode()


def decrypt(ciphertext):
    return Fernet(config.TOKEN_ENCRYPTION_KEY).decrypt(ciphertext.encode()).decode()
