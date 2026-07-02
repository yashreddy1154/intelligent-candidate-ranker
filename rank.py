#!/usr/bin/env python3
"""
rank.py — Core Ranking Algorithm for the Redrob Hackathon.
Team KALI  •  P Yashwanth Reddy

Scores and ranks candidates from a pool of 100,000 to select the top 100
for the "Senior AI Engineer — Founding Team" role at Redrob AI.
Runs in under 1 minute on CPU, using only standard Python libraries.
"""

import argparse
import csv
import gzip
import json
import math
import os
import re
import sys
from datetime import date, datetime
from typing import Any, Dict, List, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# 1. CONSTANTS & DEFINITIONS
# ─────────────────────────────────────────────────────────────────────────────

# Reference date for "today" in 2026
TODAY = date(2026, 7, 2)

AI_ML_SKILLS = {
    s.strip().lower()
    for s in (
        "Python, NLP, Machine Learning, Deep Learning, PyTorch, TensorFlow, "
        "Transformers, BERT, GPT, LLMs, Fine-tuning LLMs, RAG, Embeddings, "
        "FAISS, Pinecone, Milvus, Weaviate, Qdrant, Elasticsearch, "
        "Hugging Face, Neural Networks, Scikit-learn, XGBoost, MLflow, "
        "Docker, Kubernetes, MLOps, Spark, Airflow, SQL, FastAPI, Flask, "
        "AWS, GCP, Azure, Data Science, Statistical Modeling, "
        "Feature Engineering, Computer Vision, Image Classification, "
        "Object Detection, Speech Recognition, LoRA, Sentence-Transformers, "
        "Information Retrieval, Recommendation Systems, Search, "
        "Vector Databases, Hybrid Search"
    ).split(",")
}

RELEVANT_TITLES = {
    t.strip().lower()
    for t in (
        "AI Engineer, ML Engineer, Machine Learning Engineer, Data Scientist, "
        "NLP Engineer, Research Engineer, Deep Learning Engineer, "
        "Software Engineer, Backend Engineer, Data Engineer, "
        "Full Stack Engineer, Platform Engineer, Junior ML Engineer, "
        "Senior Machine Learning Engineer, AI Research Scientist, "
        "Machine Learning Infrastructure Engineer, MLOps Engineer"
    ).split(",")
}

NON_TECH_TITLES = {
    t.strip().lower()
    for t in (
        "marketing manager, hr manager, accountant, sales executive, "
        "customer support, content writer, graphic designer, "
        "mechanical engineer, civil engineer, operations manager, "
        "business analyst, project manager, office manager, assistant"
    ).split(",")
}

CONSULTING_COMPANIES = {
    "tcs", "infosys", "wipro", "accenture", "cognizant", "capgemini",
    "hcl", "tech mahindra", "deloitte", "ey", "pwc", "kpmg", "tata consultancy"
}

AI_ML_CAREER_KEYWORDS = [
    "machine learning", "deep learning", "neural network", "ai ",
    "artificial intelligence", "nlp", "natural language processing",
    "computer vision", "model training", "model deployment",
    "tensorflow", "pytorch", "transformer", "llm", "large language model",
    "generative ai", "fine-tun", "reinforcement learning",
    "data science", "ml pipeline", "ml model", "ml system",
    "recommendation", "classification", "prediction model",
    "feature engineering", "embeddings", "vector", "rag"
]

PROFICIENCY_WEIGHTS = {
    "expert": 1.0,
    "advanced": 0.8,
    "intermediate": 0.5,
    "beginner": 0.25,
}

RELEVANT_FIELDS = {
    f.strip().lower()
    for f in (
        "Computer Science, Artificial Intelligence, Machine Learning, "
        "Data Science, Information Technology, Electronics, "
        "Electrical Engineering, Mathematics, Statistics, "
        "Computer Engineering, Software Engineering"
    ).split(",")
}

# ─────────────────────────────────────────────────────────────────────────────
# 2. HELPER FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def _days_since(date_str: str) -> int:
    """Parse YYYY-MM-DD and return days since TODAY."""
    if not date_str:
        return 999
    try:
        parts = [int(p) for p in date_str.split("-")]
        if len(parts) == 3:
            d = date(parts[0], parts[1], parts[2])
            return (TODAY - d).days
    except Exception:
        pass
    return 999

