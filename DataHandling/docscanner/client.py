import base64
import os
from typing import List, Optional

import boto3
from botocore.config import Config
from dotenv import load_dotenv
from app.observability.ai_usage import bedrock_usage, tracked_ai_call
from app.observability.privacy import error_type

load_dotenv()


AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "amazon.nova-2-lite-v1:0")
BEDROCK_INFERENCE_PROFILE = os.getenv("BEDROCK_INFERENCE_PROFILE", None)

_bedrock_client = None


def _get_bedrock_client():
    """
    Lazily create a Bedrock Runtime client using environment-based credentials.
    """
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=AWS_REGION,
            config=Config(retries={"max_attempts": 3, "mode": "standard"}),
        )
    return _bedrock_client


def _get_inference_profile_id() -> str:
    """
    Get the inference profile ID for Nova models.
    For Nova models, we must use an inference profile instead of the model ID directly.
    """
    # If an inference profile is explicitly set, use it
    if BEDROCK_INFERENCE_PROFILE:
        return BEDROCK_INFERENCE_PROFILE
    
    # If the model ID is already an ARN or inference profile, use it as-is
    if BEDROCK_MODEL_ID.startswith("arn:") or "inference-profile" in BEDROCK_MODEL_ID:
        return BEDROCK_MODEL_ID
    
    # For Nova models, construct the inference profile ID based on region
    # Format: {region_code}.amazon.nova-{version}
    # For us-east-1: us.amazon.nova-2-lite-v1:0
    # For other regions, use the region prefix (e.g., ap-south-1 -> ap)
    region_prefix_map = {
        "us-east-1": "us",
        "us-west-2": "us",
        "eu-west-1": "eu",
        "ap-south-1": "ap",
        "ap-southeast-1": "ap",
    }
    
    region_prefix = region_prefix_map.get(AWS_REGION, "us")
    
    # Extract the model name part (e.g., "amazon.nova-2-lite-v1:0")
    model_name = BEDROCK_MODEL_ID
    
    # Construct inference profile ID: {region_prefix}.{model_name}
    inference_profile_id = f"{region_prefix}.{model_name}"
    
    return inference_profile_id


def query_bedrock(prompt: str, images_b64: Optional[List[str]] = None) -> str:
    """
    Call Amazon Bedrock (nova-lite multimodal) with text + optional images.
    
    Note: Bedrock converse API expects:
    - Text: {"text": "..."}
    - Image: {"image": {"format": "jpeg|png|gif|webp", "source": {"bytes": ...}}}
    
    For Nova models, uses inference profile ID instead of model ID.
    """
    client = _get_bedrock_client()

    # Build content array with correct Bedrock format
    content = [{"text": prompt}]
    
    if images_b64:
        for encoded in images_b64:
            # Decode base64 into raw bytes for Bedrock image input
            image_bytes = base64.b64decode(encoded)
            
            # Detect image format from the image bytes
            # Since _encode_pil_image converts everything to PNG, we check the actual bytes
            image_format = "png"  # Default (since service.py converts to PNG)
            if image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
                image_format = "png"
            elif image_bytes[:2] == b'\xff\xd8':
                image_format = "jpeg"
            elif image_bytes[:6] in [b'GIF87a', b'GIF89a']:
                image_format = "gif"
            elif image_bytes[:4] == b'RIFF' and b'WEBP' in image_bytes[:12]:
                image_format = "webp"
            
            # Use correct Bedrock format: {"image": {"format": "...", "source": {"bytes": ...}}}
            # Note: Bedrock expects {"image": {...}} not {"type": "image", ...}
            content.append({
                "image": {
                    "format": image_format,
                    "source": {
                        "bytes": image_bytes
                    }
                }
            })

    # Use inference profile ID for Nova models
    inference_profile_id = _get_inference_profile_id()
    print(f"[DEBUG] Using inference profile ID: {inference_profile_id}")
    print(f"[DEBUG] AWS Region: {AWS_REGION}")
    print(f"[DEBUG] Number of images: {len(images_b64) if images_b64 else 0}")
    
    try:
        response = tracked_ai_call(
            provider="aws_bedrock",
            model=BEDROCK_MODEL_ID,
            operation="report_summary",
            call=lambda: client.converse(
                modelId=inference_profile_id,
                messages=[{"role": "user", "content": content}],
                inferenceConfig={"maxTokens": 2000, "temperature": 0.3},
            ),
            usage_extractor=bedrock_usage,
        )
    except Exception as e:
        error_msg = f"Bedrock API error: {error_type(e)}"
        print(f"[DEBUG] {error_msg}")
        raise Exception(error_msg) from e

    # Extract the first text block from the response
    output_content = response.get("output", {}).get("message", {}).get("content", [])
    
    if not output_content:
        # Debug: print the full response structure if no content found
        print(f"[DEBUG] No content in response. Full response structure: {list(response.keys())}")
        print(f"[DEBUG] Output keys: {list(response.get('output', {}).keys())}")
        print(f"[DEBUG] Message keys: {list(response.get('output', {}).get('message', {}).keys())}")
        return ""
    
    for block in output_content:
        # Bedrock returns {"text": "..."} format
        if "text" in block:
            text = block.get("text", "")
            if not text:
                print(f"[DEBUG] Empty text in block: {block}")
            return text
    
    # If we get here, no text block was found
    print(f"[DEBUG] No text block found in output_content: {output_content}")
    return ""
