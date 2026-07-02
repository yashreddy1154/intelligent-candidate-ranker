#!/usr/bin/env python3
"""
deep_validate.py — Deep quality validator for Redrob Hackathon submissions.

Performs basic CSV validation (matching the official validator) plus deep
quality checks against the candidates data file:

  - Honeypot detection (fake expert profiles)
  - Keyword stuffer detection (non-technical roles with AI/ML skill lists)
  - Career relevance analysis (AI/ML keywords in career history)
  - Reasoning quality (uniqueness, length, factual grounding)
  - Behavioral signal health (top-10 response rates, activity recency)

Usage:
    python validation/deep_validate.py --csv submission.csv --candidates res/candidates.jsonl.gz

Only standard-library imports are used.
"""

import argparse
import csv
import gzip
import json
import re
import sys
from collections import Counter
from datetime import date, datetime

# ─────────────────────────────────────────────────────────────────────────────
# Windows terminal compatibility
# ─────────────────────────────────────────────────────────────────────────────
# Reconfigure stdout for UTF-8 so Unicode symbols (✓, ✗, ⚠) render properly.
# Enable ANSI escape-code processing on Windows 10+ terminals.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.platform == "win32":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 0x0007)
    except Exception:
        pass  # Graceful fallback if ctypes is unavailable

# ─────────────────────────────────────────────────────────────────────────────
# ANSI colour helpers
# ─────────────────────────────────────────────────────────────────────────────
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

PASS_MARK = f"{GREEN}✓{RESET}"
FAIL_MARK = f"{RED}✗{RESET}"
WARN_MARK = f"{YELLOW}⚠{RESET}"


def ok(msg: str) -> str:
    return f"  {PASS_MARK} {msg}"


def fail(msg: str) -> str:
    return f"  {FAIL_MARK} {msg}"


def warn(msg: str) -> str:
    return f"  {WARN_MARK} {msg}"


def section(title: str) -> str:
    return f"\n{BOLD}{CYAN}=== {title} ==={RESET}"


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
REQUIRED_HEADER = ["candidate_id", "rank", "score", "reasoning"]
CANDIDATE_ID_RE = re.compile(r"^CAND_[0-9]{7}$")
EXPECTED_ROWS = 100

# Non-technical titles that should NOT have lots of AI/ML skills
NON_TECH_TITLES = [
    "marketing manager",
    "hr manager",
    "accountant",
    "sales executive",
    "customer support",
    "content writer",
    "graphic designer",
    "mechanical engineer",
    "civil engineer",
    "operations manager",
]

# AI/ML skill keywords (case-insensitive substring matching)
AI_ML_SKILL_KEYWORDS = [
    "machine learning", "deep learning", "neural network", "tensorflow",
    "pytorch", "keras", "nlp", "natural language", "computer vision",
    "reinforcement learning", "transformer", "bert", "gpt", "llm",
    "generative ai", "langchain", "hugging face", "huggingface",
    "fine-tuning", "fine tuning", "rag", "vector database",
    "embedding", "diffusion", "stable diffusion", "gan", "gans",
    "cnn", "rnn", "lstm", "attention mechanism", "prompt engineering",
    "mlops", "ml pipeline", "feature engineering", "model deployment",
    "scikit-learn", "sklearn", "xgboost", "lightgbm", "catboost",
    "image classification", "object detection", "speech recognition",
    "recommendation system", "anomaly detection", "time series",
    "data science", "ai engineer", "ml engineer", "milvus", "pinecone",
    "weaviate", "chroma", "faiss", "lora", "qlora", "peft",
    "weights & biases", "wandb", "mlflow", "bentoml", "tts",
    "speech synthesis", "statistical modeling", "bayesian",
]

# AI/ML keywords for career history text matching
AI_ML_CAREER_KEYWORDS = [
    "machine learning", "deep learning", "neural network", "ai ",
    "artificial intelligence", "nlp", "natural language processing",
    "computer vision", "model training", "model deployment",
    "tensorflow", "pytorch", "transformer", "llm", "large language model",
    "generative ai", "fine-tun", "reinforcement learning",
    "data science", "ml pipeline", "ml model", "ml system",
    "recommendation", "classification", "prediction model",
    "feature engineering", "embeddings", "vector", "rag",
]

