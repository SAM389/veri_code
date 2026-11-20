from fastapi import FastAPI
from fastapi_auth_helpers import require_api_key

app = FastAPI()


@app.get("/items")
@require_api_key
def list_items():
    return []
