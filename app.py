"""
KALI — Intelligent Candidate Ranker
=====================================
Gradio demo for the Redrob Hackathon (HuggingFace Spaces).

Team KALI  •  P Yashwanth Reddy
Scores and ranks candidates from a JSON profile dump using a
multi-factor weighted algorithm tuned for an AI / ML Engineer role.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import tempfile
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import warnings
warnings.filterwarnings("ignore")

import gradio as gr

# ──────────────────────────────────────────────
# 0.  CONSTANTS & REFERENCE SETS
# ──────────────────────────────────────────────

LOGO_PATH = os.path.join(os.path.dirname(__file__), "res", "Kali_teamLogo.webp")
SAMPLE_JSON_PATH = os.path.join(os.path.dirname(__file__), "res", "sample_candidates.json")

AI_ML_SKILLS: set[str] = {
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

RELEVANT_TITLES: set[str] = {
    t.strip().lower()
    for t in (
        "AI Engineer, ML Engineer, Machine Learning Engineer, Data Scientist, "
        "NLP Engineer, Research Engineer, Deep Learning Engineer, "
        "Software Engineer, Backend Engineer, Data Engineer, "
        "Full Stack Engineer, Platform Engineer, Junior ML Engineer, "
        "Senior Machine Learning Engineer"
    ).split(",")
}

NON_RELEVANT_TITLES: set[str] = {
    t.strip().lower()
    for t in (
        "Marketing Manager, HR Manager, Accountant, Customer Support, "
        "Sales Executive, Content Writer, Graphic Designer, "
        "Mechanical Engineer, Civil Engineer, Operations Manager, "
        "Business Analyst"
    ).split(",")
}

RELEVANT_FIELDS: set[str] = {
    f.strip().lower()
    for f in (
        "Computer Science, Artificial Intelligence, Machine Learning, "
        "Data Science, Information Technology, Electronics, "
        "Electrical Engineering, Mathematics, Statistics, "
        "Computer Engineering, Software Engineering"
    ).split(",")
}

PROFICIENCY_WEIGHT: dict[str, float] = {
    "expert": 1.0,
    "advanced": 0.8,
    "intermediate": 0.5,
    "beginner": 0.25,
}


# ──────────────────────────────────────────────
# 1.  SCORING ENGINE
# ──────────────────────────────────────────────


def _score_title(candidate: dict) -> tuple[float, str]:
    """Title / Career Relevance  (weight = 0.35)"""
    title = candidate.get("profile", {}).get("current_title", "").strip().lower()
    headline = candidate.get("profile", {}).get("headline", "").strip().lower()
    summary = candidate.get("profile", {}).get("summary", "").strip().lower()

    # Direct match with known relevant titles
    if title in RELEVANT_TITLES:
        base = 0.90
        reason = f"Title '{title.title()}' directly relevant to AI/ML"
    elif title in NON_RELEVANT_TITLES:
        base = 0.10
        reason = f"Title '{title.title()}' is non-technical / unrelated"
    else:
        base = 0.40
        reason = f"Title '{title.title()}' has partial relevance"

    # Bonus from headline / summary mentioning AI keywords
    ai_mentions = sum(
        1
        for kw in ("ai", "ml", "machine learning", "deep learning", "nlp",
                    "data scien", "neural", "llm", "transformer")
        if kw in headline or kw in summary
    )
    bonus = min(ai_mentions * 0.02, 0.10)
    score = min(base + bonus, 1.0)

    # Career history scan — bonus if any past title is relevant
    for role in candidate.get("career_history", []):
        past_title = role.get("title", "").strip().lower()
        if past_title in RELEVANT_TITLES:
            score = min(score + 0.05, 1.0)
            reason += f"; prior role as '{past_title.title()}' adds relevance"
            break

    return round(score, 4), reason


def _score_skills(candidate: dict) -> tuple[float, str, list[str]]:
    """Skills Match  (weight = 0.25)"""
    skills = candidate.get("skills", [])
    matched: list[str] = []
    weighted_sum = 0.0

    for sk in skills:
        name = sk.get("name", "").strip().lower()
        if name in AI_ML_SKILLS:
            prof = sk.get("proficiency", "beginner")
            w = PROFICIENCY_WEIGHT.get(prof, 0.25)
            # Duration bonus — capped at 48 months
            dur = min(sk.get("duration_months", 0), 48)
            dur_factor = 1.0 + 0.2 * (dur / 48)
            weighted_sum += w * dur_factor
            matched.append(sk.get("name", name))

    # Normalize: getting 10+ weighted points is excellent
    norm = min(weighted_sum / 10.0, 1.0)
    reason = f"{len(matched)} AI/ML skills matched (weighted {weighted_sum:.1f})"
    return round(norm, 4), reason, matched


def _score_experience(candidate: dict) -> tuple[float, str]:
    """Experience Fit  (weight = 0.15)"""
    yoe = candidate.get("profile", {}).get("years_of_experience", 0)
    # Bell curve centred on 7 years, σ ≈ 3
    ideal = 7.0
    sigma = 3.0
    raw = math.exp(-0.5 * ((yoe - ideal) / sigma) ** 2)
    reason = f"{yoe:.1f} yrs experience (ideal ≈ 5-9 yrs)"
    return round(raw, 4), reason


def _score_behavioral(candidate: dict) -> tuple[float, str]:
    """Behavioral / Engagement Signals  (weight = 0.15)"""
    signals = candidate.get("redrob_signals", {})

    recruiter_rr = signals.get("recruiter_response_rate", 0)
    profile_comp = signals.get("profile_completeness_score", 0) / 100
    github = max(signals.get("github_activity_score", 0), 0) / 100
    interview_cr = signals.get("interview_completion_rate", 0)
    open_flag = 1.0 if signals.get("open_to_work_flag", False) else 0.0
    avg_resp = signals.get("avg_response_time_hours", 200)
    resp_speed = max(0, 1.0 - avg_resp / 200)  # faster = better

    # Weighted combination
    score = (
        0.25 * recruiter_rr
        + 0.20 * profile_comp
        + 0.15 * github
        + 0.15 * interview_cr
        + 0.10 * open_flag
        + 0.15 * resp_speed
    )
    parts = []
    parts.append(f"resp_rate={recruiter_rr:.0%}")
    parts.append(f"profile={profile_comp:.0%}")
    parts.append(f"github={github:.0%}")
    reason = "Behavioral: " + ", ".join(parts)
    return round(min(score, 1.0), 4), reason


def _score_location(candidate: dict) -> tuple[float, str]:
    """Location / Logistics  (weight = 0.05)"""
    profile = candidate.get("profile", {})
    country = profile.get("country", "").strip().lower()
    location = profile.get("location", "").strip().lower()
    signals = candidate.get("redrob_signals", {})
    relocate = signals.get("willing_to_relocate", False)

    if country == "india":
        score = 0.70
        if any(city in location for city in ("pune", "noida", "delhi", "bangalore", "bengaluru", "hyderabad", "mumbai")):
            score = 0.90
            if any(city in location for city in ("pune", "noida")):
                score = 1.0
        reason = f"India – {profile.get('location', 'N/A')}"
    else:
        score = 0.20
        reason = f"Overseas – {profile.get('location', 'N/A')}"
        if relocate:
            score = 0.40
            reason += " (willing to relocate)"

    return round(score, 4), reason


def _score_education(candidate: dict) -> tuple[float, str]:
    """Education  (weight = 0.05)"""
    education = candidate.get("education", [])
    if not education:
        return 0.3, "No education data"

    best = 0.0
    best_reason = ""
    for edu in education:
        field = edu.get("field_of_study", "").strip().lower()
        tier = edu.get("tier", "unknown")
        degree = edu.get("degree", "").strip().lower()

        # Field relevance
        if field in RELEVANT_FIELDS:
            field_score = 0.6
        else:
            field_score = 0.2

        # Tier
        tier_map = {"tier_1": 0.25, "tier_2": 0.15, "tier_3": 0.08, "tier_4": 0.02, "unknown": 0.05}
        tier_score = tier_map.get(tier, 0.05)

        # Degree level
        degree_bonus = 0.0
        if any(kw in degree for kw in ("m.tech", "m.s.", "m.sc", "mca", "master")):
            degree_bonus = 0.10
        elif any(kw in degree for kw in ("ph.d", "phd", "doctor")):
            degree_bonus = 0.15

        total = field_score + tier_score + degree_bonus
        if total > best:
            best = total
            inst = edu.get("institution", "N/A")
            best_reason = f"{edu.get('degree', '?')} in {edu.get('field_of_study', '?')} from {inst} ({tier})"

    return round(min(best, 1.0), 4), best_reason


# ── Top-level scoring function ────────────────

WEIGHTS = {
    "title":      0.35,
    "skills":     0.25,
    "experience": 0.15,
    "behavioral": 0.15,
    "location":   0.05,
    "education":  0.05,
}


def score_candidate(candidate: dict) -> dict[str, Any]:
    """Return composite score, breakdown, matched skills, and human-readable reasoning."""
    title_score, title_reason = _score_title(candidate)
    skills_score, skills_reason, matched_skills = _score_skills(candidate)
    exp_score, exp_reason = _score_experience(candidate)
    beh_score, beh_reason = _score_behavioral(candidate)
    loc_score, loc_reason = _score_location(candidate)
    edu_score, edu_reason = _score_education(candidate)

    breakdown = {
        "title":      title_score,
        "skills":     skills_score,
        "experience": exp_score,
        "behavioral": beh_score,
        "location":   loc_score,
        "education":  edu_score,
    }

    composite = sum(WEIGHTS[k] * breakdown[k] for k in WEIGHTS)
    composite = round(composite, 4)

    # Build human-readable one-liner
    reasoning_parts = [
        f"Title({title_score:.2f}): {title_reason}",
        f"Skills({skills_score:.2f}): {skills_reason}",
        f"Exp({exp_score:.2f}): {exp_reason}",
        f"Signals({beh_score:.2f}): {beh_reason}",
        f"Loc({loc_score:.2f}): {loc_reason}",
        f"Edu({edu_score:.2f}): {edu_reason}",
    ]
    reasoning = "; ".join(reasoning_parts)

    return {
        "candidate_id": candidate.get("candidate_id", "UNKNOWN"),
        "score": composite,
        "breakdown": breakdown,
        "matched_skills": matched_skills,
        "reasoning": reasoning,
        "current_title": candidate.get("profile", {}).get("current_title", "N/A"),
        "years_of_experience": candidate.get("profile", {}).get("years_of_experience", 0),
        "name": candidate.get("profile", {}).get("anonymized_name", "N/A"),
        "location": candidate.get("profile", {}).get("location", "N/A"),
    }


# ──────────────────────────────────────────────
# 2.  RANKING PIPELINE
# ──────────────────────────────────────────────


def rank_candidates(
    candidates: list[dict],
    progress_cb=None,
) -> tuple[list[dict], float]:
    """Score every candidate and return sorted results + elapsed time."""
    t0 = time.perf_counter()
    results: list[dict] = []

    for idx, cand in enumerate(candidates):
        results.append(score_candidate(cand))
        if progress_cb is not None:
            progress_cb((idx + 1) / len(candidates))

    results.sort(key=lambda r: r["score"], reverse=True)

    # Assign ranks
    for rank, r in enumerate(results, 1):
        r["rank"] = rank

    elapsed = round(time.perf_counter() - t0, 3)
    return results, elapsed


def results_to_table(results: list[dict]) -> list[list]:
    """Convert results to a list-of-lists for gr.Dataframe."""
    rows = []
    for r in results:
        rows.append([
            r["rank"],
            r["candidate_id"],
            r["name"],
            f'{r["score"]:.4f}',
            r["current_title"],
            f'{r["years_of_experience"]:.1f}',
            r["location"],
            ", ".join(r["matched_skills"][:8]) + ("…" if len(r["matched_skills"]) > 8 else ""),
            r["reasoning"][:220] + ("…" if len(r["reasoning"]) > 220 else ""),
        ])
    return rows


TABLE_HEADERS = [
    "Rank", "Candidate ID", "Name", "Score",
    "Current Title", "YoE", "Location", "Key Skills Matched", "Reasoning",
]


def results_to_csv_bytes(results: list[dict]) -> bytes:
    """Build CSV bytes from results for download."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["candidate_id", "rank", "score", "reasoning"])
    for r in results:
        writer.writerow([
            r["candidate_id"],
            r["rank"],
            f'{r["score"]:.4f}',
            r["reasoning"],
        ])
    return buf.getvalue().encode("utf-8")