# ─────────────────────────────────────────────────────────────────────────────
# Data loading
# ─────────────────────────────────────────────────────────────────────────────

def load_candidates(path: str) -> dict:
    """Load candidates JSONL (optionally gzipped) into {candidate_id: record}."""
    opener = gzip.open if path.endswith(".gz") else open
    candidates = {}
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            candidates[record["candidate_id"]] = record
    return candidates


def load_submission(csv_path: str) -> tuple:
    """
    Parse the submission CSV.

    Returns (header, rows, errors) where each row is a dict with keys
    candidate_id, rank (int), score (float), reasoning (str).
    """
    errors = []
    rows = []

    try:
        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            try:
                header = next(reader)
            except StopIteration:
                return None, [], ["File is empty — no header row."]

            raw_rows = [r for r in reader if any(cell.strip() for cell in r)]
    except UnicodeDecodeError:
        return None, [], ["File is not valid UTF-8."]
    except OSError as e:
        return None, [], [f"Cannot read file: {e}"]

    if header != REQUIRED_HEADER:
        errors.append(
            f"Header must be {REQUIRED_HEADER}, found {header}"
        )

    if len(raw_rows) != EXPECTED_ROWS:
        errors.append(
            f"Expected {EXPECTED_ROWS} data rows, found {len(raw_rows)}."
        )

    seen_ids = set()
    seen_ranks = set()

    for i, cells in enumerate(raw_rows):
        row_num = i + 2  # 1-indexed, row 1 is header
        if len(cells) != len(REQUIRED_HEADER):
            errors.append(
                f"Row {row_num}: expected {len(REQUIRED_HEADER)} columns, got {len(cells)}."
            )
            continue

        cid, rank_s, score_s, reasoning = [c.strip() for c in cells]

        # candidate_id
        if not cid:
            errors.append(f"Row {row_num}: candidate_id is empty.")
        elif not CANDIDATE_ID_RE.match(cid):
            errors.append(f"Row {row_num}: invalid candidate_id format '{cid}'.")
        elif cid in seen_ids:
            errors.append(f"Row {row_num}: duplicate candidate_id '{cid}'.")
        else:
            seen_ids.add(cid)

        # rank
        try:
            rank = int(rank_s)
            if str(rank) != rank_s:
                raise ValueError
            if not 1 <= rank <= 100:
                errors.append(f"Row {row_num}: rank {rank} out of range 1-100.")
            elif rank in seen_ranks:
                errors.append(f"Row {row_num}: duplicate rank {rank}.")
            else:
                seen_ranks.add(rank)
        except ValueError:
            errors.append(f"Row {row_num}: rank must be an integer (1-100).")
            rank = None

        # score
        try:
            score = float(score_s)
        except ValueError:
            errors.append(f"Row {row_num}: score must be a float.")
            score = None

        rows.append({
            "candidate_id": cid,
            "rank": rank,
            "score": score,
            "reasoning": reasoning,
        })

    # Missing ranks
    missing_ranks = set(range(1, 101)) - seen_ranks
    if missing_ranks:
        errors.append(f"Missing ranks: {sorted(missing_ranks)}")

    # Non-increasing score check
    scored = sorted(
        [(r["rank"], r["score"]) for r in rows if r["rank"] is not None and r["score"] is not None],
        key=lambda x: x[0],
    )
    for j in range(len(scored) - 1):
        r1, s1 = scored[j]
        r2, s2 = scored[j + 1]
        if s1 < s2:
            errors.append(
                f"Score must be non-increasing: rank {r1} ({s1}) < rank {r2} ({s2})."
            )

    return header, rows, errors


# ─────────────────────────────────────────────────────────────────────────────
# Deep checks
# ─────────────────────────────────────────────────────────────────────────────

def _is_ai_ml_skill(skill_name: str) -> bool:
    """Check if a skill name matches any AI/ML keyword."""
    lower = skill_name.lower()
    return any(kw in lower for kw in AI_ML_SKILL_KEYWORDS)


