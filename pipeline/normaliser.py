import os
import re
import json
from loguru import logger
from anthropic import Anthropic
from quality_report import PipelineStats

class Normaliser:
    """Normalizes raw compensation strings (salaries, experience, locations) 
    using either an LLM or a rule-based fallback mechanism.
    """
    
    LOCATION_MAPPING = {
        "bangalore": "Bengaluru",
        "bengaluru": "Bengaluru",
        "gurgaon": "Gurugram",
        "gurugram": "Gurugram",
        "mumbai": "Mumbai",
        "hyderabad": "Hyderabad",
        "noida": "Noida",
        "pune": "Pune",
        "chennai": "Chennai",
        "delhi ncr": "Delhi NCR",
    }

    def __init__(self):
        # Initialize Anthropic client if key is set
        api_key = os.getenv("ANTHROPIC_API_KEY")
        self.no_llm = os.getenv("NO_LLM", "false").lower() == "true"
        
        if api_key and not self.no_llm:
            self.client = Anthropic(api_key=api_key)
            logger.info("Normaliser initialized with Anthropic LLM support.")
        else:
            self.client = None
            logger.info("Normaliser initialized in Rule-Based fallback mode.")

    def parse_salary_rule_based(self, salary_text: str) -> float | None:
        """Rule-based parsing of salary into numeric values (in Rupees, where 20 LPA = 2,000,000)."""
        if not salary_text:
            return None
        
        text = salary_text.strip().lower().replace(",", "")
        
        if "not disclosed" in text or "disclose" in text:
            return None
            
        large_num_match = re.search(r'(\d{6,})', text)
        if large_num_match:
            return float(large_num_match.group(1))

        def to_rupees(lpa_value: float) -> float:
            return lpa_value * 100000.0

        range_match = re.search(r'(\d+(?:\.\d+)?)\s*[l]?[a-z]*\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)\s*[l]?[a-z]*', text)
        if range_match:
            val1 = float(range_match.group(1))
            val2 = float(range_match.group(2))
            midpoint_lpa = (val1 + val2) / 2.0
            return to_rupees(midpoint_lpa)
            
        single_match = re.search(r'(\d+(?:\.\d+)?)', text)
        if single_match:
            val = float(single_match.group(1))
            if val < 500:
                return to_rupees(val)
            return val
            
        return None

    def parse_experience_rule_based(self, exp_text: str) -> int | None:
        """Rule-based parsing of experience strings into integer years."""
        if not exp_text:
            return None
            
        text = exp_text.strip().lower()
        
        if "fresher" in text:
            return 0

        # Match ranges
        range_match = re.search(r'(\d+)\s*(?:-|–|to)\s*(\d+)', text)
        if range_match:
            val1 = int(range_match.group(1))
            val2 = int(range_match.group(2))
            return int((val1 + val2) / 2)
            
        # Match single values
        single_match = re.search(r'(\d+)', text)
        if single_match:
            return int(single_match.group(1))
            
        return None

    def clean_location_rule_based(self, loc_text: str) -> str:
        """Rule-based standardization of location names."""
        if not loc_text:
            return "Remote"
        cleaned = loc_text.strip().lower()
        return self.LOCATION_MAPPING.get(cleaned, loc_text.strip())

    def process_rule_based(self, record: dict) -> dict:
        """Applies programmatic rules when LLM is unavailable or fails."""
        normalized = record.copy()
        
        normalized["base_salary"] = self.parse_salary_rule_based(record.get("raw_salary_text", ""))
        normalized["experience_years"] = self.parse_experience_rule_based(record.get("raw_experience", ""))
        normalized["location"] = self.clean_location_rule_based(record.get("raw_location", ""))
        
        # Rule-based fallback requirement: confidence score of exactly 0.7
        normalized["confidence_score"] = 0.7
        normalized["bonus"] = 0.0
        normalized["stock"] = 0.0
        normalized["currency"] = "INR"
        normalized["source"] = "AmbitionBox"
        normalized["is_verified"] = True
        
        return normalized

    def process_llm(self, record: dict) -> dict | None:
        """Applies LLM-based normalisation using Anthropic Claude."""
        if not self.client:
            return None

        raw_salary = record.get("raw_salary_text", "")
        raw_location = record.get("raw_location", "")
        raw_experience = record.get("raw_experience", "")

        prompt = f"""
You are a data normalisation assistant for a compensation intelligence platform.
Your task is to parse raw scraped salary records and normalise them into clean structured fields.

Raw Input:
raw_salary_text: {raw_salary}
raw_location: {raw_location}
raw_experience: {raw_experience}

Normalization Rules:
1. base_salary: Extract the annual salary in Indian Rupees (INR).
   - For ranges like "₹18–22 LPA" or "18 to 22 lakhs", calculate the midpoint (e.g. 20 LPA = 2000000 INR).
   - For "₹45,00,000 per annum", return 4500000.
   - If not disclosed or not present, return null.
2. experience_years: Extract the years of experience as an integer.
   - For ranges like "5–8 yrs", return the midpoint (e.g. 6).
   - For single values like "3+ years", return the number (e.g. 3).
   - For "Fresher", return 0.
   - If not present, return null.
3. location: Standardise the location name.
   - "Bangalore" or "Bengaluru" -> "Bengaluru"
   - "Gurgaon" or "Gurugram" -> "Gurugram"
   - If not present, return "Remote".
4. confidence_score: Assign a confidence score between 0.8 and 1.0 based on how clean and complete the input is.

Output format:
Return a raw JSON object only. Do NOT include any conversational text, no markdown block syntax.

Example Output:
{{
  "base_salary": 2000000,
  "experience_years": 6,
  "location": "Bengaluru",
  "confidence_score": 0.95
}}
"""
        try:
            response = self.client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=150,
                temperature=0.0,
                system="You are a data normalisation tool that outputs raw JSON only.",
                messages=[{"role": "user", "content": prompt}]
            )
            
            content = response.content[0].text.strip()
            if content.startswith("```json"):
                content = content.split("```json")[1].split("```")[0].strip()
            elif content.startswith("```"):
                content = content.split("```")[1].split("```")[0].strip()
                
            data = json.loads(content)
            
            normalized = record.copy()
            normalized["base_salary"] = data.get("base_salary")
            normalized["experience_years"] = int(data.get("experience_years")) if data.get("experience_years") is not None else None
            normalized["location"] = data.get("location", "Remote")
            
            confidence = float(data.get("confidence_score", 0.8))
            normalized["confidence_score"] = max(0.0, min(1.0, confidence))
            normalized["bonus"] = 0.0
            normalized["stock"] = 0.0
            normalized["currency"] = "INR"
            normalized["source"] = "AmbitionBox"
            normalized["is_verified"] = True
            
            return normalized

        except Exception as e:
            logger.error(f"LLM normalisation failed: {e}. Falling back to rule-based.")
            return None

    def process_batch_llm(self, chunk: list[dict]) -> list[dict] | None:
        """Sends a batch of raw records to Claude for normalisation in a single call."""
        if not self.client:
            return None

        # Extract only the fields necessary for the prompt to save tokens
        prompt_input = []
        for r in chunk:
            prompt_input.append({
                "raw_salary_text": r.get("raw_salary_text", ""),
                "raw_location": r.get("raw_location", ""),
                "raw_experience": r.get("raw_experience", "")
            })

        prompt = f"""
You are a data normalisation assistant for a compensation intelligence platform.
Your task is to parse a batch of raw scraped salary records and normalise them into clean structured fields.

Input Batch of Raw Records:
{json.dumps(prompt_input, indent=2)}

Normalization Rules for each record:
1. base_salary: Extract the annual salary in Indian Rupees (INR).
   - For ranges like "₹18–22 LPA" or "18 to 22 lakhs", calculate the midpoint (e.g. 20 LPA = 2000000 INR).
   - For "₹45,00,000 per annum", return 4500000.
   - If not disclosed or not present, return null.
2. experience_years: Extract the years of experience as an integer.
   - For ranges like "5–8 yrs", return the midpoint (e.g. 6).
   - For single values like "3+ years", return the number (e.g. 3).
   - For "Fresher", return 0.
   - If not present, return null.
3. location: Standardise the location name.
   - "Bangalore" or "Bengaluru" -> "Bengaluru"
   - "Gurgaon" or "Gurugram" -> "Gurugram"
   - If not present, return "Remote".
4. confidence_score: Assign a confidence score between 0.8 and 1.0 based on how clean and complete the input is.

Output format:
Return a raw JSON array containing normalized objects in the same order. Do NOT include any conversational text, no markdown block syntax.

Example Output:
[
  {{
    "base_salary": 2000000,
    "experience_years": 6,
    "location": "Bengaluru",
    "confidence_score": 0.95
  }},
  ...
]
"""
        try:
            response = self.client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=1000,
                temperature=0.0,
                system="You are a data normalisation tool that outputs raw JSON array of objects only.",
                messages=[{"role": "user", "content": prompt}]
            )
            
            content = response.content[0].text.strip()
            if content.startswith("```json"):
                content = content.split("```json")[1].split("```")[0].strip()
            elif content.startswith("```"):
                content = content.split("```")[1].split("```")[0].strip()
                
            data = json.loads(content)
            if isinstance(data, list):
                return data
            return None
        except Exception as e:
            logger.error(f"LLM batch normalisation failed: {e}")
            return None

    def process(self, record: dict) -> dict:
        """Normalises a record by trying LLM first, falling back to rule-based if needed."""
        if self.client:
            res = self.process_llm(record)
            if res is not None:
                return res
        return self.process_rule_based(record)

