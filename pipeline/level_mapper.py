import os
import json
import re
from loguru import logger
from anthropic import Anthropic

class LevelMapper:
    """Maps raw job title and experience details to standard TalentDash levels."""
    
    # Layer 1: Rule-based exact mapping dictionary
    RULES = {
        "software engineer iii": "L5",
        "senior software engineer": "L4/L5",  # ambiguous
        "staff engineer": "Staff",
        "sde-ii": "SDE_II",
        "sde ii": "SDE_II",
        "sde 2": "SDE_II"
    }

    def __init__(self):
        # Initialize Anthropic client if key is present
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            self.client = Anthropic(api_key=api_key)
        else:
            self.client = None
            logger.warning("ANTHROPIC_API_KEY not found. LLM fallback will return default confidence 0.4.")

    def map_level(self, title: str, experience_years: float | None) -> tuple[str, float]:
        """Maps a job title and experience to a level and confidence score.
        
        Returns:
            (level, confidence_score)
        """
        if not title:
            return "Junior", 0.4

        cleaned_title = title.strip().lower()

        # 1. Rule-based exact match
        if cleaned_title in self.RULES:
            level = self.RULES[cleaned_title]
            if level == "L4/L5":
                # Ambiguous, needs LLM fallback (Layer 2)
                logger.info(f"Rule-based matched ambiguous level for '{title}'. Falling back to LLM.")
                return self.llm_fallback(title, experience_years)
            else:
                logger.info(f"Rule-based matched level '{level}' for '{title}'")
                return level, 0.85

        # Heuristic checks before LLM fallback to handle variations of exact rules
        if cleaned_title == "sde-2" or cleaned_title == "sde2":
            return "SDE_II", 0.85
        if cleaned_title == "software engineer 3" or cleaned_title == "sde iii":
            return "L5", 0.85

        # 2. LLM Fallback (Layer 2)
        return self.llm_fallback(title, experience_years)

    def llm_fallback(self, title: str, experience_years: float | None) -> tuple[str, float]:
        """Layer 2 - LLM fallback for ambiguous or unmapped job titles."""
        if not self.client:
            # No client: return default fallback based on experience with 0.4 confidence
            return self.heuristic_fallback(title, experience_years)

        prompt = f"""
You are a job level mapping assistant for a compensation intelligence platform.
Your task is to classify a candidate's level based on their raw job title and years of experience.

Allowed levels:
- Junior
- Mid
- Senior
- L1
- L2
- L3
- L4
- L5
- SDE_I
- SDE_II
- Staff
- Principal
- L4/L5

Input:
Raw Title: {title}
Experience Years: {experience_years}

Instructions:
1. Classify the candidate into exactly one of the allowed levels listed above.
2. If the mapping is highly ambiguous, output 'L4/L5'.
3. Output the result in JSON format with keys "level" and "confidence", where confidence is a float between 0.6 and 0.8.

Example Output:
{{
  "level": "L5",
  "confidence": 0.75
}}
"""
        try:
            response = self.client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=100,
                temperature=0.0,
                system="You map job titles and experience to standardized levels. Output raw JSON only.",
                messages=[{"role": "user", "content": prompt}]
            )
            
            content = response.content[0].text.strip()
            # Clean JSON markers if returned
            if content.startswith("```json"):
                content = content.split("```json")[1].split("```")[0].strip()
            elif content.startswith("```"):
                content = content.split("```")[1].split("```")[0].strip()
                
            data = json.loads(content)
            level = data.get("level")
            confidence = float(data.get("confidence", 0.7))
            
            # Verify level is in allowed values
            valid_levels = ["Junior", "Mid", "Senior", "L1", "L2", "L3", "L4", "L5", "SDE_I", "SDE_II", "Staff", "Principal", "L4/L5"]
            if level in valid_levels:
                confidence = max(0.6, min(0.8, confidence))
                return level, confidence
                
        except Exception as e:
            logger.error(f"LLM fallback failed: {e}")

        # Fallback if API call fails
        return self.heuristic_fallback(title, experience_years)

    def heuristic_fallback(self, title: str, experience_years: float | None) -> tuple[str, float]:
        """Heuristic classification with confidence 0.4 (unmatched review)."""
        logger.info(f"Using heuristic fallback for '{title}'")
        if experience_years is None:
            return "Mid", 0.4
            
        if experience_years < 2.0:
            return "Junior", 0.4
        elif experience_years < 6.0:
            return "Mid", 0.4
        else:
            return "Senior", 0.4
