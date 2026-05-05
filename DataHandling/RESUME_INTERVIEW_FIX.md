# Resume Interview Fix

## Problem

When the backend reloads (during development hot-reload or server restart), the frontend reconnects and sends a `start_interview` message. The backend was **always** resetting the health_agent and creating a **new form**, losing all the user's progress.

### User's Issue:
> "see when ever there is a reload on the backend part it is creating a new form if we are on the front end interview page .... why?!"

### Root Cause:
The `start_interview` handler always called `reset_health_agent_for_new_interview()`, which:
1. Called `health_agent.init_form()` - **resets form to empty**
2. Set `client_state["form_id"] = None` - **clears the form_id**
3. Created a **new placeholder form** because `form_id` was None

## Solution Implemented

### 1. Added Resume Mode Detection

The backend now checks if the frontend is providing a `formId` in the `start_interview` message:

```python
# Check if frontend is providing a formId to resume an existing interview
provided_form_id = data.get("formId") or data.get("form_id")
is_resuming = provided_form_id is not None
```

### 2. Two Modes of Operation

**NEW INTERVIEW MODE** (no `formId` provided):
- Calls `reset_health_agent_for_new_interview()` to clear everything
- Creates a new placeholder form
- Sends welcome message
- Starts from scratch

**RESUME MODE** (`formId` provided):
- **Skips** the reset
- Loads the existing form from MongoDB
- Verifies the form belongs to the current user (security check)
- Restores `health_agent.form`, `current_section`, and `idx`
- Sets `talk_mode` to "USER" (not "START")
- Sends a "Welcome back!" message
- Asks the next question to continue where they left off

### 3. Security Check

When resuming, the backend verifies that the form belongs to the current user:

```python
form_user_id = existing_form.get("userId")
if form_user_id != provided_user_id:
    # Access denied - form belongs to different user
    return error
```

### 4. Graceful Fallback

If the provided `formId` doesn't exist in the database:
- Logs an error
- Falls back to NEW INTERVIEW MODE
- Creates a new form instead

## Frontend Changes Needed

For this fix to work, the **frontend must send the `formId`** when reconnecting:

### Current Behavior (Broken):
```javascript
// Frontend sends on reconnect
websocket.send(JSON.stringify({
  type: "start_interview",
  userId: currentUserId
}));
```

### Required Behavior (Fixed):
```javascript
// Frontend should track the current formId
let currentFormId = null; // Set when interview starts

// On reconnect, send the formId if it exists
websocket.send(JSON.stringify({
  type: "start_interview",
  userId: currentUserId,
  formId: currentFormId  // ← ADD THIS
}));
```

## Testing

### Test Case 1: New Interview
```
Frontend sends: { type: "start_interview", userId: "123" }
Backend: Creates new form, sends welcome message ✅
```

### Test Case 2: Resume Interview
```
Frontend sends: { type: "start_interview", userId: "123", formId: "abc-123" }
Backend: Loads form abc-123, sends "Welcome back!", continues interview ✅
```

### Test Case 3: Backend Reload During Interview
```
1. User is on question 5 of the interview
2. Backend reloads (hot-reload)
3. Frontend reconnects with formId
4. Backend loads the form, continues from question 5 ✅
```

### Test Case 4: Security - Wrong User
```
Frontend sends: { type: "start_interview", userId: "123", formId: "form-belonging-to-user-456" }
Backend: Returns error "Access denied: This form belongs to a different user." ✅
```

### Test Case 5: Form Not Found
```
Frontend sends: { type: "start_interview", userId: "123", formId: "non-existent-form" }
Backend: Logs error, creates new form instead ✅
```

## Files Modified

**`server.py`:**
- Modified `start_interview` handler (line ~1313)
- Added resume mode detection
- Added form loading logic
- Added security checks

## Log Messages

### New Interview:
```
[start_interview] NEW INTERVIEW MODE: No formId provided. Resetting health_agent.
[reset_health_agent] New user 123 starting interview. Resetting health_agent.
[start_interview] Creating new placeholder form for user: 123
```

### Resume Interview:
```
[start_interview] RESUME MODE: FormId abc-123 provided. Will load existing form instead of resetting.
[start_interview] Loading existing form abc-123 from MongoDB...
[start_interview] ✓ Loaded form abc-123, section: Pain Assessment, idx: 2
```

### Security Error:
```
[start_interview] SECURITY ERROR: Form abc-123 belongs to user-456, but current user is user-123
```

### Form Not Found:
```
[start_interview] ERROR: Form abc-123 not found in database. Starting new interview instead.
[start_interview] NEW INTERVIEW MODE: No formId provided. Resetting health_agent.
```

## Behavior Changes

### Before Fix:
```
User: [Answers 5 questions]
Backend: [Reloads]
Frontend: [Reconnects]
Backend: [Creates NEW form, loses all progress] ❌
User: [Has to start over from question 1] 😡
```

### After Fix (with frontend changes):
```
User: [Answers 5 questions]
Backend: [Reloads]
Frontend: [Reconnects with formId]
Backend: [Loads existing form] ✅
User: [Continues from question 6] 😊
```

## Deployment Notes

1. **Backend changes are backward compatible**: If frontend doesn't send `formId`, it still works (creates new form)
2. **Frontend changes are required** for the fix to work properly
3. **No database migration needed**
4. **Existing forms are not affected**

## Frontend Implementation Guide

### 1. Track Current Form ID

```javascript
class InterviewClient {
  constructor() {
    this.currentFormId = null;
    this.userId = null;
  }
  
  // When interview starts, save the formId
  handleInterviewState(state) {
    if (state.formId) {
      this.currentFormId = state.formId;
    }
  }
}
```

### 2. Send Form ID on Reconnect

```javascript
async startInterview() {
  const message = {
    type: "start_interview",
    userId: this.userId
  };
  
  // If we have a current form, include it to resume
  if (this.currentFormId) {
    message.formId = this.currentFormId;
  }
  
  this.websocket.send(JSON.stringify(message));
}
```

### 3. Handle Resume Message

```javascript
handleMessage(message) {
  if (message.type === "text_message") {
    // Check if it's a resume message
    if (message.text.includes("Welcome back")) {
      console.log("Resuming interview...");
    }
    // Display message to user
    this.displayMessage(message.text);
  }
}
```

## Summary

✅ **Backend now supports two modes:**
- NEW INTERVIEW: No `formId` → Create new form
- RESUME: `formId` provided → Load existing form

✅ **Security:** Verifies form belongs to current user

✅ **Graceful fallback:** If form not found, creates new one

⚠️ **Frontend changes required:** Must send `formId` on reconnect

🎯 **Result:** Users can now resume their interview after backend reload without losing progress!


