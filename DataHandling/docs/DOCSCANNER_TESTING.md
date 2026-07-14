# Testing the Medical Report Summarizer

This guide explains how to test if the summarizer is working correctly.

## Prerequisites

1. **AWS Credentials**: Make sure your AWS credentials are configured (via `~/.aws/credentials` or environment variables)
2. **Environment Variables**: Set up your `.env` file or environment variables:
   ```bash
   AWS_REGION=us-east-1  # or your region
   BEDROCK_MODEL_ID=amazon.nova-2-lite-v1:0
   # Optional: If you have a specific inference profile
   BEDROCK_INFERENCE_PROFILE=us.amazon.nova-2-lite-v1:0
   ```
3. **Dependencies**: Ensure all required packages are installed:
   ```bash
   pip install boto3 pillow pdf2image requests python-dotenv
   ```

## Quick Test

The simplest way to test is using the quick test script:

```bash
cd DataHandling/docscanner
python quick_test.py ../Doc-scanner/F5.large.jpg
```

This will:
- Process the file
- Display the JSON summary
- Show basic validation results

## Comprehensive Test

For a more detailed test with validation and multiple test cases:

```bash
cd DataHandling/docscanner
python test_summarizer.py ../Doc-scanner/F5.large.jpg
```

Or test with multiple files:

```bash
python test_summarizer.py ../Doc-scanner/F5.large.jpg ../Doc-scanner/F3.large.jpg
```

## Test from Python Code

You can also test directly in Python:

```python
from pathlib import Path
from docscanner.service import summarize_report_from_path

# Test with a file path
file_path = Path("path/to/your/report.jpg")
summary = summarize_report_from_path(file_path)

print(summary)
```

Or test with bytes (simulating API usage):

```python
from docscanner.service import summarize_report_from_bytes

with open("path/to/report.jpg", "rb") as f:
    file_bytes = f.read()

summary = summarize_report_from_bytes(file_bytes, "report.jpg")
print(summary)
```

## Expected Output Structure

The summarizer should return a JSON object with this structure:

```json
{
  "document_type": "MRI / X-ray / CT / Blood Report / etc.",
  "patient_info": {
    "name": "Patient Name",
    "age": "Age",
    "sex": "M/F",
    "id": "Patient ID"
  },
  "study_details": {
    "modality": "MRI / CT / X-ray / etc.",
    "date": "Study Date",
    "institution": "Hospital/Clinic Name"
  },
  "findings": [
    {
      "title": "Finding Title",
      "details": "Detailed description"
    }
  ],
  "measurements": [
    {
      "label": "Measurement Name",
      "value": "Value",
      "units": "Units",
      "anatomical_location": "Location"
    }
  ],
  "impression": "Overall impression and conclusion",
  "chart_recommendations": [
    {
      "chart_type": "Chart Type",
      "description": "Description",
      "data_points": [
        {"label": "Label", "value": "Value"}
      ]
    }
  ],
  "notes": "Any uncertainties or missing data"
}
```

## Troubleshooting

### Error: "ValidationException: Invocation of model ID with on-demand throughput isn't supported"

**Solution**: The code should automatically handle this by using inference profiles. If you still see this error:

1. Check your AWS region matches the inference profile format
2. Set `BEDROCK_INFERENCE_PROFILE` explicitly in your environment:
   ```bash
   export BEDROCK_INFERENCE_PROFILE="us.amazon.nova-2-lite-v1:0"
   ```

### Error: "No valid documents could be processed"

**Possible causes**:
- File format not supported (only `.jpg`, `.jpeg`, `.png`, `.webp`, `.pdf` are supported)
- File is corrupted
- PDF has no pages

### Error: "Failed to parse JSON"

**Solution**: The model might have returned invalid JSON. Check the `raw_response` field in the error output to see what was returned.

### Error: AWS Credentials not found

**Solution**: Configure AWS credentials:
```bash
aws configure
# Or set environment variables:
export AWS_ACCESS_KEY_ID=your_key
export AWS_SECRET_ACCESS_KEY=your_secret
```

## Testing Different File Types

The summarizer supports:
- **Images**: `.jpg`, `.jpeg`, `.png`, `.webp`
- **PDFs**: `.pdf` (converted to images page by page)

Example:
```bash
# Test with JPG
python test_summarizer.py report.jpg

# Test with PDF
python test_summarizer.py report.pdf

# Test with multiple files
python test_summarizer.py report1.jpg report2.pdf report3.png
```

## Integration Testing

To test the full integration (as used in the server):

```python
from docscanner.service import summarize_multiple_reports

# Simulate multiple file uploads
file_data_list = [
    (open("file1.jpg", "rb").read(), "file1.jpg"),
    (open("file2.pdf", "rb").read(), "file2.pdf"),
]

summary = summarize_multiple_reports(file_data_list)
print(summary)
```

## Performance Testing

To measure processing time:

```python
import time
from docscanner.service import summarize_report_from_path

start = time.time()
summary = summarize_report_from_path(Path("report.jpg"))
elapsed = time.time() - start

print(f"Processing took {elapsed:.2f} seconds")
```

## Validation Checklist

When testing, verify:
- ✅ No "error" key in the response
- ✅ All required top-level keys are present
- ✅ `patient_info` has all required fields
- ✅ `study_details` has all required fields
- ✅ `findings` is a list (can be empty)
- ✅ `measurements` is a list (can be empty)
- ✅ `impression` is a string (can be empty)
- ✅ JSON is valid and parseable




