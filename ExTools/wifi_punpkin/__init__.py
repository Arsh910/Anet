# ExTools/wifi_punpkin/__init__.py

import asyncio
import os
import json
import httpx
from pathlib import Path

# Base directory for the wifipumpkin3 installation
WP3_DIR = Path("/home/arshdeeppalial/Projects/Anet/ExTools/wifi_punpkin/wifipumpkin3")

SCHEMA = {
    "name": "wifi_pumpkin",
    "description": "Powerful framework for rogue access point attacks. Can use a running REST API (Way B) or local execution (Way A).",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["execute", "scan", "status", "ap_start", "ap_stop"],
                "description": "The action to perform."
            },
            "commands": {
                "type": "string",
                "description": "String of commands (e.g., 'use modules.wifi.wifiscan; run') for action='execute'."
            },
            "mode": {
                "type": "string",
                "enum": ["api", "local"],
                "default": "api",
                "description": "Force use of 'api' (requires wifipumpkin3 --rest running) or 'local' (subprocess)."
            }
        },
        "required": ["action"]
    }
}

class WP3Client:
    def __init__(self):
        self.base_url = os.getenv("WP3_API_URL", "http://localhost:1337").rstrip("/")
        self.username = os.getenv("WP3_API_USERNAME", "wp3admin")
        self.password = os.getenv("WP3_API_PASSWORD", "password")
        self.token = None

    async def _get_token(self):
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(
                    f"{self.base_url}/api/v1/authenticate/",
                    auth=(self.username, self.password),
                    timeout=10
                )
                if resp.status_code == 200:
                    self.token = resp.json().get("token")
                    return True
            except Exception:
                pass
        return False

    async def call_api(self, endpoint, method="GET", data=None):
        if not self.token and not await self._get_token():
            return {"error": "Authentication failed or API not running."}

        headers = {"x-access-token": self.token}
        async with httpx.AsyncClient() as client:
            try:
                if method == "GET":
                    resp = await client.get(f"{self.base_url}/api/v1/{endpoint}", headers=headers, timeout=30)
                else:
                    resp = await client.post(f"{self.base_url}/api/v1/{endpoint}", headers=headers, json=data, timeout=30)
                
                if resp.status_code == 401: # Token expired?
                    if await self._get_token():
                        return await self.call_api(endpoint, method, data)
                
                return resp.json()
            except Exception as e:
                return {"error": str(e)}

async def run_local(cmd_args: list) -> str:
    """Fallback: Way A (Subprocess)"""
    full_cmd = ["sudo", "python3", "-m", "wifipumpkin3", "--no-colors"] + cmd_args
    try:
        process = await asyncio.create_subprocess_exec(
            *full_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(WP3_DIR)
        )
        stdout, stderr = await process.communicate()
        return (stdout.decode() + "\n" + stderr.decode()).strip()
    except Exception as e:
        return f"Local execution failed: {e}"

async def run(params: dict) -> str:
    action = params.get("action")
    mode = params.get("mode", "api")
    commands = params.get("commands", "")

    client = WP3Client()

    if mode == "api":
        # Check if API is responsive
        if action == "execute":
            res = await client.call_api("commands", method="POST", data={"commands": commands})
            return json.dumps(res, indent=2)
        
        elif action == "scan":
            # Command specifically for scan via shell inside API
            res = await client.call_api("commands", method="POST", data={"commands": "use wifiscan; set timeout 10; run"})
            return json.dumps(res, indent=2)
            
        elif action == "status":
            # Just some general info
            ap_info = await client.call_api("config/accesspoint")
            return json.dumps(ap_info, indent=2)

        elif action == "ap_start":
            res = await client.call_api("commands", method="POST", data={"commands": "start"})
            return json.dumps(res, indent=2)

        elif action == "ap_stop":
            res = await client.call_api("commands", method="POST", data={"commands": "stop"})
            return json.dumps(res, indent=2)

        # If we got an error saying API not running, we could fallback, but let's be explicit
        if isinstance(res, dict) and "error" in res:
            return f"API Mode failed: {res['error']}. Ensure 'sudo python3 -m wifipumpkin3 --rest' is running."

    # Way A (Local)
    if action == "execute":
        return await run_local(["-x", commands])
    elif action == "scan":
        return await run_local(["-x", "use wifiscan; set timeout 10; run"])
    elif action == "status":
        return await run_local(["--version"])
    
    return f"Action '{action}' not fully implemented for local mode. Use API mode (Way B) for persistent actions."
