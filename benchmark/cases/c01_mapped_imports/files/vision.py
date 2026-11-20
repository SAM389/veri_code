import cv2
import yaml
from bs4 import BeautifulSoup
from PIL import Image
from sklearn.linear_model import LinearRegression


def edges(path: str):
    return cv2.Canny(cv2.imread(path), 100, 200)


def thumb(path: str) -> None:
    with Image.open(path) as img:
        img.thumbnail((256, 256))


def title(html: str) -> str:
    return BeautifulSoup(html, "html.parser").title.string


config = yaml.safe_load("a: 1")
model = LinearRegression()
