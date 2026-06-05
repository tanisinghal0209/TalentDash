import sys
from pathlib import Path

# Add pipeline directory to sys.path to resolve imports cleanly
sys.path.insert(0, str(Path(__file__).resolve().parent))

from validator import validate_record, TalentDashLevel, SalaryRecord

def run_tests():
    print("Running Pydantic validation tests...\n")

    # Test Case 1: Valid record (including total_compensation calculation)
    valid_data = {
        "company": "google",
        "company_slug": "google",
        "role": "Software Engineer",
        "level_standardized": TalentDashLevel.L5,
        "location": "Bengaluru",
        "currency": "INR",
        "experience_years": 5,
        "base_salary": 2500000.0,
        "bonus": 500000.0,
        "stock": 500000.0,
        "source": "AmbitionBox",
        "confidence_score": 0.85,
        "is_verified": True
    }
    
    rec, err = validate_record(valid_data)
    if err:
        print(f"[FAIL] Valid record failed validation: {err}")
        return False
    else:
        assert rec.company == "google"
        assert rec.total_compensation == 3500000.0  # base + bonus + stock
        print(f"[PASS] Valid record passed. Computed total_compensation: {rec.total_compensation} INR")

    # 4 Should-Fail Test Cases:
    
    # 1. Company too short (< 2 chars after normalisation)
    fail_company = valid_data.copy()
    fail_company["company"] = "g"
    rec, err = validate_record(fail_company)
    if rec is None and err:
        print("[PASS] Test Case 1 (Company < 2 chars) failed as expected.")
    else:
        print("[FAIL] Test Case 1 (Company < 2 chars) passed validation but should have failed.")
        return False

    # 2. Experience out of bounds (>= 51 or <= 0)
    fail_exp = valid_data.copy()
    fail_exp["experience_years"] = 52
    rec, err = validate_record(fail_exp)
    if rec is None and err:
        print("[PASS] Test Case 2 (Experience >= 51) failed as expected.")
    else:
        print("[FAIL] Test Case 2 (Experience >= 51) passed validation but should have failed.")
        return False

    # 3. Base salary <= 0
    fail_salary = valid_data.copy()
    fail_salary["base_salary"] = 0.0
    rec, err = validate_record(fail_salary)
    if rec is None and err:
        print("[PASS] Test Case 3 (Base salary <= 0) failed as expected.")
    else:
        print("[FAIL] Test Case 3 (Base salary <= 0) passed validation but should have failed.")
        return False

    # 4. Confidence score out of bounds
    fail_confidence = valid_data.copy()
    fail_confidence["confidence_score"] = 1.1
    rec, err = validate_record(fail_confidence)
    if rec is None and err:
        print("[PASS] Test Case 4 (Confidence score > 1.0) failed as expected.")
    else:
        print("[FAIL] Test Case 4 (Confidence score > 1.0) passed validation but should have failed.")
        return False

    print("\nAll validation checks completed successfully!")
    return True

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
