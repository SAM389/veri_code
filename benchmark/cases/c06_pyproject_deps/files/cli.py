import httpx
import typer
from pydantic import BaseModel
from rich import print as rprint

app = typer.Typer()


class Repo(BaseModel):
    name: str
    stars: int


@app.command()
def show(owner: str, repo: str) -> None:
    data = httpx.get(f"https://api.github.com/repos/{owner}/{repo}", timeout=10).json()
    rprint(Repo(name=data["name"], stars=data["stargazers_count"]))
