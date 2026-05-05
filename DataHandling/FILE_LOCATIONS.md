# File Locations - Correction Feature

## 🗂️ Directory Structure

```
/Users/chrisdev/Healthflex/Customer-agent/DataHandling/
│
├── src/
│   ├── prompts.py                    ⭐ CORE PROMPTS HERE
│   │   ├── Lines 22-36:  SYSTEM_PROMPT (mentions corrections)
│   │   ├── Lines 193-243: CORRECTION_DETECTION_PROMPT ⭐⭐⭐
│   │   ├── Lines 227-242: CORRECTION_APPLY_PROMPT
│   │   └── Lines 244-249: CORRECTION_CONFIRMATION_PROMPT
│   │
│   └── llm/
│       └── functionalities.py        ⭐ CORE LOGIC HERE
│           ├── Lines 36-48:   Import correction prompts
│           ├── Lines 435-475: detect_correction() method ⭐⭐⭐
│           ├── Lines 477-545: apply_correction() method ⭐⭐⭐
│           └── Lines 659-687: main_processor() integration ⭐⭐⭐
│
├── test_correction_feature.py        ⭐ TEST SCRIPT
├── QUICK_REFERENCE.md                📖 Quick lookup guide
├── CORRECTION_FEATURE_GUIDE.md       📖 Complete documentation
├── CORRECTION_FLOW.md                📖 Flow diagrams
├── IMPLEMENTATION_SUMMARY.md         📖 Summary of changes
└── FILE_LOCATIONS.md                 📖 This file
```

---

## ⭐ Most Important Files

### 1. Core Prompts
**File:** `src/prompts.py`  
**Lines:** 193-249  
**What:** The LLM prompts that power correction detection and application

```python
# Line 193: Detection prompt
CORRECTION_DETECTION_PROMPT = """
Analyze the user's message to determine if they are trying to 
correct previously provided information...
"""

# Line 227: Application prompt
CORRECTION_APPLY_PROMPT = """
The user wants to correct a field in the form...
"""

# Line 244: Confirmation prompt
CORRECTION_CONFIRMATION_PROMPT = """
I've updated the form based on your correction...
"""
```

### 2. Core Logic
**File:** `src/llm/functionalities.py`  
**Lines:** 435-687  
**What:** The methods that detect and apply corrections

```python
# Line 435: Detection method
def detect_correction(self, user_message):
    """Detect if user is making a correction"""
    # Uses CORRECTION_DETECTION_PROMPT
    # Returns correction_data dict or None

# Line 477: Application method
def apply_correction(self, correction_data):
    """Apply the correction to the form"""
    # Uses CORRECTION_APPLY_PROMPT
    # Returns (success, message) tuple

# Line 659: Integration in main processor
def main_processor(self, user_response):
    # STEP 1: Check for correction FIRST
    correction_data = self.detect_correction(user_response)
    if correction_data:
        success, message = self.apply_correction(correction_data)
        return message
    # STEP 2: Normal form filling
```

---

## 📝 Exact Line Numbers

### `src/prompts.py`

| Lines | Content | Purpose |
|-------|---------|---------|
| 22-36 | `SYSTEM_PROMPT` | Mentions correction detection |
| 193-225 | `CORRECTION_DETECTION_PROMPT` | Detects corrections |
| 227-242 | `CORRECTION_APPLY_PROMPT` | Applies corrections |
| 244-249 | `CORRECTION_CONFIRMATION_PROMPT` | Confirms changes |

### `src/llm/functionalities.py`

| Lines | Content | Purpose |
|-------|---------|---------|
| 36-48 | Import statements | Imports correction prompts |
| 435-475 | `detect_correction()` | Detects if user is correcting |
| 477-545 | `apply_correction()` | Applies the correction |
| 659-687 | `main_processor()` modified | Integrates correction detection |

---

## 🔍 How to Find Things

### To Modify Detection Logic
1. Open: `src/prompts.py`
2. Go to: Line 193
3. Edit: `CORRECTION_DETECTION_PROMPT`

### To Modify Application Logic
1. Open: `src/llm/functionalities.py`
2. Go to: Line 477
3. Edit: `apply_correction()` method

### To Change Integration
1. Open: `src/llm/functionalities.py`
2. Go to: Line 659
3. Edit: `main_processor()` method

### To Add New Correction Phrases
1. Open: `src/prompts.py`
2. Go to: Line 196-210
3. Add to: "Look for correction indicators like:" section

### To Add Medical Terminology
1. Open: `src/prompts.py`
2. Go to: Line 212-218
3. Add to: "IMPORTANT MEDICAL CORRECTION EXAMPLES:" section

---

## 🧪 Testing Files

### Test Script
**File:** `test_correction_feature.py`  
**Location:** `/Users/chrisdev/Healthflex/Customer-agent/DataHandling/`  
**Run:** `python test_correction_feature.py`

**What it tests:**
- Initial response with mistake
- Direct correction (ACL tear → ACL pull)
- Pain severity correction
- Ambiguous correction (clarification flow)
- Direct detection method

---

## 📚 Documentation Files

### Quick Reference
**File:** `QUICK_REFERENCE.md`  
**For:** Developers who need quick answers  
**Contains:** File locations, key methods, testing, configuration

### Complete Guide
**File:** `CORRECTION_FEATURE_GUIDE.md`  
**For:** Understanding the entire feature  
**Contains:** Architecture, use cases, behavior modes, API reference