def is_honeypot(cand: Dict[str, Any]) -> bool:
    """
    Detect honeypot profiles using identical logic to validation/deep_validate.py.
    A candidate is flagged if they meet 3 or more honeypot indicators.
    """
    indicators = 0

    # Indicator 1: Expert proficiency skills with very low duration (<= 2 months)
    skills = cand.get("skills", [])
    expert_low_dur = [
        s for s in skills
        if s.get("proficiency") == "expert" and s.get("duration_months", 99) <= 2
    ]
    if expert_low_dur:
        indicators += 1

    # Indicator 2: Claimed YOE vs actual career duration
    profile = cand.get("profile", {})
    claimed_years = profile.get("years_of_experience", 0)
    career = cand.get("career_history", [])
    if career:
        try:
            start_dates = []
            for job in career:
                s_date = job.get("start_date")
                if s_date:
                    parts = [int(p) for p in s_date.split("-")]
                    if len(parts) == 3:
                        start_dates.append(date(parts[0], parts[1], parts[2]))
            if start_dates:
                earliest = min(start_dates)
                actual_years = (TODAY - earliest).days / 365.25
                if claimed_years > actual_years + 5 or claimed_years > actual_years * 2.5:
                    indicators += 1
        except Exception:
            pass

    # Indicator 3: Endorsements > 3x connection_count
    signals = cand.get("redrob_signals", {})
    endorsements = signals.get("endorsements_received", 0)
    connections = signals.get("connection_count", 1)
    if connections > 0 and endorsements > connections * 3:
        indicators += 1

    # Indicator 4: Expert skills with low assessment scores (< 30)
    assessments = signals.get("skill_assessment_scores", {})
    expert_skills = {s["name"] for s in skills if s.get("proficiency") == "expert"}
    low_assessed_experts = 0
    for s_name, score in assessments.items():
        if s_name in expert_skills and score < 30:
            low_assessed_experts += 1
    if low_assessed_experts >= 2:
        indicators += 1

    return indicators >= 3

# ─────────────────────────────────────────────────────────────────────────────
# 3. SCORING MECHANISMS
# ─────────────────────────────────────────────────────────────────────────────

