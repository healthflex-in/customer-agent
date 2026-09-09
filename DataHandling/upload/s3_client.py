import os
import uuid
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError


AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_S3_REGION = os.getenv("AWS_S3_REGION", "us-east-1")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")

_S3_CLIENT = None


def is_s3_configured() -> bool:
    return bool(AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY and S3_BUCKET_NAME)


def get_s3_client():
    global _S3_CLIENT
    if _S3_CLIENT is None:
        if not is_s3_configured():
            raise RuntimeError("AWS S3 credentials are not configured.")
        _S3_CLIENT = boto3.client(
            "s3",
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_S3_REGION,
        )
    return _S3_CLIENT


def generate_object_key(user_id: str, form_id: str, filename: str) -> str:
    """
    Generate a unique, per-user, per-form S3 object key.
    
    Even if the *same* physical document is uploaded for multiple users,
    each upload is stored independently under that user's own namespace
    (and further grouped by form_id).
    """
    safe_name = filename.replace(" ", "_")
    unique_id = uuid.uuid4().hex
    # Per-user and per-form isolation:
    # patients/{user_id}/forms/{form_id}/{unique}_{filename}
    return f"patients/{user_id}/forms/{form_id}/{unique_id}_{safe_name}"


def upload_bytes_to_s3(
    file_bytes: bytes, key: str, content_type: Optional[str] = None
) -> str:
    client = get_s3_client()
    extra_args = {"ACL": "private"}
    if content_type:
        extra_args["ContentType"] = content_type
    try:
        client.put_object(Bucket=S3_BUCKET_NAME, Key=key, Body=file_bytes, **extra_args)
    except (ClientError, BotoCoreError) as exc:
        raise RuntimeError(f"Failed to upload to S3: {exc}") from exc

    region = AWS_S3_REGION or "us-east-1"
    base_host = f"s3.{region}.amazonaws.com"
    return f"https://{S3_BUCKET_NAME}.{base_host}/{key}"


def download_bytes_from_s3(key: str, max_bytes: int | None = None) -> bytes:
    """Download file bytes from S3 using the object key."""
    if max_bytes is not None and max_bytes <= 0:
        raise ValueError("S3 download limit must be positive")
    client = get_s3_client()
    try:
        response = client.get_object(Bucket=S3_BUCKET_NAME, Key=key)
        body = response["Body"]
        if max_bytes is None:
            return body.read()
        data = bytearray()
        while True:
            remaining = max_bytes - len(data)
            chunk = body.read(min(64 * 1024, remaining + 1))
            if not chunk:
                return bytes(data)
            data.extend(chunk)
            if len(data) > max_bytes:
                raise ValueError("S3 report exceeds the download size limit")
    except (ClientError, BotoCoreError) as exc:
        raise RuntimeError(f"Failed to download from S3: {exc}") from exc


def download_bytes_from_s3_url(s3_url: str) -> bytes:
    """Download file bytes from S3 using the full S3 URL."""
    # Extract key from URL like: https://bucket.s3.region.amazonaws.com/key
    # or s3://bucket/key
    if s3_url.startswith("s3://"):
        # s3://bucket/key format
        path = s3_url.replace(f"s3://{S3_BUCKET_NAME}/", "")
        return download_bytes_from_s3(path)
    else:
        # https://bucket.s3.region.amazonaws.com/key format
        key = s3_url.split(f"{S3_BUCKET_NAME}/", 1)[1] if f"{S3_BUCKET_NAME}/" in s3_url else s3_url
        return download_bytes_from_s3(key)
