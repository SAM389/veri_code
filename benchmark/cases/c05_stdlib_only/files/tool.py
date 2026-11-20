import argparse
import asyncio
import concurrent.futures
import csv
import dataclasses
import json
import pathlib
import sqlite3
import tomllib
import zoneinfo


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=pathlib.Path)
    args = parser.parse_args()
    print(json.dumps({"path": str(args.path)}))


if __name__ == "__main__":
    asyncio.run(asyncio.sleep(0))
    main()
