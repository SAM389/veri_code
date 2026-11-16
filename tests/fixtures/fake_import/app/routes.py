import fastapi_auth_helpers
import requests
from fastapi import APIRouter

router = APIRouter()


@router.get("/me")
def me(token: str):
    user = fastapi_auth_helpers.verify(token)
    return requests.get(f"https://api.example.com/users/{user}").json()
