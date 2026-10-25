from app.core.security import hash_password, verify_password

def test_hashing():
    password = "this is a test pass."
    hashed_password = hash_password(password=password)
    assert password != hashed_password

def test_correct_password_succeeds():
    password = "SecurePassword123!"
    hashed = hash_password(password)

    assert verify_password(password, hashed) is True


def test_incorrect_password_fails():
    password = "SecurePassword123!"
    hashed = hash_password(password)

    assert verify_password("WrongPassword456!", hashed) is False