def build_summary_markdown(results: list[dict], elapsed: float) -> str:
    """Create a rich Markdown summary block."""
    n = len(results)
    if n == 0:
        return "⚠️ No candidates processed."

    top_score = results[0]["score"]
    bottom_score = results[-1]["score"]
    avg_score = sum(r["score"] for r in results) / n

    # Aggregate skill counts
    skill_counter: Counter = Counter()
    for r in results:
        for sk in r["matched_skills"]:
            skill_counter[sk] += 1
    top_skills = skill_counter.most_common(10)

    # Score distribution buckets
    buckets = {"★★★★★ (0.8-1.0)": 0, "★★★★ (0.6-0.8)": 0, "★★★ (0.4-0.6)": 0, "★★ (0.2-0.4)": 0, "★ (0.0-0.2)": 0}
    for r in results:
        s = r["score"]
        if s >= 0.8:
            buckets["★★★★★ (0.8-1.0)"] += 1
        elif s >= 0.6:
            buckets["★★★★ (0.6-0.8)"] += 1
        elif s >= 0.4:
            buckets["★★★ (0.4-0.6)"] += 1
        elif s >= 0.2:
            buckets["★★ (0.2-0.4)"] += 1
        else:
            buckets["★ (0.0-0.2)"] += 1

    md = f"""
## 📊 Ranking Summary

| Metric | Value |
|--------|-------|
| **Total Candidates** | {n} |
| **Processing Time** | {elapsed:.3f}s |
| **Top Score** | {top_score:.4f} |
| **Average Score** | {avg_score:.4f} |
| **Bottom Score** | {bottom_score:.4f} |

### Score Distribution
| Tier | Count |
|------|-------|
"""
    for tier, count in buckets.items():
        bar = "█" * count + " " if count else ""
        md += f"| {tier} | {count}  {bar}|\n"

    md += "\n### 🏆 Top 5 Candidates\n"
    md += "| Rank | ID | Name | Score | Title |\n|------|-----|------|-------|-------|\n"
    for r in results[:5]:
        md += f'| {r["rank"]} | {r["candidate_id"]} | {r["name"]} | {r["score"]:.4f} | {r["current_title"]} |\n'

    md += "\n### 🔑 Most Matched AI/ML Skills\n"
    md += "| Skill | Candidates |\n|-------|------------|\n"
    for skill, cnt in top_skills:
        md += f"| {skill} | {cnt} |\n"

    return md


