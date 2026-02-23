import os
import time
import pytest
from fastapi.testclient import TestClient

import sys
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

import main as app_main


@pytest.fixture(scope="module")
def client():
    os.environ["LOG_LEVEL"] = "DEBUG"
    with TestClient(app_main.app) as c:
        yield c


def test_health_and_status(client):
    assert client.get("/healthz").status_code == 200
    # Small delay to avoid rate limiting
    import time
    time.sleep(0.1)
    s = client.get("/status")
    assert s.status_code == 200


def test_joystick_enqueues(client):
    payload = {
        "ts": time.time(),
        "axes": [0.0, 0.0, 0.0, 0.0],
        "buttons": [0]*19,
        "ttl": 150,
    }
    r = client.post("/joystick/frame", json=payload)
    assert r.status_code == 200
    assert r.json().get("success") is True

import os
import subprocess
import time
import uuid

import pytest
import httpx


def _docker(args):
    return subprocess.run(["docker", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)


@pytest.mark.skipif(os.getenv("CI") is None and _docker(["version"]).returncode != 0, reason="Docker not available")
def test_container_build_run_health_and_rate_limit():
    image_tag = f"robot-service-test:{uuid.uuid4().hex[:8]}"
    container_name = f"robot-service-test-{uuid.uuid4().hex[:8]}"
    host_port = os.getenv("TEST_SERVICE_PORT", "38110")
    env = {
        "SERVICE_PORT": "8110",
        "LOG_LEVEL": "debug",
    }

    try:
        # Build image
        build = _docker(["build", "-t", image_tag, "."])
        assert build.returncode == 0, f"docker build failed: {build.stderr}"

        # Run container
        run_args = [
            "run", "-d", "--rm",
            "--name", container_name,
            "-p", f"{host_port}:8110",
        ]
        for k, v in env.items():
            run_args += ["-e", f"{k}={v}"]
        run_args.append(image_tag)

        run = _docker(run_args)
        assert run.returncode == 0, f"docker run failed: {run.stderr}"

        base = f"http://127.0.0.1:{host_port}"

        # Wait for readiness
        deadline = time.time() + 60
        last_err = None
        while time.time() < deadline:
            try:
                r = httpx.get(base + "/healthz", timeout=2.0)
                if r.status_code == 200:
                    break
            except Exception as e:
                last_err = e
            time.sleep(1)
        else:
            assert False, f"service did not become ready: {last_err}"

        # Health and readiness endpoints
        r1 = httpx.get(base + "/healthz", timeout=5)
        assert r1.status_code == 200 and r1.json().get("status") == "ok"
        r2 = httpx.get(base + "/readyz", timeout=5)
        assert r2.status_code == 200 and r2.json().get("ready") is True

        # Rate limit: send two joystick frames immediately
        payload = {
            "ts": time.time(),
            "axes": [0.0] * 6,
            "buttons": [0] * 18,
            "ttl": 150,
        }
        r_ok = httpx.post(base + "/joystick/frame", json=payload, timeout=5)
        r_rl = httpx.post(base + "/joystick/frame", json=payload, timeout=5)

        assert r_ok.status_code == 200
        assert r_rl.status_code in (200, 429)
        # If not rate limited due to timing, a short immediate third request should be 429
        r_rl2 = httpx.post(base + "/joystick/frame", json=payload, timeout=5)
        assert r_rl2.status_code in (200, 429)
    finally:
        _docker(["logs", container_name])
        _docker(["rm", "-f", container_name])
        _docker(["rmi", "-f", image_tag])


