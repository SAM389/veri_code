# VeriCode

**VeriCode checks the code an AI coding agent writes, catches what the agent made up, and tells the agent exactly what to fix.**


- **Package check:** every imported or declared package really exists on PyPI, at the pinned version.
- **Symbol check:** the functions and attributes the agent calls really exist in that library (via pyright).
- **Repair loop:** an MCP tool (`verify_change`) gives the agent structured findings so it fixes its own change.

Facts come from real tools (PyPI, pyright), never from an LLM.

## Install (once released)

```bash
pip install vericode-ai
vericode check path/to/project
```

## Development

```bash
uv venv --python 3.11
uv pip install -e ".[dev]"
pytest
ruff check .
```

## License

Source-available under **MIT + [Commons Clause](https://commonsclause.com/)**. See [`LICENSE`](LICENSE).

You may use, modify and share VeriCode freely, including at work. You may **not** sell it, or sell a product, hosted service or paid support whose value comes mainly from VeriCode, without written permission from the author. For a commercial license, open an issue or contact the author.
