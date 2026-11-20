import config
import utils
from app.models import Item
from fastapi import FastAPI

app = FastAPI(debug=config.DEBUG)


@app.get("/items/{name}")
def get_item(name: str) -> Item:
    return Item(name=utils.slugify(name))
