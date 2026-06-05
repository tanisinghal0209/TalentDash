import json
import os
from enum import Enum
from pydantic import BaseModel, Field, field_validator, ValidationError, computed_field
from quality_report import PipelineStats

class TalentDashLevel(str, Enum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    L5 = "L5"
    SDE_I = "SDE_I"
    SDE_II = "SDE_II"
    Staff = "Staff"
    Principal = "Principal"
    Senior = "Senior"
    Mid = "Mid"
    Junior = "Junior"
    Ambiguous = "L4/L5"

class SalaryRecord(BaseModel):
    company: str
    company_slug: str
    role: str
    level_standardized: TalentDashLevel
    location: str
    currency: str
    experience_years: int
    base_salary: float
    bonus: float = 0.0
    stock: float = 0.0
    source: str
    confidence_score: float
    is_verified: bool = True

    @computed_field
    @property
    def total_compensation(self) -> float:
        return self.base_salary + self.bonus + self.stock

    @field_validator('company')
    @classmethod
    def validate_company(cls, v: str) -> str:
        if len(v.strip()) < 2:
            raise ValueError("Company must be at least 2 characters after normalisation")
        return v.strip()

    @field_validator('experience_years')
    @classmethod
    def validate_experience(cls, v: int) -> int:
        if v <= 0 or v >= 51:
            raise ValueError("Experience years must be positive and less than 51")
        return v

    @field_validator('base_salary')
    @classmethod
    def validate_base_salary(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Base salary must be greater than 0")
        return v

    @field_validator('confidence_score')
    @classmethod
    def validate_confidence_score(cls, v: float) -> float:
        if v < 0.0 or v > 1.0:
            raise ValueError("Confidence score must be in the range 0.0 to 1.0")
        return v

def validate_record(raw_dict: dict) -> tuple[SalaryRecord | None, ValidationError | None]:
    """Validates a single record against the SalaryRecord Pydantic model."""
    try:
        record = SalaryRecord(**raw_dict)
        return record, None
    except ValidationError as e:
        return None, e

def run_validation_pipeline(batch: list[dict], stats: PipelineStats) -> list[SalaryRecord]:
    """Validates a batch of normalized records, logs errors, and updates stats."""
    validated = []
    rejections = []
    rejections_path = "data/rejections.jsonl"

    for idx, item in enumerate(batch):
        rec, err = validate_record(item)
        if rec:
            validated.append(rec)
            stats.passed_pydantic += 1
            # Add to stats processed list (include computed field total_compensation)
            record_dict = rec.model_dump()
            record_dict["total_compensation"] = rec.base_salary + rec.bonus + rec.stock
            stats.processed_records.append(record_dict)
        else:
            stats.rejected_total += 1
            reason = str(err)
            
            # Map index to get corresponding raw scraped record
            raw_input = stats.raw_records[idx] if idx < len(stats.raw_records) else item
            rejections.append({
                "raw_input": raw_input,
                "rejection_reason": reason
            })
            
            # Classify Pydantic ValidationError errors
            errors_list = err.errors()
            has_level_err = False
            has_salary_err = False
            has_missing_err = False
            has_other_err = False
            
            for error in errors_list:
                loc_field = error["loc"][0] if error["loc"] else ""
                error_type = error["type"]
                
                if loc_field == "level_standardized":
                    has_level_err = True
                elif loc_field == "base_salary":
                    has_salary_err = True
                elif error_type in ["missing", "value_error.missing"]:
                    has_missing_err = True
                elif loc_field in ["company", "role"]:
                    # Length check fail or empty values treated as missing/invalid
                    has_missing_err = True
                else:
                    has_other_err = True
                    
            if has_level_err:
                stats.rejected_level += 1
            elif has_salary_err:
                stats.rejected_salary += 1
            elif has_missing_err:
                stats.rejected_missing += 1
            else:
                stats.rejected_other += 1

    # Save to rejections.jsonl
    if rejections:
        os.makedirs(os.path.dirname(rejections_path), exist_ok=True)
        with open(rejections_path, "a", encoding="utf-8") as f:
            for r in rejections:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                
    return validated
