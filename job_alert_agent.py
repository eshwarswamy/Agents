import os
import re
import json
import hashlib
import logging
import feedparser
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from google import genai

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── Candidate Profile ─────────────────────────────────────────────────────────
PROFILE_SUMMARY = """
Senior IT professional, 10+ years experience:
- Core: Java, Spring Boot, Spring Batch, J2EE, Microservices, REST APIs
- IAM: ForgeRock AM/IDM/DS, PingAM, OAuth 2.0, SAML 2.0, LDAP, SSO, RBAC
- Cloud: AWS (EC2, Lambda, SQS, SNS, CloudWatch), Azure, Docker
- Data: Apache Kafka, Elasticsearch, PostgreSQL, MySQL, MSSQL
- Tools: Jenkins CI/CD, Git, SonarQube, JIRA
- Scripting: Python, JavaScript, Bash
- Target: Senior/Lead roles in India or Remote, 10+ years experience
"""

# ── Keyword filters ───────────────────────────────────────────────────────────
MUST_HAVE = [
    "java", "spring", "iam", "forgerock", "pingam",
    "identity", "access management", "backend", "microservice", "spring boot"
]
NICE_TO_HAVE = [
    "aws", "azure", "kafka", "docker", "oauth", "saml",
    "senior", "lead", "architect", "ldap", "sso", "security", "api"
]

