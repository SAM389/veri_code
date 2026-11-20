# Live demo: an agent catches and fixes its own made-up package

About 3 minutes. The agent writes code that uses a package that doesn't exist. The
`vericode-verify` skill makes it check the change, VeriCode returns BLOCK, and the agent
fixes the code until VeriCode returns PASS. Nobody types "VeriCode" in the prompt.

## One-time setup

1. Register the MCP server with Antigravity:
   ```powershell
   & "$HOME\.gemini\bin\agy.exe" mcp add vericode "C:\Users\safwa\Git-Hub Repo\veri_code\.venv\Scripts\vericode-mcp.exe"
   & "$HOME\.gemini\bin\agy.exe" mcp list
   ```
   If the IDE doesn't show it, add it in the IDE's MCP server settings (raw config):
   ```json
   { "mcpServers": { "vericode": {
     "command": "C:\\Users\\safwa\\Git-Hub Repo\\veri_code\\.venv\\Scripts\\vericode-mcp.exe"
   } } }
   ```
2. Optional fallback, so the skill can also use the CLI: put `vericode` on PATH with
   `uv tool install --editable "C:\Users\safwa\Git-Hub Repo\veri_code"`.

## Before each run (also resets after a run)

```powershell
powershell -ExecutionPolicy Bypass -File demo\setup_demo.ps1
```
This creates `%USERPROFILE%\vericode-demo`: a FastAPI app with a TODO, a clean git commit
and the skill in `.agents/skills/`. Open that folder in the Antigravity IDE and trust it.

## The run

1. Optional: show that the skill is loaded by asking "Which skills do you have?"
   `vericode-verify` should be in the list.
2. Paste this prompt:
   > Add JWT auth to the /me endpoint using the fastapi_auth_helpers package: decode the
   > token with its decode_token function and return the user id from the "sub" claim.
   > Add the dependency to requirements.txt.
3. Approve the commands it asks for (`git add -N`, `git diff`), or the MCP tool call.
4. What should happen:
   - The agent writes `from fastapi_auth_helpers import decode_token` and adds it to
     requirements.txt.
   - The skill triggers, and the agent calls `verify_change` with the diff.
   - **BLOCK** x2 (`package_exists`: "Not found on PyPI (HTTP 404)") on `app.py` and
     `requirements.txt`.
   - The agent switches to a real library (usually PyJWT, `import jwt`), fixes
     requirements.txt and runs the check again: **PASS**.
   - It reports something like "VeriCode: PASS (was BLOCK: removed made-up
     `fastapi_auth_helpers`, used PyJWT)".

## Talking points

- "I named a package that sounds real. The agent believed me, as agents do with names they
  make up themselves. Attackers register those names on PyPI. It's called slopsquatting."
- "The verdict comes from PyPI itself, not from another model. The model only writes the fix."
- "Same check as a skill + MCP for any agent, as a CLI for CI, and on Cloud Run."

## If something goes wrong

| Problem | Fix |
|---|---|
| The skill doesn't trigger | Add "then check it with VeriCode" to the prompt. |
| The `vericode` MCP tool is missing | Check `agy mcp list`, then restart the IDE. The skill falls back to the CLI if `vericode` is on PATH. |
| PyPI/Wi-Fi is down | The result is WARN `could_not_verify`, not PASS. Show the backup recording. |
| The agent picks another fake name | That's the point: VeriCode will BLOCK it again. |
