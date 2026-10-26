# FRM-01: Clinical Intake Interview

Standard patient intake form for all Stance Health MSK clinic visits.

---

## Sections

### Present Complaint
- Primary Complaint
- Duration of the Issue
- Onset (Gradual or Sudden)
- Mechanism of Injury or Cause

### Previous Consultations
- Previous Diagnosis or Advice and Prescribed Treatment Taken
- Current Status of Issue (Improved, Same, Worse)

### Pain Assessment
- Primary Location of Pain
- Severity (1-10)
- Aggravating Factors
- Relieving Factors

### History & Diagnostics
- Systemic Illness and Surgical History
- Current Lifestyle
- Reports

### Treatment Goals
- Short-Term Goals (within 3 months)
- Long-Term Goals (after 3 months)
- Specific Expectations from Treatment

---

## Visit Types

### specific_complaint
skip:
description: SPECIFIC COMPLAINT — patient has a specific pain, injury, or condition to address. Collect: complaint details, pain severity/location, duration, onset, aggravating/relieving factors, previous consultations, health history, diagnostics, treatment goals.

### general_assessment
skip: Pain Assessment, Previous Consultations, Present Complaint
description: GENERAL ASSESSMENT / WELLNESS VISIT — patient has NO specific complaint. They want a check-up, posture review, or to explore the clinic. SKIP all questions about specific pain, injury mechanism, and previous treatment for a complaint. Collect ONLY: general health conditions, lifestyle, treatment/wellness goals.

### clinic_inquiry
skip:
description: CLINIC INQUIRY — patient was asking about the clinic. They may or may not have a complaint. Collect whatever health context they're willing to share: any conditions, lifestyle, goals.

### unknown
skip:
description: VISIT TYPE UNKNOWN — gather what you can. Start with what brings them in today, then collect health context naturally.

---

## Field Labels

Primary Complaint: What's bothering them (pain/issue, location, severity 0–10)
Duration of the Issue: How long they've had this
Onset (Gradual or Sudden): Whether it started suddenly or gradually
Mechanism of Injury or Cause: What caused it / how it started
Previous Diagnosis or Advice and Prescribed Treatment Taken: Previous doctor/physio visits and what they said
Current Status of Issue (Improved, Same, Worse): How the issue is now, compared with before the previous treatment (improved, the same, or worse)
Primary Location of Pain: Exactly where the pain is
Severity (1-10): Pain severity on a scale of 0–10
Aggravating Factors: What makes it worse
Relieving Factors: What gives relief
Systemic Illness and Surgical History: Other health conditions, past surgeries or fractures
Current Lifestyle: Work or usual occupation, and activity or exercise routine
Reports: Any MRI, X-ray, CT scan, or blood reports
Short-Term Goals (within 3 months): What they want to achieve in the next 3 months
Long-Term Goals (after 3 months): Their long-term health/activity goal
Specific Expectations from Treatment: What they expect from this treatment

---

## Field Rules

### Onset (Gradual or Sudden)
values: Gradual, Sudden

### Current Status of Issue (Improved, Same, Worse)
values: Improved, Same, Worse
applies_when_answered: Previous Diagnosis or Advice and Prescribed Treatment Taken

### Severity (1-10)
scale: 0-10

---

## Predefined Questions

### initial_comprehensive
title: Initial Comprehensive Interview

To help me understand your situation, I'd like to ask you a few questions. Please share as much detail as you can:

- What exactly is bothering you right now (pain, stiffness, weakness, swelling, etc.), where do you feel it, and how severe is it on a scale of 0 to 10?
- How long have you been experiencing this, and how did it start (suddenly after an injury or gradually)?
- What makes it worse or better (any movement, position, time of day, rest, etc.)?
- Have you consulted any doctor, physiotherapist, or hospital for this before? If yes, what did they say and what treatment was prescribed?
- Do you have any other health conditions or past surgeries? What is your work or usual occupation, what is your typical activity or exercise routine, and do you have any MRI, X-ray, CT scan, or blood reports related to this issue?
- What are your goals with treatment - what would you like to be able to do in the next 3 months and long-term?

### past_treatment
title: Past Treatment & History/Diagnostics

Have you consulted any doctor, physiotherapist, or hospital for this same problem before?
If yes:

- What did they diagnose or say was the issue?
- What treatment, medicines, injections, or exercises were prescribed?
- Did it help at all, and what is the current status of your issue (improved, same, or worse)?

Now about your overall health, lifestyle, and any reports:

- Do you have any other health conditions like diabetes, high blood pressure, thyroid, heart issues, or any past surgeries or fractures?
- What is your work or usual occupation?
- What is your typical activity or exercise routine?
- Do you have any MRI, X-ray, CT scan, or blood reports related to this issue?

### goals
title: Treatment Goals

What are your goals with treatment?

- In the next 3 months, what would you like to be able to do?
- Long-term, what is your ultimate goal (e.g., walk without pain, play sports, climb stairs easily, etc.)?
- What are your specific expectations from this treatment?

---

## General Visit First Question

Welcome! Happy to learn more about you so we can make the most of your visit.

- Do you have any existing health conditions (e.g. diabetes, BP, thyroid) or past surgeries/fractures?
- What's your work or usual occupation, and what is your typical activity or exercise routine?
- What are you hoping to get out of your visit today — any wellness goals, posture concerns, or things you'd like to explore?
