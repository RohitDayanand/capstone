"""Create (or redeploy) the Render web service via the Render API.

Reads RENDER_API_KEY from .env. Usage: uv run python deploy_render.py
"""

import os
import secrets
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()

API = "https://api.render.com/v1"
NAME = "fight-night"
REPO = "https://github.com/RohitDayanand/capstone"
S = requests.Session()
S.headers.update({"Authorization": f"Bearer {os.environ['RENDER_API_KEY']}", "Accept": "application/json"})


def call(method, path, **kw):
    r = S.request(method, API + path, timeout=30, **kw)
    if not r.ok:
        sys.exit(f"{method} {path} -> {r.status_code}: {r.text}")
    return r.json() if r.text else None


def find_service():
    for item in call("GET", "/services", params={"name": NAME, "limit": 20}):
        if item["service"]["name"] == NAME:
            return item["service"]
    return None


def create_service():
    owner_id = call("GET", "/owners", params={"limit": 1})[0]["owner"]["id"]
    host_key = secrets.token_urlsafe(9)
    body = {
        "type": "web_service",
        "name": NAME,
        "ownerId": owner_id,
        "repo": REPO,
        "branch": "main",
        "autoDeploy": "yes",
        "envVars": [
            {"key": "HOST_KEY", "value": host_key},
            {"key": "PYTHON_VERSION", "value": "3.13.0"},
        ],
        "serviceDetails": {
            "runtime": "python",
            "plan": "free",
            "region": "oregon",
            "healthCheckPath": "/healthz",
            "envSpecificDetails": {
                "buildCommand": "pip install -r requirements.txt",
                "startCommand": "python server.py",
            },
        },
    }
    return call("POST", "/services", json=body)["service"]


def host_key_of(svc_id):
    for item in call("GET", f"/services/{svc_id}/env-vars"):
        if item["envVar"]["key"] == "HOST_KEY":
            return item["envVar"]["value"]


def main():
    svc = find_service()
    if svc:
        print(f"Service exists ({svc['id']}), triggering deploy...")
        call("POST", f"/services/{svc['id']}/deploys", json={})
    else:
        print("Creating service...")
        svc = create_service()
    url = svc["serviceDetails"]["url"]
    print(f"Service {svc['id']} at {url}")

    # Wait for the latest deploy to finish.
    while True:
        deploy = call("GET", f"/services/{svc['id']}/deploys", params={"limit": 1})[0]["deploy"]
        print(f"  deploy {deploy['id']}: {deploy['status']}")
        if deploy["status"] == "live":
            break
        if deploy["status"] in ("build_failed", "update_failed", "canceled", "pre_deploy_failed", "deactivated"):
            sys.exit("Deploy failed. Check logs in the Render dashboard.")
        time.sleep(15)

    print(f"\nPlayers: {url}/play")
    print(f"Host:    {url}/?key={host_key_of(svc['id'])}")


if __name__ == "__main__":
    main()
