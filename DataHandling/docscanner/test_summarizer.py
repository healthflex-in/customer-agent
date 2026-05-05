#!/usr/bin/env python3
"""
Test script for the medical report summarizer.

This script tests the summarizer functionality with various inputs:
- Single image files
- Multiple files
- Error handling
- JSON validation

Usage:
    python test_summarizer.py [path_to_test_file]
    
Examples:
    # Test with a specific file
    python test_summarizer.py ../Doc-scanner/F5.large.jpg
    
    # Test with multiple files
    python test_summarizer.py file1.jpg file2.pdf
    
    # Test with default test file (if exists)
    python test_summarizer.py
"""

import json
import sys
from pathlib import Path
from typing import Dict, Any

# Add parent directory to path so we can import docscanner
# This allows the script to work when run from any directory
script_dir = Path(__file__).parent
parent_dir = script_dir.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

from docscanner.service import (
    summarize_report_from_path,
    summarize_report_from_bytes,
    summarize_multiple_reports,
)


def validate_summary_structure(summary: Dict[str, Any]) -> tuple[bool, list[str]]:
    """
    Validate that the summary has the expected structure.
    
    Returns:
        (is_valid, list_of_errors)
    """
    errors = []
    required_keys = [
        "document_type",
        "patient_info",
        "study_details",
        "findings",
        "measurements",
        "impression",
        "chart_recommendations",
        "notes",
    ]
    
    for key in required_keys:
        if key not in summary:
            errors.append(f"Missing required key: {key}")
    
    # Validate nested structures
    if "patient_info" in summary:
        patient_required = ["name", "age", "sex", "id"]
        for field in patient_required:
            if field not in summary["patient_info"]:
                errors.append(f"Missing patient_info field: {field}")
    
    if "study_details" in summary:
        study_required = ["modality", "date", "institution"]
        for field in study_required:
            if field not in summary["study_details"]:
                errors.append(f"Missing study_details field: {field}")
    
    # Validate list types
    for list_key in ["findings", "measurements", "chart_recommendations"]:
        if list_key in summary and not isinstance(summary[list_key], list):
            errors.append(f"{list_key} should be a list, got {type(summary[list_key])}")
    
    return len(errors) == 0, errors


def print_summary(summary: Dict[str, Any], title: str = "Summary"):
    """Pretty print the summary with validation."""
    print(f"\n{'='*60}")
    print(f"{title}")
    print(f"{'='*60}")
    
    # Check for errors first
    if "error" in summary:
        print(f"❌ ERROR: {summary['error']}")
        if "raw_response" in summary:
            print(f"\nRaw response:\n{summary['raw_response']}")
        return
    
    # Validate structure
    is_valid, errors = validate_summary_structure(summary)
    
    if not is_valid:
        print("⚠️  Structure validation issues:")
        for error in errors:
            print(f"   - {error}")
    else:
        print("✅ Structure validation passed")
    
    # Print key information
    print(f"\n📄 Document Type: {summary.get('document_type', 'N/A')}")
    
    if "patient_info" in summary:
        patient = summary["patient_info"]
        print(f"\n👤 Patient Info:")
        print(f"   Name: {patient.get('name', 'N/A')}")
        print(f"   Age: {patient.get('age', 'N/A')}")
        print(f"   Sex: {patient.get('sex', 'N/A')}")
        print(f"   ID: {patient.get('id', 'N/A')}")
    
    if "study_details" in summary:
        study = summary["study_details"]
        print(f"\n🔬 Study Details:")
        print(f"   Modality: {study.get('modality', 'N/A')}")
        print(f"   Date: {study.get('date', 'N/A')}")
        print(f"   Institution: {study.get('institution', 'N/A')}")
    
    findings = summary.get("findings", [])
    if findings:
        print(f"\n🔍 Findings ({len(findings)}):")
        for i, finding in enumerate(findings[:3], 1):  # Show first 3
            title = finding.get("title", "Untitled")
            details = finding.get("details", "")[:100]  # First 100 chars
            print(f"   {i}. {title}: {details}...")
        if len(findings) > 3:
            print(f"   ... and {len(findings) - 3} more")
    
    measurements = summary.get("measurements", [])
    if measurements:
        print(f"\n📊 Measurements ({len(measurements)}):")
        for i, measurement in enumerate(measurements[:5], 1):  # Show first 5
            label = measurement.get("label", "N/A")
            value = measurement.get("value", "N/A")
            units = measurement.get("units", "")
            print(f"   {i}. {label}: {value} {units}")
        if len(measurements) > 5:
            print(f"   ... and {len(measurements) - 5} more")
    
    impression = summary.get("impression", "")
    if impression:
        print(f"\n💭 Impression:")
        print(f"   {impression[:200]}...")  # First 200 chars
    
    notes = summary.get("notes", "")
    if notes:
        print(f"\n📝 Notes:")
        print(f"   {notes}")
    
    # Print metadata if available
    if "files_processed" in summary:
        print(f"\n📁 Files Processed: {summary.get('total_documents', 0)}")
        for file_info in summary["files_processed"]:
            print(f"   - {file_info['filename']} ({file_info['pages']} page(s))")
    
    print(f"\n{'='*60}\n")


