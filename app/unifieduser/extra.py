import string
import secrets


def make_random_password(length=10, alphabet=None):
    if not alphabet:
        alphabet = string.ascii_letters + string.digits + "~*+-_[]#"

    while True:
        password = ''.join(secrets.choice(alphabet) for i in range(16))
        if (any(c.islower() for c in password)
                and any(c.isupper() for c in password)
                and sum(c.isdigit() for c in password) >= 3):
            break