# ──────────────────────────────────────────────
# 3.  GRADIO APPLICATION
# ──────────────────────────────────────────────

# ── Custom theme & CSS ─────────────────────────

CUSTOM_CSS = """
/* ── Global ───────────────────────────── */
.gradio-container {
    max-width: 1260px !important;
    margin: auto !important;
}
/* ── Header hero ──────────────────────── */
#hero-row {
    background: linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%);
    border-radius: 16px;
    padding: 28px 36px;
    margin-bottom: 18px;
    display: flex;
    align-items: center;
    gap: 24px;
    box-shadow: 0 8px 32px rgba(0,0,0,.45);
}
#hero-row img {
    border-radius: 14px;
    max-height: 100px;
}
#hero-text h1 {
    margin: 0;
    font-size: 2.2rem;
    background: linear-gradient(90deg, #00d2ff, #928dff, #ff6ec7);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    font-weight: 800;
}
#hero-text p {
    margin: 4px 0 0;
    color: #b0b0cc;
    font-size: 1.05rem;
}
/* ── Buttons ──────────────────────────── */
.primary-btn {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
    border: none !important;
    font-weight: 700 !important;
    font-size: 1.05rem !important;
    letter-spacing: .3px;
    transition: transform .15s ease;
}
.primary-btn:hover {
    transform: scale(1.03);
}
/* ── Stats cards row ──────────────────── */
.stat-card {
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    border-radius: 12px;
    padding: 18px;
    text-align: center;
    border: 1px solid rgba(255,255,255,.08);
}
.stat-card h3 {
    margin: 0 0 4px;
    font-size: .85rem;
    color: #8888aa;
    text-transform: uppercase;
    letter-spacing: 1px;
}
.stat-card p {
    margin: 0;
    font-size: 1.6rem;
    font-weight: 800;
    background: linear-gradient(90deg, #00d2ff, #928dff);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}
/* ── Table polish ─────────────────────── */
table { font-size: .92rem !important; }
/* ── About section ────────────────────── */
.about-section {
    background: linear-gradient(135deg, #0f0c29 0%, #1a1a2e 100%);
    border-radius: 14px;
    padding: 28px 32px;
    border: 1px solid rgba(255,255,255,.06);
}
.about-section h2 {
    background: linear-gradient(90deg, #00d2ff, #928dff);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}
"""