def test_single_file(file_path: Path):
    """Test summarization of a single file."""
    print(f"\n🧪 Testing single file: {file_path.name}")
    
    if not file_path.exists():
        print(f"❌ File not found: {file_path}")
        return False
    
    try:
        summary = summarize_report_from_path(file_path)
        print_summary(summary, f"Single File Test: {file_path.name}")
        return "error" not in summary
    except Exception as e:
        print(f"❌ Exception occurred: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_multiple_files(file_paths: list[Path]):
    """Test summarization of multiple files."""
    print(f"\n🧪 Testing multiple files ({len(file_paths)} files)")
    
    file_data_list = []
    for file_path in file_paths:
        if not file_path.exists():
            print(f"⚠️  Skipping non-existent file: {file_path}")
            continue
        try:
            file_bytes = file_path.read_bytes()
            file_data_list.append((file_bytes, file_path.name))
        except Exception as e:
            print(f"⚠️  Failed to read {file_path}: {e}")
    
    if not file_data_list:
        print("❌ No valid files to process")
        return False
    
    try:
        summary = summarize_multiple_reports(file_data_list)
        print_summary(summary, f"Multiple Files Test ({len(file_data_list)} files)")
        return "error" not in summary
    except Exception as e:
        print(f"❌ Exception occurred: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_from_bytes(file_path: Path):
    """Test summarization from bytes (simulating API usage)."""
    print(f"\n🧪 Testing from bytes: {file_path.name}")
    
    if not file_path.exists():
        print(f"❌ File not found: {file_path}")
        return False
    
    try:
        file_bytes = file_path.read_bytes()
        summary = summarize_report_from_bytes(file_bytes, file_path.name)
        print_summary(summary, f"From Bytes Test: {file_path.name}")
        return "error" not in summary
    except Exception as e:
        print(f"❌ Exception occurred: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main test function."""
    print("="*60)
    print("Medical Report Summarizer Test Suite")
    print("="*60)
    
    # Get file paths from command line or use defaults
    if len(sys.argv) > 1:
        test_files = [Path(arg) for arg in sys.argv[1:]]
    else:
        # Try to find a default test file
        default_paths = [
            Path(__file__).parent.parent / "Doc-scanner" / "F5.large.jpg",
            Path(__file__).parent.parent / "Doc-scanner" / "F3.large.jpg",
        ]
        test_files = [p for p in default_paths if p.exists()]
        
        if not test_files:
            print("\n❌ No test files provided and no default files found.")
            print("\nUsage:")
            print("  python test_summarizer.py <file1> [file2] [file3] ...")
            print("\nExample:")
            print("  python test_summarizer.py ../Doc-scanner/F5.large.jpg")
            return
    
    print(f"\n📋 Found {len(test_files)} test file(s)")
    for f in test_files:
        print(f"   - {f}")
    
    # Run tests
    results = []
    
    # Test 1: Single file (first file)
    if test_files:
        results.append(("Single File", test_single_file(test_files[0])))
    
    # Test 2: From bytes (first file)
    if test_files:
        results.append(("From Bytes", test_from_bytes(test_files[0])))
    
    # Test 3: Multiple files (if more than one)
    if len(test_files) > 1:
        results.append(("Multiple Files", test_multiple_files(test_files)))
    
    # Print summary
    print("\n" + "="*60)
    print("Test Results Summary")
    print("="*60)
    
    for test_name, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{test_name}: {status}")
    
    all_passed = all(result[1] for result in results)
    
    print("="*60)
    if all_passed:
        print("✅ All tests passed!")
    else:
        print("❌ Some tests failed. Check the output above for details.")
    print("="*60)
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())




