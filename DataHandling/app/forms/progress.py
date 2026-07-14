"""Form progress and section completion calculations — pure functions, no I/O."""


def calculate_form_progress(form_data: dict) -> float:
    if not form_data:
        return 0
    expected = ["Present Complaint","Previous Consultations","Pain Assessment",
                "History & Diagnostics","Treatment Goals","Referral"]
    total = filled = 0
    for sec in expected:
        if sec not in form_data or not isinstance(form_data[sec], dict):
            continue
        sd = form_data[sec]
        if sec == "Previous Consultations":
            pf = "Previous Diagnosis or Advice and Prescribed Treatment Taken"
            sf = "Current Status of Issue (Improved, Same, Worse)"
            pv = sd.get(pf, "")
            sv = sd.get(sf, "")
            no_c = ["no previous","didn't visit","did not visit","haven't consulted",
                    "have not consulted","no consultations","no doctor","no hospital",
                    "never consulted","not consulted","none","nothing"]
            if pv and str(pv).strip() and any(i in str(pv).lower() for i in no_c):
                total += 1; filled += 1
            else:
                total += 2
                if pv and str(pv).strip(): filled += 1
                if sv and str(sv).strip(): filled += 1
        else:
            for fn, fv in sd.items():
                total += 1
                if fv and str(fv).strip():
                    fl = str(fv).strip().lower()
                    if sec == "History & Diagnostics":
                        filled += 1
                    elif sec == "Referral" and fn == "Source":
                        if fl not in {"yes","no","none","n/a","na",""}:
                            filled += 1
                    else:
                        # Count as filled if the field has any substantive value,
                        # including descriptive "none" answers like "No specific goals mentioned".
                        # Only exclude bare single-word nulls that mean "not answered yet".
                        if fl not in {"none","no","nothing","n/a","na","","nil","tbd","unknown"}:
                            filled += 1
                        elif len(fl) > 6:
                            # Longer values like "No specific goals mentioned" count as filled
                            filled += 1
    return 0 if total == 0 else min(100, max(0, round(filled/total*100, 1)))


def calculate_section_completion_status(form_data: dict) -> dict:
    if not form_data:
        return {"totalSteps":0,"currentStep":0,"progress":0,"steps":[]}
    section_order = ["Present Complaint","Previous Consultations","Pain Assessment",
                     "History & Diagnostics","Treatment Goals","Referral"]
    done = []
    todo = []
    for sec in section_order:
        if sec not in form_data or not isinstance(form_data[sec], dict):
            continue
        sd = form_data[sec]
        filled = 0
        if sec == "Previous Consultations":
            pf = "Previous Diagnosis or Advice and Prescribed Treatment Taken"
            sf = "Current Status of Issue (Improved, Same, Worse)"
            pv = sd.get(pf, ""); sv = sd.get(sf, "")
            ck = ["doctor","physiotherapist","hospital","consulted","visited",
                  "diagnosis","prescribed","treatment","physio","clinic"]
            has_c = pv and any(k in str(pv).lower() for k in ck)
            if sv and not pv and not has_c:
                tf = 2; filled = 0
            elif pv and str(pv).strip():
                nc = ["no previous","didn't visit","did not visit","haven't consulted",
                      "have not consulted","no consultations","no doctor","no hospital",
                      "never consulted","not consulted","none","nothing"]
                if any(i in str(pv).lower() for i in nc):
                    tf = 1; filled = 1
                else:
                    tf = 2
                    if pv: filled += 1
                    if sv: filled += 1
            else:
                tf = 2; filled = 0
        else:
            tf = len(sd)
            if tf == 0:
                todo.append({"name":sec,"isComplete":False,"completionPercentage":0,"filledFields":0,"totalFields":0})
                continue
            for _, fv in sd.items():
                if fv and str(fv).strip():
                    fl = str(fv).strip().lower()
                    if fl not in {"n/a","na","nil","tbd","unknown"}:
                        filled += 1
        pct = round(filled/tf*100, 1) if tf > 0 else 0
        is_c = filled == tf and tf > 0
        info = {"name":sec,"isComplete":is_c,"completionPercentage":pct,"filledFields":filled,"totalFields":tf}
        (done if is_c else todo).append(info)
    all_s = sorted(done+todo, key=lambda s: section_order.index(s["name"]) if s["name"] in section_order else 99)
    n = len(all_s); d = len(done)
    return {"totalSteps":n,"currentStep":d+1 if todo else n,"completedSteps":d,
            "progress":round(d/n*100,1) if n else 0,"steps":all_s}
