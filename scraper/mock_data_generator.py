import json
import os
import random

# Realism list inputs as per requirements
COMPANIES = [
    "Google India Pvt. Ltd.", "GOOGLE", "Tata Consultancy Services", 
    "TCS Ltd.", "amazon.com", "Amazon Web Services", "Infosys BPO", 
    "Wipro Technologies", "Flipkart Internet Pvt Ltd", "Microsoft India", 
    "Meta Platforms", "Goldman Sachs India"
]

SALARIES_FORMATS = [
    "₹{low}–{high} LPA", 
    "{low} to {high} lakhs", 
    "₹{exact},00,000 per annum", 
    "{exact} LPA", 
    "₹{low}L - ₹{high}L", 
    "{low}-{high} LPA CTC"
]

EXPERIENCES = [
    "5–8 yrs", "3+ years", "2 to 4 years", "7 Years", "1-2 yr"
]

LOCATIONS = [
    "Bengaluru", "Bangalore", "Mumbai", "Hyderabad", "Gurugram",
    "Gurgaon", "Chennai", "Pune", "Delhi NCR", "Noida"
]

ROLES = [
    "Software Engineer", "Senior Software Engineer", "SDE II", 
    "Staff Engineer", "Data Analyst", "Senior Data Analyst", "Data Scientist",
    "Product Manager", "Engineering Manager", "Principal Engineer"
]

def generate_random_record():
    low = random.randint(12, 45)
    high = low + random.randint(2, 6)
    exact = random.randint(15, 55)
    
    fmt = random.choice(SALARIES_FORMATS)
    salary_text = fmt.format(low=low, high=high, exact=exact)
    
    return {
        "raw_company": random.choice(COMPANIES),
        "raw_role": random.choice(ROLES),
        "raw_salary_text": salary_text,
        "raw_location": random.choice(LOCATIONS),
        "raw_experience": random.choice(EXPERIENCES)
    }

def main():
    records = []
    
    # 1. Base record for duplication
    duplicate_base = {
        "raw_company": "Meta Platforms",
        "raw_role": "Engineering Manager",
        "raw_salary_text": "25-30 LPA CTC",
        "raw_location": "Bengaluru",
        "raw_experience": "7 Years"
    }
    
    # Generate 74 random realistic records
    for _ in range(74):
        records.append(generate_random_record())
        
    # Inject base record
    records.insert(10, duplicate_base.copy())
    
    # 2. Inject duplicate of the base record (same company, role, salary)
    records.append(duplicate_base.copy())
    
    # 3. Inject empty company name failure
    records.append({
        "raw_company": "",
        "raw_role": "Software Engineer",
        "raw_salary_text": "₹18–22 LPA",
        "raw_location": "Noida",
        "raw_experience": "5–8 yrs"
    })
    
    # 4. Inject salary "Not disclosed" failure
    records.append({
        "raw_company": "Google India Pvt. Ltd.",
        "raw_role": "Data Analyst",
        "raw_salary_text": "Not disclosed",
        "raw_location": "Mumbai",
        "raw_experience": "3+ years"
    })
    
    # 5. Inject experience "Fresher" (no number) failure
    records.append({
        "raw_company": "Microsoft India",
        "raw_role": "Software Engineer",
        "raw_salary_text": "20 LPA",
        "raw_location": "Hyderabad",
        "raw_experience": "Fresher"
    })
    
    # 6. Inject role only, no other data failure
    records.append({
        "raw_company": "",
        "raw_role": "Data Scientist",
        "raw_salary_text": "",
        "raw_location": "",
        "raw_experience": ""
    })
    
    # Ensure target output directory exists
    output_dir = "data"
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "raw_records.json")
    
    # Write to target JSON
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=4, ensure_ascii=False)
        
    print(f"Generated {len(records)} records")

if __name__ == "__main__":
    main()
