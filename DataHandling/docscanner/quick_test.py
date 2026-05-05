#!/usr/bin/env python3
"""
Quick test script for the summarizer - minimal output version.

Usage:
    python quick_test.py <path_to_file>
"""

import sys
import json
from pathlib import Path

# Add parent directory to path so we can import docscanner
# This allows the script to work when run from any directory
script_dir = Path(__file__).parent
parent_dir = script_dir.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

from docscanner.service import summarize_report_from_path


def main():
    if len(sys.argv) < 2:
        print("Usage: python quick_test.py <path_to_file>")
        sys.exit(1)
    
    file_path = Path(sys.argv[1])
    
    if not file_path.exists():
        print(f"Error: File not found: {file_path}")
        sys.exit(1)
    
    print(f"Processing: {file_path.name}...")
    print(f"File size: {file_path.stat().st_size / 1024:.2f} KB")
    
    try:
        summary = summarize_report_from_path(file_path)
        
        if "error" in summary:
            print(f"\n❌ Error: {summary['error']}")
            if "raw_response" in summary:
                raw = summary["raw_response"]
                print(f"\nRaw response from Bedrock:")
                print("-" * 60)
                if raw:
                    print(raw)
                else:
                    print("(Empty response)")
                print("-" * 60)
                print(f"Response length: {len(raw) if raw else 0} characters")
            sys.exit(1)
        
        # Print JSON output
        print("\n" + "="*60)
        print("SUMMARY (JSON):")
        print("="*60)
        print(json.dumps(summary, indent=2))
        print("="*60)
        
        # Quick validation
        required_keys = ["document_type", "patient_info", "findings", "impression"]
        missing = [k for k in required_keys if k not in summary]
        
        if missing:
            print(f"\n⚠️  Missing keys: {missing}")
        else:
            print("\n✅ Basic structure validation passed")
        
    except Exception as e:
        print(f"❌ Exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()




