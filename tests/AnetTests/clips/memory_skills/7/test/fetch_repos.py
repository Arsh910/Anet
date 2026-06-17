#!/usr/bin/env python3
"""
Fetch top 5 Python repositories from GitHub and save a report.json
with name, stars, and README snippet for each.
"""

import requests
import json
import base64
import sys
from typing import List, Dict, Any

# Top 5 Python repos (from research)
REPOS = [
    "public-apis/public-apis",
    "EbookFoundation/free-programming-books",
    "jackfrued/Python-100-Days",
    "TheAlgorithms/Python",
    "scikit-learn/scikit-learn"
]

GITHUB_API_URL = "https://api.github.com"
README_SNIPPET_SIZE = 500

def fetch_repo_stars(owner: str, repo: str) -> int:
    """Fetch the number of stars for a repository."""
    url = f"{GITHUB_API_URL}/repos/{owner}/{repo}"
    headers = {"Accept": "application/vnd.github.v3+json"}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get("stargazers_count", 0)
    except Exception as e:
        print(f"Error fetching stars for {owner}/{repo}: {e}")
        return 0

def fetch_readme_snippet(owner: str, repo: str) -> str:
    """Fetch the first 500 characters of a repository's README."""
    url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/readme"
    headers = {"Accept": "application/vnd.github.v3.raw"}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        content = response.text
        return content[:README_SNIPPET_SIZE]
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            print(f"README not found for {owner}/{repo}")
        else:
            print(f"Error fetching README for {owner}/{repo}: {e}")
        return ""
    except Exception as e:
        print(f"Error fetching README for {owner}/{repo}: {e}")
        return ""

def generate_report() -> List[Dict[str, Any]]:
    """Generate report with top 5 Python repos."""
    report = []
    
    for repo in REPOS:
        owner, name = repo.split("/")
        print(f"Fetching data for {repo}...")
        
        stars = fetch_repo_stars(owner, name)
        readme_snippet = fetch_readme_snippet(owner, name)
        
        entry = {
            "name": repo,
            "stars": stars,
            "readme_snippet": readme_snippet
        }
        report.append(entry)
        print(f"  - Stars: {stars}")
        print(f"  - README snippet: {len(readme_snippet)} chars")
    
    return report

def main():
    """Main entry point."""
    print("Fetching top 5 Python repositories from GitHub...")
    
    report = generate_report()
    
    output_file = "report.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    print(f"\nReport saved to {output_file}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
