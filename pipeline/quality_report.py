import json
from dataclasses import dataclass, field

@dataclass
class PipelineStats:
    total_scraped: int = 0
    passed_normalisation: int = 0
    passed_pydantic: int = 0
    rejected_total: int = 0
    rejected_level: int = 0
    rejected_salary: int = 0
    rejected_missing: int = 0
    rejected_other: int = 0
    stored_success: int = 0
    duplicates_skipped: int = 0
    processed_records: list[dict] = field(default_factory=list)
    raw_records: list[dict] = field(default_factory=list)  # Aligning raw indices for sample display

def print_quality_report(stats: PipelineStats):
    """Prints the TalentDash quality report in the exact format required."""
    
    # Calculate null rates for specific fields in validated records
    total_stored = len(stats.processed_records)
    
    def calculate_null_rate(field_name: str) -> str:
        if total_stored == 0:
            return "0%"
        null_count = 0
        for r in stats.processed_records:
            val = r.get(field_name)
            # Count null, empty string, or error placeholders
            if val is None or val == "" or val == -1.0 or val == -1:
                null_count += 1
        return f"{(null_count / total_stored) * 100:.0f}%"

    null_bonus = calculate_null_rate("bonus")
    null_stock = calculate_null_rate("stock")
    null_location = calculate_null_rate("location")

    print("============================")
    print("TALENTDASH PIPELINE REPORT")
    print("============================")
    print(f"Total records scraped:          {stats.total_scraped}")
    print(f"Records after LLM normalisation: {stats.passed_normalisation}")
    print(f"Records passed Pydantic:        {stats.passed_pydantic}")
    print(f"Records rejected (total):       {stats.rejected_total}")
    print(f"  - Invalid level:              {stats.rejected_level}")
    print(f"  - Negative/zero salary:       {stats.rejected_salary}")
    print(f"  - Missing required field:     {stats.rejected_missing}")
    print(f"  - Other:                      {stats.rejected_other}")
    print(f"Records stored successfully:    {stats.stored_success}")
    print(f"Duplicates skipped:             {stats.duplicates_skipped}")
    print("")
    print("NULL RATE PER FIELD:")
    print(f"  bonus:      {null_bonus}")
    print(f"  stock:      {null_stock}")
    print(f"  location:   {null_location}")
    print("")
    print("SAMPLE (first 3 records):")
    
    # Display the first 3 raw vs normalised records side-by-side
    sample_size = min(3, len(stats.processed_records), len(stats.raw_records))
    for i in range(sample_size):
        raw = stats.raw_records[i]
        norm = stats.processed_records[i]
        
        print(f"Record #{i+1}:")
        print("  [RAW]")
        print(f"    Company:    {raw.get('raw_company')}")
        print(f"    Role:       {raw.get('raw_role')}")
        print(f"    Salary:     {raw.get('raw_salary_text')}")
        print(f"    Location:   {raw.get('raw_location')}")
        print(f"    Experience: {raw.get('raw_experience')}")
        print("  [NORMALISED]")
        print(f"    Company:    {norm.get('company')} (Slug: {norm.get('company_slug')})")
        print(f"    Role:       {norm.get('role')}")
        print(f"    Level:      {norm.get('level_standardized')}")
        print(f"    Location:   {norm.get('location')}")
        print(f"    Salary:     {norm.get('base_salary')} (Bonus: {norm.get('bonus')}, Stock: {norm.get('stock')})")
        print(f"    Total Comp: {norm.get('total_compensation')} (Score: {norm.get('confidence_score')})")
        print("-" * 30)
    print("============================")
