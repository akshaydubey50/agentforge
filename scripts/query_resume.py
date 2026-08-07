#!/usr/bin/env python3
"""Query Akshay Dubey's work experience from resumes"""

import requests
import sys

sys.path.insert(0, "src")

# Query text
query = """
Provide complete work experience details for Akshay Dubey.
Include job titles, companies, dates, responsibilities, technologies used, and key achievements.
Include information from both AI Fullstack Engineer resume and AI Engineer resume.
"""

# Make API request
try:
    response = requests.post(
        'http://localhost:8000/v1/ask',
        json={
            'question': query,
            'strategy': 'structure_aware',
            'top_k': 5,
            'use_reranker': True,
            'sparse_weight': 1.0
        },
        timeout=60
    )
    response.raise_for_status()
    data = response.json()

    print("\n" + "="*80)
    print("AKSHAY DUBEY - COMPLETE WORK EXPERIENCE")
    print("="*80)
    print()

    print("ANSWER FROM RESUMES:")
    print("-"*80)
    print(data['answer'])
    print()

    print("="*80)
    print("CONFIDENCE SCORES:")
    print("="*80)
    conf = data['confidence']
    print(f"  Overall Confidence: {conf['overall']:.2f}")
    print(f"  Retrieval Confidence: {conf['retrieval_confidence']:.2f}")
    print(f"  Citation Coverage: {conf['citation_coverage']:.2f}")
    print(f"  Completeness: {conf['completeness']:.2f}")
    print()

    print("="*80)
    print("RETRIEVED SOURCES:")
    print("="*80)
    for i, source in enumerate(data['sources'], 1):
        status = "CITED" if source['cited'] else "NOT CITED"
        print(f"\n[{i}] {source['title']} ({source['filename']}) - {status}")
        print(f"    Score: {source['score']:.4f}")
        print("-"*80)
        text = source['text']
        display_text = text[:600] + "...[truncated]" if len(text) > 600 else text
        print(display_text)

    print("\n" + "="*80)

except requests.exceptions.ConnectionError:
    print("ERROR: Cannot connect to API at http://localhost:8000")
    print("Make sure to start the API with:")
    print("  uvicorn src.rag.main:app --host 0.0.0.0 --port 8000")
    sys.exit(1)
except Exception as e:
    print(f"ERROR: {e}")
    sys.exit(1)
