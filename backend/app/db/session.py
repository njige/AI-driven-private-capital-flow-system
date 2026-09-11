from typing import List, Dict, Any

# In-memory storage session for filings
submissions_db: List[Dict[str, Any]] = []

def get_all_submissions() -> List[Dict[str, Any]]:
    """Retrieve all statutory filings."""
    return submissions_db

def add_submission(record: Dict[str, Any]) -> Dict[str, Any]:
    """Appends a newly processed/validated filing to the database store."""
    submissions_db.insert(0, record)  # Insert at the beginning so latest items show first
    return record

def clear_submissions() -> None:
    """Wipes in-memory filings for testing."""
    global submissions_db
    submissions_db.clear()