### Flow Diagrams
**File:** `CORRECTION_FLOW.md`  
**For:** Visual learners  
**Contains:** Flow diagrams, decision trees, example scenarios

### Implementation Summary
**File:** `IMPLEMENTATION_SUMMARY.md`  
**For:** Overview of what was implemented  
**Contains:** Changes made, files modified, testing instructions

### This File
**File:** `FILE_LOCATIONS.md`  
**For:** Finding specific code locations  
**Contains:** Directory structure, line numbers, quick navigation

---

## 🎯 Quick Navigation

### "Where is the prompt for detecting corrections?"
→ `src/prompts.py` line 193

### "Where is the method that detects corrections?"
→ `src/llm/functionalities.py` line 435

### "Where is the method that applies corrections?"
→ `src/llm/functionalities.py` line 477

### "Where is correction detection integrated?"
→ `src/llm/functionalities.py` line 659

### "Where do I add new correction phrases?"
→ `src/prompts.py` line 196-210

### "Where do I add medical terminology?"
→ `src/prompts.py` line 212-218

### "Where is the test script?"
→ `test_correction_feature.py` (root of DataHandling/)

---

## 🔧 Common Modifications

### Add a New Correction Phrase

**File:** `src/prompts.py`  
**Line:** 196-210  
**Add:**
```python
Look for correction indicators like:
- "I made a mistake"
- "Actually, it was..."
- "YOUR NEW PHRASE"  # Add here
```

### Add Medical Terminology

**File:** `src/prompts.py`  
**Line:** 212-218  
**Add:**
```python
IMPORTANT MEDICAL CORRECTION EXAMPLES:
- "ACL tear" vs "ACL pull/sprain" → Primary Complaint
- "YOUR TERM" vs "ALTERNATIVE" → Field Name  # Add here
```

### Adjust Confidence Threshold

**File:** `src/prompts.py`  
**Line:** 230-235  
**Change:**
```python
# From:
4. If you can identify the field with HIGH confidence, set needs_clarification to false

# To (more cautious):
4. If you can identify the field with ABSOLUTE certainty, set needs_clarification to false

# Or (more confident):
4. If you can identify the field with MEDIUM confidence or higher, set needs_clarification to false
```

### Add Logging

**File:** `src/llm/functionalities.py`  
**Lines:** 435-545  
**Add:**
```python
print(f"[DEBUG] Your custom log message here")
```

---

## 📊 Code Statistics

### Lines of Code Added

| File | Lines Added | Purpose |
|------|-------------|---------|
| `src/prompts.py` | ~50 lines | Enhanced detection prompt |
| `src/llm/functionalities.py` | ~110 lines | Detection and application methods |
| **Total Core Code** | **~160 lines** | |
| `test_correction_feature.py` | ~200 lines | Test script |
| Documentation | ~1000 lines | 5 documentation files |

### Methods Added

| Method | Lines | Purpose |
|--------|-------|---------|
| `detect_correction()` | 40 lines | Detect corrections |
| `apply_correction()` | 68 lines | Apply corrections |
| `main_processor()` modified | 28 lines | Integration |

---

## 🗺️ Visual Map

```
User Input
    │
    ▼
main_processor() ─────────────────┐
    │                             │
    ├─ detect_correction() ◄──────┤─── CORRECTION_DETECTION_PROMPT
    │       │                     │    (src/prompts.py:193)
    │       ├─ Is correction?     │
    │       │   YES │   NO        │
    │       │       └─────────────┤─── Normal form filling
    │       ▼                     │
    ├─ apply_correction() ◄───────┤─── CORRECTION_APPLY_PROMPT
    │       │                     │    (src/prompts.py:227)
    │       ├─ Update form        │
    │       ▼                     │
    └─ Confirmation ◄─────────────┘─── CORRECTION_CONFIRMATION_PROMPT
            │                          (src/prompts.py:244)
            ▼
        User sees message
```

---

## 🎯 Your Use Case: File Locations

### "ACL Tear" → "ACL Pull" Correction

**Detection happens here:**
- File: `src/llm/functionalities.py`
- Method: `detect_correction()`
- Line: 435-475
- Uses prompt from: `src/prompts.py` line 193

**Application happens here:**
- File: `src/llm/functionalities.py`
- Method: `apply_correction()`
- Line: 477-545
- Uses prompt from: `src/prompts.py` line 227

**Integration happens here:**
- File: `src/llm/functionalities.py`
- Method: `main_processor()`
- Line: 659-687

**Medical terminology defined here:**
- File: `src/prompts.py`
- Section: "IMPORTANT MEDICAL CORRECTION EXAMPLES"
- Line: 212-218

---

## 📞 Quick Access Commands

```bash
# Open core prompts
code src/prompts.py +193

# Open core logic
code src/llm/functionalities.py +435

# Run tests
python test_correction_feature.py

# View quick reference
cat QUICK_REFERENCE.md

# View complete guide
cat CORRECTION_FEATURE_GUIDE.md

# View this file
cat FILE_LOCATIONS.md
```

---

## Summary

All correction-related code is in **2 main files**:

1. **`src/prompts.py`** (lines 193-249) - The prompts
2. **`src/llm/functionalities.py`** (lines 435-687) - The logic

Everything else is **documentation and testing**.

**Your specific use case ("ACL tear" → "ACL pull") is handled by the code in these exact locations!**