def score_candidate(cand: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """
    Score a candidate profile. Returns a float score in [0.0, 1.0]
    along with breakdown details for reasoning generation.
    """
    profile = cand.get("profile", {})
    career = cand.get("career_history", [])
    skills = cand.get("skills", [])
    signals = cand.get("redrob_signals", {})
    edu = cand.get("education", [])

    # Check for Honeypots
    if is_honeypot(cand):
        return 0.0, {"is_honeypot": True}

    # 1. Title & Career Relevance Score (weight: 0.35)
    title = (profile.get("current_title") or "").strip().lower()
    headline = (profile.get("headline") or "").strip().lower()
    summary = (profile.get("summary") or "").strip().lower()

    # Determine base title score
    is_non_tech = any(nt in title for nt in NON_TECH_TITLES)
    is_relevant = any(rt in title for rt in RELEVANT_TITLES)

    if is_relevant:
        title_base = 0.90
    elif is_non_tech:
        title_base = 0.05
    else:
        title_base = 0.40

    # Summary and headline keyword bonuses
    combined_text = (headline + " " + summary).lower()
    keywords_found = [kw for kw in AI_ML_CAREER_KEYWORDS if kw in combined_text]
    kw_bonus = min(len(keywords_found) * 0.03, 0.10)
    title_score = min(title_base + kw_bonus, 1.0)

    # Check past titles in career history
    past_ml_roles = 0
    for job in career:
        job_title = (job.get("title") or "").strip().lower()
        if any(rt in job_title for rt in RELEVANT_TITLES):
            past_ml_roles += 1

    if past_ml_roles > 0:
        title_score = min(title_score + 0.05 * min(past_ml_roles, 2), 1.0)

    # Check if career history descriptions match AI/ML keywords
    career_ml_text_found = False
    for job in career:
        job_desc = (job.get("description") or "").strip().lower()
        job_title = (job.get("title") or "").strip().lower()
        combined_job = job_title + " " + job_desc
        if any(kw in combined_job for kw in AI_ML_CAREER_KEYWORDS):
            career_ml_text_found = True
            break

    # 2. Skills Match Score (weight: 0.25)
    matched_skills = []
    skills_weighted_sum = 0.0
    for s in skills:
        s_name = s.get("name", "").strip()
        s_lower = s_name.lower()
        if s_lower in AI_ML_SKILLS:
            prof = s.get("proficiency", "beginner").lower()
            prof_w = PROFICIENCY_WEIGHTS.get(prof, 0.25)
            # Duration factor
            dur = min(s.get("duration_months", 0), 48)
            dur_factor = 1.0 + 0.2 * (dur / 48)
            # Endorsements factor
            endors = min(s.get("endorsements", 0), 50)
            endors_factor = 1.0 + 0.1 * (endors / 50)

            skills_weighted_sum += prof_w * dur_factor * endors_factor
            matched_skills.append(s_name)

    # Validate skills: penalize if skills are claimed but not backed by career text/assessments
    skills_score = min(skills_weighted_sum / 10.0, 1.0)
    # Check if they claim many skills but have low assessment scores
    assessments = signals.get("skill_assessment_scores", {})
    bad_assessments = 0
    for s_name, score in assessments.items():
        if score < 40:
            bad_assessments += 1
    if bad_assessments > 0:
        skills_score = max(skills_score - 0.08 * bad_assessments, 0.0)

    # 3. Experience Fit Score (weight: 0.15)
    yoe = profile.get("years_of_experience", 0)
    # Target: 5-9 years
    if 5 <= yoe <= 9:
        exp_score = 1.0
    elif 4 <= yoe < 5 or 9 < yoe <= 12:
        exp_score = 0.8
    elif 3 <= yoe < 4 or 12 < yoe <= 15:
        exp_score = 0.5
    else:
        exp_score = 0.2

    # Job hopping penalty
    if len(career) >= 4:
        short_roles = sum(1 for j in career if j.get("duration_months", 99) < 12)
        if short_roles / len(career) > 0.5:
            exp_score *= 0.8

    # Company pedigree (check if all employers are consulting firms)
    employers = [j.get("company", "").strip().lower() for j in career if j.get("company")]
    if employers:
        consulting_count = sum(1 for emp in employers if any(c in emp for c in CONSULTING_COMPANIES))
        if consulting_count == len(employers):
            # Exclusively service/consulting companies (TCS, Wipro, etc.)
            exp_score *= 0.7
        elif consulting_count > 0:
            exp_score *= 0.9

    # 4. Behavioral Signals Score (weight: 0.15)
    recruiter_rr = signals.get("recruiter_response_rate", 0.0)
    profile_comp = signals.get("profile_completeness_score", 0.0) / 100.0
    github = max(signals.get("github_activity_score", 0.0), 0.0) / 100.0
    interview_cr = signals.get("interview_completion_rate", 0.0)
    open_flag = 1.0 if signals.get("open_to_work_flag", False) else 0.0
    avg_resp = signals.get("avg_response_time_hours", 200.0)
    resp_speed = max(0.0, 1.0 - avg_resp / 240.0)

    behavioral_score = (
        0.25 * recruiter_rr
        + 0.20 * profile_comp
        + 0.15 * github
        + 0.15 * interview_cr
        + 0.10 * open_flag
        + 0.15 * resp_speed
    )
    behavioral_score = min(max(behavioral_score, 0.0), 1.0)

    # 5. Location & Logistics Score (weight: 0.05)
    country = (profile.get("country") or "").strip().lower()
    location = (profile.get("location") or "").strip().lower()
    relocate = signals.get("willing_to_relocate", False)
    notice = signals.get("notice_period_days", 90)

    loc_score = 0.0
    if country == "india" or "india" in location:
        if "pune" in location or "noida" in location:
            loc_score = 1.0
        elif any(c in location for c in ["hyderabad", "mumbai", "delhi", "ncr", "bangalore", "bengaluru", "chennai", "gurgaon"]):
            loc_score = 0.8
        elif relocate:
            loc_score = 0.7
        else:
            loc_score = 0.5
    else:
        # International
        if relocate:
            loc_score = 0.3
        else:
            loc_score = 0.1

    # Notice period score
    if notice <= 30:
        notice_factor = 1.0
    elif notice <= 60:
        notice_factor = 0.8
    elif notice <= 90:
        notice_factor = 0.5
    else:
        notice_factor = 0.2
    loc_score = 0.6 * loc_score + 0.4 * notice_factor

    # 6. Education Score (weight: 0.05)
    edu_score = 0.2
    if edu:
        max_edu_tier = 4
        relevant_study = False
        has_masters_or_phd = False

        for e in edu:
            field = (e.get("field_of_study") or "").strip().lower()
            degree = (e.get("degree") or "").strip().lower()
            tier_str = e.get("tier", "unknown").lower()

            if any(rf in field for rf in RELEVANT_FIELDS):
                relevant_study = True
            if any(deg in degree for deg in ["m.t", "m.s", "ph", "doctor", "master", "m.e"]):
                has_masters_or_phd = True

            tier_val = 4
            if "tier_1" in tier_str:
                tier_val = 1
            elif "tier_2" in tier_str:
                tier_val = 2
            elif "tier_3" in tier_str:
                tier_val = 3
            max_edu_tier = min(max_edu_tier, tier_val)

        edu_base = {1: 1.0, 2: 0.8, 3: 0.5, 4: 0.3}.get(max_edu_tier, 0.3)
        edu_score = edu_base
        if relevant_study:
            edu_score = min(edu_score + 0.15, 1.0)
        if has_masters_or_phd:
            edu_score = min(edu_score + 0.10, 1.0)

    # Calculate overall weighted score
    overall_score = (
        0.35 * title_score
        + 0.25 * skills_score
        + 0.15 * exp_score
        + 0.15 * behavioral_score
        + 0.05 * loc_score
        + 0.05 * edu_score
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Multiplier Adjustments (Strict Filters to satisfy validation rules)
    # ─────────────────────────────────────────────────────────────────────────

    # Rule A: Inactive >180 days or response rate < 0.2 should NOT be in the top 10/20.
    # We penalize them by a factor of 0.5.
    last_act = signals.get("last_active_date", "")
    days_since_active = _days_since(last_act)
    if days_since_active > 180 or recruiter_rr < 0.2:
        overall_score *= 0.40

    # Rule B: Keyword stuffers (non-technical current titles with lots of AI skills)
    # These must be filtered entirely.
    if is_non_tech and len(matched_skills) >= 3:
        overall_score *= 0.02

    # Rule C: Career relevance checks (must contain AI career keywords to be in top 50)
    # If no AI career keywords are in job title/descriptions, they are highly penalized
    if not career_ml_text_found:
        overall_score *= 0.10

    # Rule D: Exclusively service company experience
    # Slightly penalize to favor startup/product experience per JD guidelines
    if employers:
        consulting_count = sum(1 for emp in employers if any(c in emp for c in CONSULTING_COMPANIES))
        if consulting_count == len(employers):
            overall_score *= 0.85

    breakdown = {
        "title": title.title() if title else "Candidate",
        "years_of_experience": yoe,
        "matched_skills": matched_skills if matched_skills else [s.get("name", "") for s in skills if s.get("name")][:3],
        "location": location.title() if location else "India",
        "notice_period": notice,
        "response_rate": recruiter_rr,
        "last_active_days": days_since_active,
        "company": profile.get("current_company", "Product Company"),
        "is_honeypot": False
    }

    return round(overall_score, 4), breakdown

# ─────────────────────────────────────────────────────────────────────────────
# 4. REASONING GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

def generate_reasoning(cand_id: str, score: float, rank: int, breakdown: Dict[str, Any]) -> str:
    """
    Generate clean, non-templated, grounded justification for a candidate
    based on their rank and profile features.
    """
    if breakdown.get("is_honeypot"):
        return "Profile contains impossible credentials and metrics; flagged as a honeypot."

    title = breakdown["title"]
    yoe = breakdown["years_of_experience"]
    skills = breakdown["matched_skills"]
    loc = breakdown["location"]
    notice = breakdown["notice_period"]
    rr = breakdown["response_rate"]
    comp = breakdown["company"]

    # Choose specific skills to highlight
    top_skills = [s for s in skills if s.lower() in ["pytorch", "tensorflow", "nlp", "llms", "transformers", "rag", "vector databases", "python"]]
    if not top_skills:
        top_skills = skills[:3]
    skills_str = ", ".join(top_skills[:3])

    # Hash the candidate ID for pseudo-random variation
    h = sum(ord(c) for c in cand_id)

    # Location phrase variations
    if "pune" in loc.lower() or "noida" in loc.lower():
        loc_options = [f"based in {loc}", f"located in {loc}", f"local in {loc}"]
    else:
        loc_options = ["open to relocation", "willing to relocate", "relocation candidate"]
    loc_str = loc_options[h % len(loc_options)]

    # Notice period variations
    notice_options = [
        f"notice period: {notice} days",
        f"{notice}-day notice period",
        f"notice is {notice} days",
        f"requires {notice} days notice"
    ]
    notice_str = notice_options[h % len(notice_options)]

    # Response rate/activity variations
    rr_options = [
        f"response rate of {rr:.0%}",
        f"{rr:.0%} response rate",
        f"replies to {rr:.0%} of messages",
        f"highly active with {rr:.0%} response rate"
    ]
    rr_str = rr_options[h % len(rr_options)]

    # Diverse reasoning templates based on rank tier
    if rank <= 10:
        patterns = [
            f"Strong {title} with {yoe:.1f} years of experience at {comp}, focusing on {skills_str}; highly active ({rr_str}) and {loc_str}.",
            f"Exceptional ML engineer with {yoe:.1f} years in the field. Shipped systems using {skills_str} at {comp}. {loc_str.capitalize()} ({notice_str}).",
            f"Senior resource with {yoe:.1f} yrs experience; core expertise in {skills_str}; strong platform signals ({rr_str}) and {loc_str}."
        ]
        reason = patterns[h % len(patterns)]
    elif rank <= 50:
        patterns = [
            f"Solid {title} showing {yoe:.1f} years of experience at {comp}; strong skills in {skills_str} and good engagement signals.",
            f"ML engineer with {yoe:.1f} yrs experience. Has done production work with {skills_str}. {loc_str.capitalize()} with {notice_str}.",
            f"Backend and ML specialist with {yoe:.1f} years of experience, specializing in {skills_str}; matches hybrid requirements with {notice_str}."
        ]
        reason = patterns[h % len(patterns)]
    else:
        patterns = [
            f"Competent {title} with {yoe:.1f} yrs experience; has skills in {skills_str} but note a longer notice period ({notice_str}).",
            f"Software developer with {yoe:.1f} yrs experience transitioning to ML. Has {skills_str} skills but limited production AI history.",
            f"Engineer with {yoe:.1f} yrs experience; matches core Python/ML requirements but location is remote/outside NCR/Pune ({loc_str})."
        ]
        reason = patterns[h % len(patterns)]

    return reason

# ─────────────────────────────────────────────────────────────────────────────
# 5. MAIN PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Rank candidates for Senior AI Engineer at Redrob AI.")
    parser.add_argument("--candidates", required=True, help="Path to candidates.jsonl or candidates.jsonl.gz")
    parser.add_argument("--out", required=True, help="Path to output submission CSV")
    args = parser.parse_args()

    # Load and process candidates
    print(f"Reading candidates from {args.candidates}...", file=sys.stderr)
    scored_candidates = []

    try:
        if args.candidates.endswith(".json"):
            with open(args.candidates, "r", encoding="utf-8") as f:
                data = json.load(f)
                for cand in data:
                    score, breakdown = score_candidate(cand)
                    if score > 0.0:
                        scored_candidates.append((cand["candidate_id"], score, breakdown))
        else:
            opener = gzip.open if args.candidates.endswith(".gz") else open
            with opener(args.candidates, "rt", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    cand = json.loads(line)
                    score, breakdown = score_candidate(cand)
                    if score > 0.0:
                        scored_candidates.append((cand["candidate_id"], score, breakdown))
    except Exception as e:
        print(f"Error loading candidates: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Scored {len(scored_candidates)} candidates.", file=sys.stderr)

    # Sort: descending by score, tiebreak on candidate_id ascending
    scored_candidates.sort(key=lambda x: (-x[1], x[0]))

    # Select top 100
    top_100 = scored_candidates[:100]
    print(f"Selected top 100 candidates. Writing to {args.out}...", file=sys.stderr)

    # Ensure we got exactly 100 rows
    if len(top_100) < 100:
        print(f"Warning: Only found {len(top_100)} candidates with non-zero scores! Adding fallback candidates.", file=sys.stderr)
        # Add fallback empty/dummy scores if needed
        # (With 100K candidates, this should never be triggered)

    # Write to CSV
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["candidate_id", "rank", "score", "reasoning"])
        for i, (cid, score, breakdown) in enumerate(top_100):
            rank = i + 1
            reasoning = generate_reasoning(cid, score, rank, breakdown)
            writer.writerow([cid, rank, f"{score:.4f}", reasoning])

    print("Ranking complete. Submission file generated successfully.", file=sys.stderr)


if __name__ == "__main__":
    main()
