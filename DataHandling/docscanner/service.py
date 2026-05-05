import base64
import io
import json
import re
import tempfile
from pathlib import Path
from typing import List, Tuple
from urllib.parse import urlparse

import requests
from pdf2image import convert_from_path
from PIL import Image

from docscanner.client import query_bedrock
from docscanner.prompts import SUMMARY_PROMPT


SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def _encode_pil_image(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
    buffer.close()
    return encoded


def _images_from_bytes(file_bytes: bytes, suffix: str) -> List[str]:
    suffix = suffix.lower()
    if suffix in SUPPORTED_IMAGE_SUFFIXES:
        image = Image.open(io.BytesIO(file_bytes)).convert("RGB")
        return [_encode_pil_image(image)]

    if suffix == ".pdf":
        with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp_file:
            tmp_file.write(file_bytes)
            tmp_file.flush()
            images = convert_from_path(tmp_file.name)
        if not images:
            raise ValueError("PDF did not contain any pages.")
        return [_encode_pil_image(img.convert("RGB")) for img in images]

    raise ValueError(
        f"Unsupported file extension '{suffix}'. "
        f"Supported formats: {', '.join(sorted(SUPPORTED_IMAGE_SUFFIXES | {'.pdf'}))}."
    )


def _call_model_with_images(images_b64: List[str]) -> Tuple[dict, str]:
    response_text = query_bedrock(SUMMARY_PROMPT, images_b64)
    
    # Clean the response text - remove markdown code fences if present
    cleaned_text = response_text.strip()
    
    # Remove markdown code fences (```json ... ``` or ``` ... ```)
    # Handle various formats: ```json, ```JSON, ```, etc.
    if cleaned_text.startswith("```"):
        # Find the first newline after the opening fence (could be ```json, ```JSON, ```, etc.)
        lines = cleaned_text.split("\n")
        if len(lines) > 1:
            # Skip the first line (the opening fence) and join the rest
            cleaned_text = "\n".join(lines[1:])
        else:
            # If no newline, try to find where the fence ends
            fence_end = cleaned_text.find("```", 3)  # Start searching after the first ```
            if fence_end != -1:
                cleaned_text = cleaned_text[3:fence_end]
        
        # Remove closing fence if it's on its own line or at the end
        cleaned_text = cleaned_text.strip()
        if cleaned_text.endswith("```"):
            cleaned_text = cleaned_text[:-3].strip()
        # Also check if last line is just ```
        lines = cleaned_text.split("\n")
        if lines and lines[-1].strip() == "```":
            cleaned_text = "\n".join(lines[:-1]).strip()
    
    try:
        parsed = json.loads(cleaned_text)
    except json.JSONDecodeError as exc:
        # Try one more time with regex to be absolutely sure we remove all markdown
        # Remove any markdown code fences using regex - more aggressive approach
        regex_cleaned = re.sub(r'^```[a-zA-Z]*\s*\n', '', cleaned_text, flags=re.MULTILINE)
        regex_cleaned = re.sub(r'\n\s*```\s*$', '', regex_cleaned, flags=re.MULTILINE)
        regex_cleaned = re.sub(r'^```[a-zA-Z]*', '', regex_cleaned)  # Remove opening fence at start
        regex_cleaned = re.sub(r'```\s*$', '', regex_cleaned)  # Remove closing fence at end
        regex_cleaned = regex_cleaned.strip()
        
        try:
            parsed = json.loads(regex_cleaned)
        except json.JSONDecodeError as regex_exc:
            parsed = {
                "raw_response": response_text,
                "cleaned_response": cleaned_text,
                "regex_cleaned_response": regex_cleaned,
                "error": f"Failed to parse JSON. First attempt: {exc}. Regex attempt: {regex_exc}",
            }
    return parsed, response_text


def summarize_report_from_bytes(data: bytes, filename: str) -> dict:
    suffix = Path(filename).suffix or ".pdf"
    images_b64 = _images_from_bytes(data, suffix)
    parsed, _ = _call_model_with_images(images_b64)
    return parsed


def summarize_report_from_path(path: Path) -> dict:
    data = path.read_bytes()
    return summarize_report_from_bytes(data, path.name)


def summarize_report_from_url(file_url: str) -> dict:
    response = requests.get(file_url, timeout=60)
    response.raise_for_status()
    parsed_url = urlparse(file_url)
    filename = Path(parsed_url.path).name or "report.pdf"
    return summarize_report_from_bytes(response.content, filename)


def summarize_multiple_reports(file_data_list: List[Tuple[bytes, str]]) -> dict:
    """
    Summarize multiple reports together and return a combined summary.
    
    Args:
        file_data_list: List of tuples (file_bytes, filename)
    
    Returns:
        Combined summary dictionary
    """
    all_images_b64 = []
    file_info = []
    
    for file_bytes, filename in file_data_list:
        suffix = Path(filename).suffix or ".pdf"
        try:
            images = _images_from_bytes(file_bytes, suffix)
            all_images_b64.extend(images)
            file_info.append({"filename": filename, "pages": len(images)})
        except Exception as e:
            print(f"Warning: Failed to process {filename}: {e}")
            continue
    
    if not all_images_b64:
        return {
            "error": "No valid documents could be processed",
            "files_processed": file_info
        }
    
    # Use a combined prompt for multiple documents
    combined_prompt = f"""You are analyzing {len(file_data_list)} medical report(s) together.
    
Files being analyzed:
{chr(10).join(f"- {info['filename']} ({info['pages']} page(s))" for info in file_info)}

{SUMMARY_PROMPT}

IMPORTANT: Since you are analyzing multiple documents together, combine all findings, measurements, and impressions into a single comprehensive summary. 
If there are conflicting or duplicate findings across documents, note them clearly.
"""
    
    parsed, _ = _call_model_with_images(all_images_b64)
    
    # Add metadata about which files were processed
    parsed["files_processed"] = file_info
    parsed["total_documents"] = len(file_data_list)
    
    return parsed

