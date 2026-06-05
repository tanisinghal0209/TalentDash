import json
import re
import os
from pathlib import Path
from loguru import logger

class CompanyNormaliser:
    """Normalizes company names using programmatic cleaning and alias lookup tables."""
    
    def __init__(self, aliases_path: str = "data/aliases.json"):
        self.aliases_path = Path(aliases_path)
        self.aliases = self.load_aliases()

    def load_aliases(self) -> dict:
        """Loads company alias mapping from JSON file."""
        if self.aliases_path.exists():
            try:
                with open(self.aliases_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return {k.lower(): [v.lower() for v in val_list] for k, val_list in data.items()}
            except Exception as e:
                logger.error(f"Failed to load aliases from {self.aliases_path}: {e}")
        else:
            logger.warning(f"Aliases file not found at {self.aliases_path}. Proceeding with programmatic rules only.")
        return {}

    def clean_programmatically(self, name: str) -> str:
        """Applies programmatic rules to clean the raw company name:
        
        - Lowercase
        - Strip leading/trailing whitespaces and reduce multiple internal spaces
        - Remove common legal suffixes and domain endings (pvt ltd, inc, ltd, llc, .com, etc.)
        - Strip trailing/leading punctuation symbols
        """
        if not name:
            return ""

        cleaned = name.lower()
        cleaned = " ".join(cleaned.split())
        cleaned = re.sub(r'\.(com|in|org|net|co)$', '', cleaned)
        
        legal_pattern = r'\b(pvt\.?\s*ltd\.?|ltd\.?|inc\.?|llc\.?|corp\.?|co\.?|corporation|pvt\.?)\b\.?$'
        cleaned = re.sub(legal_pattern, '', cleaned).strip()
        cleaned = re.sub(r'^[,\-\.\s]+|[,\-\.\s]+$', '', cleaned)

        return cleaned

    def normalise(self, company_name: str) -> str:
        """Normalises a raw company name to its canonical form using cleaning rules and alias mapping."""
        if not company_name:
            return ""
        
        cleaned = self.clean_programmatically(company_name)
        if not cleaned:
            return ""

        if cleaned in self.aliases:
            return cleaned

        for canonical, alias_list in self.aliases.items():
            if cleaned in alias_list:
                return canonical

        return cleaned

# ---------------------------------------------------------------------------
# Module level helper functions
# ---------------------------------------------------------------------------

_normaliser_instance = None

def get_normaliser_instance() -> CompanyNormaliser:
    """Returns a single cached instance of CompanyNormaliser."""
    global _normaliser_instance
    if _normaliser_instance is None:
        # Resolve target data directory path
        path = "data/aliases.json"
        if not os.path.exists(path):
            path = os.path.join(os.path.dirname(__file__), "..", "data", "aliases.json")
        _normaliser_instance = CompanyNormaliser(aliases_path=path)
    return _normaliser_instance

def slugify(text: str) -> str:
    """Converts a company name to a URL-friendly slug."""
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    text = re.sub(r'-+', '-', text).strip('-')
    return text

def normalize_company(name: str) -> tuple[str, str]:
    """Normalises a company name and returns a tuple of (canonical_name, company_slug)."""
    cn = get_normaliser_instance()
    canonical = cn.normalise(name)
    if not canonical:
        return "", ""
    slug = slugify(canonical)
    return canonical, slug