def _days_since(date_str: str) -> int:
    """Days between a date string (YYYY-MM-DD) and today."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        return (date.today() - d).days
    except (ValueError, TypeError):
        return -1


def check_honeypots(rows: list, candidates: dict) -> list:
    """
    Detect honeypot candidates — profiles designed to mislead naive rankers.

    Signals:
      1. Expert proficiency skills with 0 or very low duration_months (≤2)
      2. years_of_experience inconsistent with career_history dates
      3. Impossibly high endorsement counts relative to connection_count
      4. Multiple expert-level skills with very low assessment scores
    """
    report = []
    flagged = []

    for row in rows:
        cid = row["candidate_id"]
        cand = candidates.get(cid)
        if not cand:
            continue

        indicators = []

        # --- Signal 1: Expert skills with 0/very low duration ---
        skills = cand.get("skills", [])
        expert_low_dur = [
            s for s in skills
            if s.get("proficiency") == "expert" and s.get("duration_months", 99) <= 2
        ]
        if expert_low_dur:
            names = ", ".join(s["name"] for s in expert_low_dur)
            indicators.append(
                f"Expert in {len(expert_low_dur)} skill(s) with ≤2 months usage: {names}"
            )

        # --- Signal 2: years_of_experience vs career dates ---
        profile = cand.get("profile", {})
        claimed_years = profile.get("years_of_experience", 0)
        career = cand.get("career_history", [])
        if career:
            try:
                earliest = min(
                    datetime.strptime(j["start_date"], "%Y-%m-%d")
                    for j in career if j.get("start_date")
                )
                actual_years = (date.today() - earliest.date()).days / 365.25
                # Flag if claimed is more than double the actual, or actual < claimed - 5
                if claimed_years > actual_years + 5 or claimed_years > actual_years * 2.5:
                    indicators.append(
                        f"Claims {claimed_years} yrs but earliest role is {actual_years:.1f} yrs ago"
                    )
            except (ValueError, TypeError):
                pass

        # --- Signal 3: Endorsements vs connections ---
        signals = cand.get("redrob_signals", {})
        endorsements = signals.get("endorsements_received", 0)
        connections = signals.get("connection_count", 1)
        if connections > 0 and endorsements > connections * 3:
            indicators.append(
                f"Endorsements ({endorsements}) > 3× connections ({connections})"
            )

        # --- Signal 4: Expert skills with low assessment scores ---
        assessments = signals.get("skill_assessment_scores", {})
        expert_skills = {s["name"] for s in skills if s.get("proficiency") == "expert"}
        low_assessed_experts = []
        for skill_name, score in assessments.items():
            if skill_name in expert_skills and score < 30:
                low_assessed_experts.append(f"{skill_name}={score}")
        if len(low_assessed_experts) >= 2:
            indicators.append(
                f"{len(low_assessed_experts)} expert skills with assessment <30: "
                + ", ".join(low_assessed_experts)
            )

        if len(indicators) >= 3:
            flagged.append((row["rank"], cid, indicators))

    # Build report
    if flagged:
        flagged.sort(key=lambda x: x[0] if x[0] is not None else 999)
        report.append(
            warn(f"Found {len(flagged)} potential honeypot(s) in top 100:")
        )
        for rank, cid, inds in flagged:
            report.append(f"    Rank {rank}: {cid}")
            for ind in inds:
                report.append(f"      - {ind}")
        pct = len(flagged)
        color = RED if pct > 10 else YELLOW
        report.append(
            f"  Honeypot rate: {color}{pct}%{RESET} (threshold: <10%)"
        )
        if pct > 10:
            report.append(
                fail(f"WARNING: {pct} candidates appear to be honeypots. "
                     "Disqualification threshold is >10.")
            )
    else:
        report.append(ok("No honeypot signals detected."))

    return report, len(flagged)


def check_keyword_stuffers(rows: list, candidates: dict) -> list:
    """Detect non-technical roles that list lots of AI/ML skills."""
    report = []
    flagged = []

    for row in rows:
        cid = row["candidate_id"]
        cand = candidates.get(cid)
        if not cand:
            continue

        profile = cand.get("profile", {})
        title = (profile.get("current_title") or "").lower()

        # Check if title matches a non-technical role
        is_non_tech = any(nt in title for nt in NON_TECH_TITLES)
        if not is_non_tech:
            continue

        # Count AI/ML skills
        skills = cand.get("skills", [])
        ai_skills = [s["name"] for s in skills if _is_ai_ml_skill(s["name"])]

        if len(ai_skills) >= 3:
            flagged.append((
                row["rank"],
                cid,
                profile.get("current_title", "Unknown"),
                ai_skills,
            ))

    if flagged:
        flagged.sort(key=lambda x: x[0] if x[0] is not None else 999)
        report.append(
            warn(f"Found {len(flagged)} potential keyword stuffer(s):")
        )
        for rank, cid, title, ai_skills in flagged:
            report.append(
                f"    Rank {rank}: {cid} — {title} "
                f"but lists {len(ai_skills)} AI/ML skills: {', '.join(ai_skills[:5])}"
                + ("..." if len(ai_skills) > 5 else "")
            )
    else:
        report.append(ok("No keyword stuffers detected."))

    return report, len(flagged)


def check_career_relevance(rows: list, candidates: dict) -> list:
    """Check whether ranked candidates have AI/ML keywords in career history."""
    report = []

    # Sort rows by rank
    ranked = sorted(
        [r for r in rows if r["rank"] is not None],
        key=lambda r: r["rank"],
    )
    top_10 = [r for r in ranked if r["rank"] <= 10]
    top_50 = [r for r in ranked if r["rank"] <= 50]

    def career_has_ai(cand: dict) -> bool:
        for job in cand.get("career_history", []):
            desc = (job.get("description") or "").lower()
            title = (job.get("title") or "").lower()
            combined = desc + " " + title
            if any(kw in combined for kw in AI_ML_CAREER_KEYWORDS):
                return True
        return False

    # Top 10
    no_ai_top10 = []
    for r in top_10:
        cand = candidates.get(r["candidate_id"])
        if cand and not career_has_ai(cand):
            no_ai_top10.append(r)

    if no_ai_top10:
        report.append(
            warn(f"Top 10: {len(no_ai_top10)} candidate(s) have NO AI/ML keywords in career history:")
        )
        for r in no_ai_top10:
            cand = candidates.get(r["candidate_id"], {})
            title = cand.get("profile", {}).get("current_title", "N/A")
            report.append(f"    Rank {r['rank']}: {r['candidate_id']} — {title}")
    else:
        report.append(ok("Top 10: All candidates have AI/ML keywords in career history."))

    # Top 50
    no_ai_top50 = []
    for r in top_50:
        cand = candidates.get(r["candidate_id"])
        if cand and not career_has_ai(cand):
            no_ai_top50.append(r)

    if no_ai_top50:
        report.append(
            warn(f"Top 50: {len(no_ai_top50)} candidate(s) have NO AI/ML keywords in career history.")
        )
    else:
        report.append(ok("Top 50: All candidates have AI/ML keywords in career history."))

    return report, len(no_ai_top10)


def check_reasoning_quality(rows: list, candidates: dict) -> list:
    """Assess the quality of reasoning strings."""
    report = []
    issues = 0

    reasonings = [r["reasoning"] for r in rows]

    # --- Empty reasonings ---
    empty = [r for r in rows if not r["reasoning"].strip()]
    if empty:
        report.append(
            fail(f"{len(empty)} row(s) have empty reasoning.")
        )
        issues += len(empty)
    else:
        report.append(ok("All reasonings are non-empty."))

    # --- Duplicate reasonings ---
    counts = Counter(reasonings)
    dupes = {txt: cnt for txt, cnt in counts.items() if cnt > 1 and txt.strip()}
    total_dupes = sum(v for v in dupes.values())
    if total_dupes > 5:
        report.append(
            fail(f"{total_dupes} reasonings are duplicated across {len(dupes)} unique string(s).")
        )
        for txt, cnt in sorted(dupes.items(), key=lambda x: -x[1])[:3]:
            preview = txt[:80] + "..." if len(txt) > 80 else txt
            report.append(f"    ×{cnt}: \"{preview}\"")
        issues += 1
    elif dupes:
        report.append(
            warn(f"{total_dupes} duplicated reasoning(s) (≤5, acceptable).")
        )
    else:
        report.append(ok("All reasonings are unique."))

    # --- Length statistics ---
    lengths = [len(r) for r in reasonings if r.strip()]
    if lengths:
        avg_len = sum(lengths) / len(lengths)
        min_len = min(lengths)
        max_len = max(lengths)
        report.append(
            ok(f"Reasoning length — avg: {avg_len:.0f} chars, "
               f"min: {min_len}, max: {max_len}")
        )
        if avg_len < 50:
            report.append(warn("Average reasoning is very short (<50 chars)."))
            issues += 1

    # --- Factual grounding sample check ---
    # For the first 10 ranked candidates, check if reasoning mentions something
    # from their profile (name of a skill, their title, or their company).
    ranked = sorted(
        [r for r in rows if r["rank"] is not None],
        key=lambda r: r["rank"],
    )
    sample = ranked[:10]
    grounded = 0
    ungrounded = []

    for r in sample:
        cand = candidates.get(r["candidate_id"])
        if not cand:
            continue
        reasoning_lower = r["reasoning"].lower()
        profile = cand.get("profile", {})

        # Collect profile facts to check against
        facts = []
        facts.append((profile.get("current_title") or "").lower())
        facts.append((profile.get("current_company") or "").lower())
        for s in cand.get("skills", []):
            facts.append(s["name"].lower())
        for j in cand.get("career_history", []):
            facts.append((j.get("title") or "").lower())
            facts.append((j.get("company") or "").lower())

        # A reasoning is "grounded" if it mentions at least one factual element
        facts = [f for f in facts if f and len(f) > 2]  # skip tiny strings
        is_grounded = any(fact in reasoning_lower for fact in facts)

        if is_grounded:
            grounded += 1
        else:
            ungrounded.append(r)

    if sample:
        pct = grounded / len(sample) * 100
        if pct >= 80:
            report.append(ok(f"Factual grounding (top 10 sample): {grounded}/{len(sample)} ({pct:.0f}%)"))
        else:
            report.append(
                warn(f"Factual grounding (top 10 sample): only {grounded}/{len(sample)} ({pct:.0f}%) "
                     "mention profile facts.")
            )
            for r in ungrounded:
                report.append(f"    Rank {r['rank']}: {r['candidate_id']}")
            issues += 1

    return report, issues


def check_behavioral_signals(rows: list, candidates: dict) -> list:
    """Report behavioral signal health for top-10 candidates."""
    report = []
    issues = 0

    ranked = sorted(
        [r for r in rows if r["rank"] is not None],
        key=lambda r: r["rank"],
    )
    top_10 = ranked[:10]

    for r in top_10:
        cid = r["candidate_id"]
        cand = candidates.get(cid)
        if not cand:
            report.append(fail(f"Rank {r['rank']}: {cid} — NOT FOUND in candidates file!"))
            issues += 1
            continue

        profile = cand.get("profile", {})
        signals = cand.get("redrob_signals", {})
        title = profile.get("current_title", "N/A")
        yrs = profile.get("years_of_experience", "?")

        resp_rate = signals.get("recruiter_response_rate", None)
        last_active = signals.get("last_active_date", None)
        interview_rate = signals.get("interview_completion_rate", None)
        open_to_work = signals.get("open_to_work_flag", None)

        days_ago = _days_since(last_active) if last_active else -1

        # Build status string
        parts = [f"{title}, {yrs} yrs"]
        flags = []

        if resp_rate is not None:
            parts.append(f"response_rate={resp_rate:.2f}")
            if resp_rate < 0.2:
                flags.append(f"LOW response rate ({resp_rate:.2f})")
        if days_ago >= 0:
            parts.append(f"active {days_ago}d ago")
            if days_ago > 180:
                flags.append(f"INACTIVE for {days_ago} days")
        if interview_rate is not None:
            parts.append(f"interview_rate={interview_rate:.2f}")
        if open_to_work is not None:
            parts.append(f"open_to_work={'yes' if open_to_work else 'no'}")

        detail = ", ".join(parts)

        if flags:
            issues += 1
            flag_str = "; ".join(flags)
            report.append(
                warn(f"Rank {r['rank']}: {cid} — {detail}  ← {flag_str}")
            )
        else:
            report.append(
                ok(f"Rank {r['rank']}: {cid} — {detail}")
            )

    return report, issues


# ─────────────────────────────────────────────────────────────────────────────
# Candidate existence check
# ─────────────────────────────────────────────────────────────────────────────

def check_candidate_ids(rows: list, candidates: dict) -> list:
    """Ensure all submitted candidate_ids exist in the candidates file."""
    report = []
    missing = []
    for r in rows:
        cid = r["candidate_id"]
        if cid and cid not in candidates:
            missing.append((r["rank"], cid))

    if missing:
        missing.sort(key=lambda x: x[0] if x[0] is not None else 999)
        report.append(
            fail(f"{len(missing)} candidate_id(s) NOT FOUND in candidates file:")
        )
        for rank, cid in missing[:10]:
            report.append(f"    Rank {rank}: {cid}")
        if len(missing) > 10:
            report.append(f"    ... and {len(missing) - 10} more")
    else:
        report.append(ok("All 100 candidate_ids exist in the candidates file."))

    return report, len(missing)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Deep validator for Redrob Hackathon submissions.",
    )
    parser.add_argument(
        "--csv", required=True,
        help="Path to the submission CSV file.",
    )
    parser.add_argument(
        "--candidates", required=True,
        help="Path to candidates.jsonl or candidates.jsonl.gz.",
    )
    args = parser.parse_args()

    total_issues = 0

    # ── Load candidates ────────────────────────────────────────────────────
    print(f"\n{BOLD}Loading candidates from {args.candidates} ...{RESET}", end=" ")
    try:
        candidates = load_candidates(args.candidates)
        print(f"{GREEN}{len(candidates)} loaded.{RESET}")
    except Exception as e:
        print(f"\n{RED}ERROR: Cannot load candidates file: {e}{RESET}")
        sys.exit(1)

    # ── Basic Validation ───────────────────────────────────────────────────
    print(section("BASIC VALIDATION"))

    header, rows, basic_errors = load_submission(args.csv)

    if not basic_errors:
        print(ok(f"Row count: {EXPECTED_ROWS}"))
        print(ok(f"Columns: {', '.join(REQUIRED_HEADER)}"))
        print(ok("Ranks 1-100, all present and unique"))
        print(ok("Scores are non-increasing with rank"))
        print(ok("Candidate IDs: valid format, no duplicates"))
    else:
        for e in basic_errors:
            print(fail(e))
        total_issues += len(basic_errors)

    if not rows:
        print(f"\n{RED}Cannot proceed with deep checks — no valid rows.{RESET}")
        sys.exit(1)

    # ── Candidate ID existence ─────────────────────────────────────────────
    id_report, id_issues = check_candidate_ids(rows, candidates)
    for line in id_report:
        print(line)
    total_issues += id_issues

    # ── Honeypot Check ─────────────────────────────────────────────────────
    print(section("HONEYPOT CHECK"))
    hp_report, hp_issues = check_honeypots(rows, candidates)
    for line in hp_report:
        print(line)
    total_issues += hp_issues

    # ── Keyword Stuffer Check ──────────────────────────────────────────────
    print(section("KEYWORD STUFFER CHECK"))
    ks_report, ks_issues = check_keyword_stuffers(rows, candidates)
    for line in ks_report:
        print(line)
    total_issues += ks_issues

    # ── Career Relevance ───────────────────────────────────────────────────
    print(section("CAREER RELEVANCE CHECK"))
    cr_report, cr_issues = check_career_relevance(rows, candidates)
    for line in cr_report:
        print(line)
    total_issues += cr_issues

    # ── Reasoning Quality ──────────────────────────────────────────────────
    print(section("REASONING QUALITY"))
    rq_report, rq_issues = check_reasoning_quality(rows, candidates)
    for line in rq_report:
        print(line)
    total_issues += rq_issues

    # ── Top-10 Deep Dive ───────────────────────────────────────────────────
    print(section("TOP-10 DEEP DIVE"))
    bs_report, bs_issues = check_behavioral_signals(rows, candidates)
    for line in bs_report:
        print(line)
    total_issues += bs_issues

    # ── Overall Verdict ────────────────────────────────────────────────────
    print(section("OVERALL VERDICT"))
    if total_issues == 0:
        print(f"  {GREEN}{BOLD}✓ PASS — Submission looks good!{RESET}")
    else:
        print(f"  {RED}{BOLD}✗ FAIL — {total_issues} issue(s) found (see above){RESET}")
        sys.exit(1)

    print()  # trailing newline


if __name__ == "__main__":
    main()
