import os

import attr
import jwt
from dateutil import parser
from dotenv import load_dotenv

load_dotenv()


@attr.define
class Token:
    raw: str

    def claims(self) -> dict:
        return jwt.decode(self.raw, os.environ["SECRET"], algorithms=["HS256"])


expires = parser.isoparse("2026-01-01T00:00:00Z")
