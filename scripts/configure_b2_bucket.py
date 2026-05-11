from __future__ import annotations

import json
import os
from pathlib import Path

import boto3


def load_local_env() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.strip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def env_or_file(name: str) -> str | None:
    file_path = os.environ.get(f"{name}_FILE")
    if file_path and Path(file_path).exists():
        return Path(file_path).read_text(encoding="utf-8").strip()
    return os.environ.get(name)


def main() -> None:
    load_local_env()
    endpoint = os.environ.get("B2_ENDPOINT", "https://s3.us-east-005.backblazeb2.com")
    region = os.environ.get("B2_REGION", "us-east-005")
    bucket = os.environ.get("B2_BUCKET", "fc-dev")
    key_id = env_or_file("B2_KEY_ID")
    app_key = env_or_file("B2_APP_KEY")
    if not key_id or not app_key:
        raise RuntimeError("B2_KEY_ID/B2_APP_KEY não configurados.")

    origins = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ]
    cors = {
        "CORSRules": [
            {
                "AllowedHeaders": ["*"],
                "AllowedMethods": ["GET", "HEAD", "PUT"],
                "AllowedOrigins": origins,
                "ExposeHeaders": ["ETag", "Content-Length", "Content-Type"],
                "MaxAgeSeconds": 3600,
            }
        ]
    }

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key_id,
        aws_secret_access_key=app_key,
        region_name=region,
    )
    client.put_bucket_cors(Bucket=bucket, CORSConfiguration=cors)
    print(json.dumps({"bucket": bucket, "region": region, "origins": origins}, ensure_ascii=False))


if __name__ == "__main__":
    main()
