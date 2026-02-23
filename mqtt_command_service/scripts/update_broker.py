#!/usr/bin/env python3
import argparse
import json
import sys

from app.services.config_service import get_config_service


def main() -> int:
    parser = argparse.ArgumentParser(description="Update MQTT broker in config storage.")
    parser.add_argument(
        "--broker",
        required=True,
        help="Resolvable hostname or IP of MQTT broker",
    )
    parser.add_argument(
        "--run-id",
        default="manual",
        help="Optional run identifier for logging",
    )
    args = parser.parse_args()

    cs = get_config_service()
    success = cs.update_config(
        {"MQTT_BROKER": args.broker},
        updated_by="debug",
        reason=f"debug broker update run={args.run_id}",
    )

    payload = {"success": bool(success), "broker": args.broker, "runId": args.run_id}
    sys.stdout.write(json.dumps(payload) + "\n")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())