def normalize_batch(batch: list[dict], stats: PipelineStats) -> list[dict]:
    """Orchestrates batch normalization and updates stats counters.
    Groups raw records into batches of 10 and sends them to the LLM (if enabled),
    otherwise processes them using rule-based fallback.
    """
    normaliser = Normaliser()
    normalized = []
    
    # Check if LLM is enabled
    if normaliser.client and not normaliser.no_llm:
        # Group into batches of 10
        chunk_size = 10
        chunks = [batch[i:i + chunk_size] for i in range(0, len(batch), chunk_size)]
        
        for chunk in chunks:
            try:
                # Attempt to normalize the entire chunk via LLM
                chunk_results = normaliser.process_batch_llm(chunk)
                if chunk_results and len(chunk_results) == len(chunk):
                    for idx, norm_res in enumerate(chunk_results):
                        # Merge raw fields with LLM normalised fields
                        raw_record = chunk[idx]
                        merged = raw_record.copy()
                        merged["base_salary"] = norm_res.get("base_salary")
                        merged["experience_years"] = int(norm_res.get("experience_years")) if norm_res.get("experience_years") is not None else None
                        merged["location"] = norm_res.get("location", "Remote")
                        confidence = float(norm_res.get("confidence_score", 0.8))
                        merged["confidence_score"] = max(0.0, min(1.0, confidence))
                        merged["bonus"] = 0.0
                        merged["stock"] = 0.0
                        merged["currency"] = "INR"
                        merged["source"] = "AmbitionBox"
                        merged["is_verified"] = True
                        normalized.append(merged)
                        stats.passed_normalisation += 1
                else:
                    # If length mismatch or failure, fall back to rule-based for this chunk
                    logger.warning("LLM batch size mismatch or invalid response. Falling back to rule-based.")
                    for item in chunk:
                        norm = normaliser.process_rule_based(item)
                        normalized.append(norm)
                        stats.passed_normalisation += 1
            except Exception as e:
                logger.error(f"Failed to process batch via LLM: {e}. Falling back to rule-based.")
                for item in chunk:
                    norm = normaliser.process_rule_based(item)
                    normalized.append(norm)
                    stats.passed_normalisation += 1
    else:
        # Rule-based fallback only
        for item in batch:
            try:
                norm = normaliser.process_rule_based(item)
                normalized.append(norm)
                stats.passed_normalisation += 1
            except Exception as e:
                logger.error(f"Failed to normalize record rule-based: {e}")
                
    return normalized