def _load_sample() -> str:
    """Return the path to the bundled sample JSON."""
    if os.path.isfile(SAMPLE_JSON_PATH):
        return SAMPLE_JSON_PATH
    return ""


def _process(file_obj, use_sample: bool, progress=gr.Progress()):
    """Main processing callback."""
    # Determine data source
    if use_sample:
        src_path = SAMPLE_JSON_PATH
        if not os.path.isfile(src_path):
            raise gr.Error("Sample file not found — please upload a JSON file instead.")
    elif file_obj is not None:
        src_path = file_obj  # Gradio gives a temp path string for type="filepath"
    else:
        raise gr.Error("Please upload a JSON file or click 'Use Sample Data'.")

    # Load JSON
    try:
        with open(src_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise gr.Error(f"Invalid JSON: {exc}")
    except Exception as exc:
        raise gr.Error(f"Could not read file: {exc}")

    # Normalise: accept list or single-object
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise gr.Error("JSON must be an array of candidate objects (or a single object).")
    if len(data) == 0:
        raise gr.Error("JSON file contains no candidates.")
    if len(data) > 200:
        raise gr.Error(f"Demo supports ≤200 candidates, got {len(data)}. Trim the file.")

    # Rank
    results, elapsed = rank_candidates(data, progress_cb=progress)

    # Build outputs
    table_data = results_to_table(results)
    summary_md = build_summary_markdown(results, elapsed)

    # CSV download
    csv_bytes = results_to_csv_bytes(results)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv", prefix="kali_ranked_")
    tmp.write(csv_bytes)
    tmp.close()

    return table_data, summary_md, tmp.name


def _process_upload(file_obj, progress=gr.Progress()):
    return _process(file_obj, use_sample=False, progress=progress)


def _process_sample(progress=gr.Progress()):
    return _process(None, use_sample=True, progress=progress)


def build_app() -> gr.Blocks:
    theme = gr.themes.Base(
        primary_hue=gr.themes.colors.indigo,
        secondary_hue=gr.themes.colors.purple,
        neutral_hue=gr.themes.colors.slate,
        font=gr.themes.GoogleFont("Inter"),
    ).set(
        body_background_fill="#0b0b1a",
        body_background_fill_dark="#0b0b1a",
        block_background_fill="#111128",
        block_background_fill_dark="#111128",
        block_border_color="rgba(255,255,255,0.06)",
        block_label_text_color="#a0a0cc",
        block_title_text_color="#d0d0ee",
        input_background_fill="#181835",
        input_background_fill_dark="#181835",
        button_primary_background_fill="linear-gradient(135deg, #667eea 0%, #764ba2 100%)",
        button_primary_text_color="#ffffff",
    )

    with gr.Blocks(theme=theme, css=CUSTOM_CSS, title="KALI – Intelligent Candidate Ranker") as demo:

        # ─── Hero header ────────────────────────
        with gr.Row(elem_id="hero-row"):
            if os.path.isfile(LOGO_PATH):
                gr.Image(
                    value=LOGO_PATH,
                    show_label=False,
                    interactive=False,
                    height=100,
                    width=100,
                    container=False,
                )
            with gr.Column(elem_id="hero-text"):
                gr.Markdown(
                    "# KALI — Intelligent Candidate Ranker\n"
                    "Multi-factor AI/ML talent scoring engine  •  "
                    "Team KALI  •  Redrob Hackathon 2026"
                )

        # ─── Tabs ────────────────────────────────
        with gr.Tabs():
            # ── TAB 1: Ranker ────────────────────
            with gr.Tab("🚀 Rank Candidates", id="rank"):
                gr.Markdown(
                    "Upload a **JSON file** with candidate profiles (≤ 200) "
                    "or use the bundled sample data to see the ranker in action."
                )

                with gr.Row():
                    with gr.Column(scale=3):
                        file_input = gr.File(
                            label="📄 Upload Candidate JSON",
                            file_types=[".json"],
                            type="filepath",
                        )
                    with gr.Column(scale=1, min_width=200):
                        btn_upload = gr.Button(
                            "⚡ Rank Uploaded File",
                            variant="primary",
                            elem_classes=["primary-btn"],
                            size="lg",
                        )
                        btn_sample = gr.Button(
                            "📦 Use Sample Data",
                            variant="secondary",
                            size="lg",
                        )

                # Summary
                summary_md = gr.Markdown(
                    value="*Results will appear here after ranking…*",
                    label="Summary",
                )

                # Results table
                results_table = gr.Dataframe(
                    headers=TABLE_HEADERS,
                    datatype=["number", "str", "str", "str", "str", "str", "str", "str", "str"],
                    label="📋 Ranked Candidates",
                    wrap=True,
                    column_widths=[
                        "60px", "120px", "130px", "80px",
                        "150px", "55px", "120px", "200px", "340px",
                    ],
                    interactive=False,
                )

                csv_download = gr.File(label="⬇️ Download Ranked CSV", interactive=False)

                # Wire events
                btn_upload.click(
                    fn=_process_upload,
                    inputs=[file_input],
                    outputs=[results_table, summary_md, csv_download],
                )
                btn_sample.click(
                    fn=_process_sample,
                    inputs=[],
                    outputs=[results_table, summary_md, csv_download],
                )

            # ── TAB 2: About ─────────────────────
            with gr.Tab("ℹ️ About", id="about"):
                gr.Markdown("""
<div class="about-section">

## 🧠 About KALI

**KALI** (Knowledge-Augmented Listing Intelligence) is an intelligent
candidate ranking engine built for the **Redrob Hackathon 2026**.

It scores and ranks candidates for an **AI / ML Engineer** role using a
**multi-factor weighted algorithm** that evaluates six orthogonal
dimensions of candidate fitness.

---

### 🏗️ Methodology

| Factor | Weight | What It Measures |
|--------|--------|------------------|
| **Title / Career Relevance** | 35 % | Current & past titles vs. target role taxonomy |
| **Skills Match** | 25 % | AI/ML skill overlap weighted by proficiency & duration |
| **Experience Fit** | 15 % | Years of experience vs. ideal bell-curve (5-9 yrs) |
| **Behavioral Signals** | 15 % | Platform engagement: response rate, profile completeness, GitHub activity |
| **Location / Logistics** | 5 % | India preference, metro-city bonus, relocation willingness |
| **Education** | 5 % | Field relevance, institution tier, degree level |

Each factor produces a **0–1 sub-score**; the final composite is a
weighted sum, also on a 0–1 scale.

---

### 🎯 Design Principles

- **No LLM calls** — pure algorithmic scoring for speed & reproducibility
- **Transparent reasoning** — every score comes with a human-readable explanation
- **Extensible weights** — easy to re-tune for different roles / geographies
- **Hackathon-ready** — runs in < 1 second on 100 candidates, deploys on HF Spaces

---

### 👤 Team

| | |
|---|---|
| **Team Name** | KALI |
| **Member** | P Yashwanth Reddy |
| **Event** | Redrob – Intelligent Candidate Discovery & Ranking Challenge |

---

*Built with 🐍 Python + 🖼️ Gradio  •  Deployed on 🤗 HuggingFace Spaces*

</div>
""")

    return demo


# ──────────────────────────────────────────────
# 4.  ENTRY POINT
# ──────────────────────────────────────────────

if __name__ == "__main__":
    app = build_app()
    # Bind to 127.0.0.1 locally so links are clickable on Windows, but use 0.0.0.0 in HuggingFace Spaces
    is_hf = "SPACE_ID" in os.environ
    app.launch(
        server_name="0.0.0.0" if is_hf else "127.0.0.1",
        server_port=7860,
        share=False,
        show_error=True,
    )
