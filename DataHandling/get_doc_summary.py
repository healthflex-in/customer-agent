#!/usr/bin/env python3
"""
Utility script to retrieve document summaries for a given form_id.
Usage: 
  python get_doc_summary.py <form_id>     - Get summary for specific form
  python get_doc_summary.py --list        - List all forms with attachments
"""

import sys
import json
from server import fetch_form_by_id, fetch_form_attachments, init_mongo
import server

def list_all_forms_with_attachments():
    """List all forms that have attachments."""
    if server.customer_info_collection is None:
        init_mongo()
    
    if server.customer_info_collection is not None:
        print("❌ Cannot connect to MongoDB.")
        return
    
    try:
        # Find all forms with attachments
        forms = list(server.customer_info_collection.find(
            {"attachments": {"$exists": True, "$ne": []}},
            {"_id": 0, "formId": 1, "userId": 1, "createdAt": 1, "attachments": 1}
        ).sort("createdAt", -1))
        
        if not forms:
            print("⚠️  No forms with attachments found in database.")
            return
        
        print(f"\n{'='*60}")
        print(f"Found {len(forms)} form(s) with attachments:")
        print(f"{'='*60}\n")
        
        for idx, form in enumerate(forms, 1):
            form_id = form.get("formId", "Unknown")
            user_id = form.get("userId", "Unknown")
            created_at = form.get("createdAt", "Unknown")
            attachments = form.get("attachments", [])
            
            print(f"{idx}. Form ID: {form_id}")
            print(f"   User ID: {user_id}")
            print(f"   Created: {created_at}")
            print(f"   Attachments: {len(attachments)}")
            for att in attachments:
                filename = att.get("fileName", "Unknown")
                has_summary = "Yes" if att.get("summary") else "No"
                print(f"      - {filename} (Summary: {has_summary})")
            print()
        
        print(f"{'='*60}\n")
        
    except Exception as e:
        print(f"❌ Error listing forms: {e}")
        import traceback
        traceback.print_exc()

def get_document_summary(form_id: str):
    """Retrieve and display document summaries for a form."""
    
    print(f"\n{'='*60}")
    print(f"Document Summary for Form ID: {form_id}")
    print(f"{'='*60}\n")
    
    # Method 1: Get summary from form data (readable text)
    form = fetch_form_by_id(form_id)
    if not form:
        print(f"❌ Form {form_id} not found in database.")
        print("\n💡 Tip: Run 'python get_doc_summary.py --list' to see all available forms.")
        
        # Try to find similar form IDs
        if server.customer_info_collection is None:
            init_mongo()
        
        if server.customer_info_collection is not None:
            try:
                # Search for forms with similar IDs
                all_forms = list(server.customer_info_collection.find(
                    {},
                    {"_id": 0, "formId": 1}
                ).limit(10))
                
                if all_forms:
                    print(f"\n📋 Found {len(all_forms)} form(s) in database:")
                    for f in all_forms:
                        print(f"   - {f.get('formId', 'Unknown')}")
            except:
                pass
        
        return
    
    form_data = form.get("form_data", {})
    diagnostic_reports = form_data.get("Diagnostic Reports", {})
    reports_text = diagnostic_reports.get("Reports", "")
    
    if reports_text:
        print("📄 Summary from Form Data (Diagnostic Reports section):")
        print("-" * 60)
        print(reports_text)
        print()
    else:
        print("⚠️  No summary found in Diagnostic Reports section.")
        print()
    
    # Method 2: Get full JSON summaries from attachments
    attachments = fetch_form_attachments(form_id)
    
    if attachments:
        print(f"📎 Found {len(attachments)} attachment(s):\n")
        
        for idx, attachment in enumerate(attachments, 1):
            filename = attachment.get("fileName", "Unknown")
            summary = attachment.get("summary")
            
            print(f"Attachment {idx}: {filename}")
            print("-" * 60)
            
            if summary:
                if isinstance(summary, dict):
                    print("Full JSON Summary:")
                    print(json.dumps(summary, indent=2))
                else:
                    print(f"Summary: {summary}")
            else:
                print("⚠️  No summary found in attachment metadata.")
            
            print()
    else:
        print("⚠️  No attachments found for this form.")
    
    print(f"{'='*60}\n")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python get_doc_summary.py <form_id>     - Get summary for specific form")
        print("  python get_doc_summary.py --list        - List all forms with attachments")
        print("\nExample:")
        print("  python get_doc_summary.py a4d67a00-92f8-4905-92e6-be6fca7cbb01")
        sys.exit(1)
    
    if sys.argv[1] == "--list":
        list_all_forms_with_attachments()
    else:
        form_id = sys.argv[1]
        get_document_summary(form_id)