# ── Search queries sent to each source ───────────────────────────────────────
QUERIES = [
    "Java Spring Boot IAM",
    "ForgeRock PingAM Identity Access Management",
    "Java Microservices AWS Azure Backend",
    "Spring Boot Senior Backend Engineer",
    "Identity Access Management Java",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ── Source 1: Indeed India RSS (official — most reliable) ─────────────────────

def fetch_indeed(query):
    jobs = []
    url = (
        f"https://in.indeed.com/rss"
        f"?q={query.replace(' ', '+')}&l=India&fromage=1&sort=date"
    )
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries[:15]:
            jobs.append({
                "title":    entry.get("title", "").strip(),
                "company":  entry.get("author", "Unknown").strip(),
                "location": "India",
                "link":     entry.get("link", ""),
                "summary":  BeautifulSoup(entry.get("summary", ""), "html.parser").get_text()[:300],
                "source":   "Indeed",
            })
        log.info(f"Indeed [{query}]: {len(jobs)}")
    except Exception as e:
        log.error(f"Indeed failed [{query}]: {e}")
    return jobs


# ── Source 2: LinkedIn Guest Jobs API (unofficial but widely used) ────────────

def fetch_linkedin(query):
    jobs = []
    url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    params = {
        "keywords": query,
        "location": "India",
        "f_TPR":    "r86400",   # last 24 hours
        "start":    0,
    }
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            log.warning(f"LinkedIn returned HTTP {resp.status_code} for [{query}]")
            return jobs

        soup = BeautifulSoup(resp.text, "html.parser")
        for card in soup.find_all("div", class_="base-card")[:15]:
            title_el   = card.find("h3", class_="base-search-card__title")
            company_el = card.find("h4", class_="base-search-card__subtitle")
            loc_el     = card.find("span", class_="job-search-card__location")
            link_el    = card.find("a", class_="base-card__full-link")

            title = title_el.get_text(strip=True) if title_el else ""
            if title:
                jobs.append({
                    "title":    title,
                    "company":  company_el.get_text(strip=True) if company_el else "Unknown",
                    "location": loc_el.get_text(strip=True) if loc_el else "India",
                    "link":     link_el["href"].split("?")[0] if link_el else "",
                    "summary":  "",
                    "source":   "LinkedIn",
                })
        log.info(f"LinkedIn [{query}]: {len(jobs)}")
    except Exception as e:
        log.error(f"LinkedIn failed [{query}]: {e}")
    return jobs


# ── Source 3: TimesJobs ───────────────────────────────────────────────────────

def fetch_timesjobs(query):
    jobs = []
    url = (
        "https://www.timesjobs.com/candidate/job-search.html"
        f"?searchType=personalizedSearch&from=submit"
        f"&txtKeywords={query.replace(' ', '%20')}"
        f"&txtLocation=india&pDate=I&sequence=1&startPage=1"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        for card in soup.find_all("li", class_="clearfix job-bx wht-shd-bx")[:15]:
            title_el   = card.find("h2")
            company_el = card.find("h3", class_="joblist-comp-name")
            loc_el     = card.find("ul", class_="top-jd-dtl")
            link_el    = title_el.find("a") if title_el else None

            title = link_el.get_text(strip=True) if link_el else ""
            if title:
                jobs.append({
                    "title":    title,
                    "company":  company_el.get_text(strip=True) if company_el else "Unknown",
                    "location": loc_el.get_text(strip=True)[:60] if loc_el else "India",
                    "link":     link_el["href"] if link_el else "",
                    "summary":  "",
                    "source":   "TimesJobs",
                })
        log.info(f"TimesJobs [{query}]: {len(jobs)}")
    except Exception as e:
        log.error(f"TimesJobs failed [{query}]: {e}")
    return jobs


# ── Source 4: Hirist (tech-focused India board) ───────────────────────────────

def fetch_hirist(query):
    jobs = []
    url = f"https://www.hirist.tech/jobs?q={query.replace(' ', '+')}&location=india"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")

        cards = (
            soup.find_all("div", class_=re.compile(r"job.?card", re.I)) or
            soup.find_all("article") or
            soup.find_all("li", class_=re.compile(r"job", re.I))
        )

        for card in cards[:15]:
            link_el    = card.find("a", href=re.compile(r"/j/|/jobs/"))
            title_el   = card.find(["h2", "h3", "h4"])
            company_el = card.find(class_=re.compile(r"company|employer", re.I))

            title = title_el.get_text(strip=True) if title_el else ""
            link  = link_el["href"] if link_el else ""
            if link and link.startswith("/"):
                link = "https://www.hirist.tech" + link

            if title:
                jobs.append({
                    "title":    title,
                    "company":  company_el.get_text(strip=True) if company_el else "Unknown",
                    "location": "India",
                    "link":     link,
                    "summary":  "",
                    "source":   "Hirist",
                })
        log.info(f"Hirist [{query}]: {len(jobs)}")
    except Exception as e:
        log.error(f"Hirist failed [{query}]: {e}")
    return jobs


# ── Deduplication ─────────────────────────────────────────────────────────────

def deduplicate(jobs):
    seen, unique = set(), []
    for j in jobs:
        key = hashlib.md5(
            f"{j['title'].lower().strip()}{j['company'].lower().strip()}".encode()
        ).hexdigest()
        if key not in seen:
            seen.add(key)
            unique.append(j)
    return unique


# ── Keyword scoring (pre-filter before AI to save API calls) ──────────────────

def score_job(job):
    text = f"{job['title']} {job['summary']}".lower()
    score  = sum(3 for kw in MUST_HAVE if kw in text)
    score += sum(1 for kw in NICE_TO_HAVE if kw in text)
    return score


def prefilter(jobs):
    scored   = [(score_job(j), j) for j in jobs]
    filtered = [(s, j) for s, j in scored if s > 0]
    filtered.sort(key=lambda x: x[0], reverse=True)
    return [j for _, j in filtered[:40]]


# ── AI Relevance Ranking ──────────────────────────────────────────────────────

def ai_rank(jobs):
    if not jobs:
        return []

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    jobs_list = "\n".join(
        f"{i+1}. {j['title']} at {j['company']} ({j['location']}) [{j['source']}]"
        for i, j in enumerate(jobs)
    )

    prompt = f"""You are a job matching assistant. Rank these jobs by relevance to this candidate profile.

Candidate Profile:
{PROFILE_SUMMARY}

Job List:
{jobs_list}

Return ONLY a JSON array of job numbers (1-based), best match first, top 15 only.
Example: [3, 7, 1, 12, 5]
No explanation. JSON array only."""

    try:
        interaction = client.interactions.create(
            model="gemini-3.5-flash-lite",
            input=prompt,
        )
        match = re.search(r'\[[\d,\s]+\]', interaction.output_text)
        if match:
            indices = json.loads(match.group())
            ranked  = [jobs[i - 1] for i in indices if 1 <= i <= len(jobs)]
            log.info(f"AI ranked {len(ranked)} jobs")
            return ranked[:15]
    except Exception as e:
        log.error(f"AI ranking failed, using keyword order: {e}")

    return jobs[:15]


# ── Telegram Message Formatting ───────────────────────────────────────────────

def format_message(jobs, date_str):
    if not jobs:
        return (
            f"*Job Alert — {date_str}*\n\n"
            "No matching jobs found in the last 24 hours. Will check again tomorrow."
        )

    by_source = {}
    for j in jobs:
        by_source.setdefault(j["source"], []).append(j)

    source_icons = {"Indeed": "🟦", "LinkedIn": "🔷", "TimesJobs": "🟧", "Hirist": "🟩"}

    lines = [
        f"*Job Alert — {date_str}*",
        f"_{len(jobs)} relevant jobs across {len(by_source)} sources_\n",
    ]

    for source, items in by_source.items():
        icon = source_icons.get(source, "📌")
        lines.append(f"\n{icon} *{source}* ({len(items)} jobs)")
        for j in items:
            title   = j["title"].replace("*", "").replace("[", "(").replace("]", ")")
            company = j["company"].replace("*", "").strip()
            loc     = j["location"].strip()[:50]
            link    = j["link"]

            if link:
                lines.append(f"• [{title}]({link})\n  🏢 {company}  📍 {loc}")
            else:
                lines.append(f"• *{title}*\n  🏢 {company}  📍 {loc}")

    lines.append("\n_Filtered for your Java/IAM/ForgeRock/Cloud profile_")
    return "\n".join(lines)


# ── Telegram Sender ───────────────────────────────────────────────────────────

def send_telegram(text):
    token   = os.environ["JOBS_BOT_TOKEN"]
    chat_id = os.environ["JOBS_CHAT_ID"]
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"

    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        resp = requests.post(api_url, json={
            "chat_id":                  chat_id,
            "text":                     chunk,
            "parse_mode":               "Markdown",
            "disable_web_page_preview": False,
        }, timeout=15)
        resp.raise_for_status()

    log.info("Telegram: sent")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info("=== Job Alert Agent starting ===")

    all_jobs = []

    # Indeed — run all queries (most reliable source)
    for query in QUERIES:
        all_jobs.extend(fetch_indeed(query))

    # LinkedIn — limit to 3 queries (slower, unofficial)
    for query in QUERIES[:3]:
        all_jobs.extend(fetch_linkedin(query))

    # TimesJobs + Hirist — 2 queries each (best-effort scrapers)
    for query in QUERIES[:2]:
        all_jobs.extend(fetch_timesjobs(query))
        all_jobs.extend(fetch_hirist(query))

    log.info(f"Raw total: {len(all_jobs)}")

    unique   = deduplicate(all_jobs)
    log.info(f"After dedup: {len(unique)}")

    filtered = prefilter(unique)
    log.info(f"After keyword filter: {len(filtered)}")

    top_jobs = ai_rank(filtered)

    date_str = datetime.utcnow().strftime("%B %d, %Y")
    message  = format_message(top_jobs, date_str)

    send_telegram(message)
    log.info("=== Job Alert Agent complete ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log.critical(f"Fatal: {exc}")
        raise SystemExit(1)
