import reqeusts


def fetch_user(user_id: int) -> dict:
    return reqeusts.get(f"https://api.example.com/users/{user_id}", timeout=10).json()
