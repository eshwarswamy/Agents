import os
import feedparser
import requests
import smtplib
import logging
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from google import genai

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

RSS_FEEDS = {
    "World":      "http://feeds.bbci.co.uk/news/world/rss.xml",
    "Technology": "https://feeds.feedburner.com/TechCrunch",
    "Science":    "https://www.sciencedaily.com/rss/top/science.xml",
    "Business":   "https://feeds.reuters.com/reuters/businessNews",
    "AI/ML":      "https://www.marktechpost.com/feed/",
}
MAX_PER_FEED = 5


def fetch_news():
    articles = []
    for category, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:MAX_PER_FEED]:
                title = entry.get("title", "").strip()
                summary = entry.get("summary", "").strip()[:600]
                link = entry.get("link", "")
                if title:
                    articles.append({
                        "category": category,
                        "title": title,
                        "summary": summary,
                        "link": link,
                    })
            log.info(f"[{category}] fetched {min(MAX_PER_FEED, len(feed.entries))} articles")
        except Exception as exc:
            log.error(f"[{category}] fetch failed: {exc}")
    return articles


def summarize(articles):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    articles_block = "\n\n".join(
        f"[{a['category']}] {a['title']}\n{a['summary']}"
        for a in articles
    )

    prompt = f"""You are a news digest assistant. From the articles below create a crisp daily digest.

Format:
- One "Today's Highlights" paragraph (2-3 sentences, big picture)
- Grouped bullet points per category (2-3 bullets each, key facts only)
- A "Key Takeaway" sentence at the end

Limit: 450 words. Tone: professional, neutral.

Articles:
{articles_block}

Digest:"""

    interaction = client.interactions.create(
        model="gemini-3.5-flash-lite",
        input=prompt,
    )
    return interaction.output_text.strip()


def send_telegram(text):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }, timeout=15)
        resp.raise_for_status()

    log.info("Telegram: sent")


def send_email(subject, body):
    sender = os.environ["GMAIL_USER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ["RECIPIENT_EMAIL"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
        server.login(sender, password)
        server.sendmail(sender, recipient, msg.as_string())

    log.info("Email: sent")


def build_links_section(articles):
    by_category = {}
    for a in articles:
        by_category.setdefault(a["category"], []).append(a)

    lines = ["\n\n--- Read More ---"]
    for category, items in by_category.items():
        lines.append(f"\n{category}:")
        for a in items:
            if a["link"]:
                lines.append(f"  • {a['title']}\n    {a['link']}")
    return "\n".join(lines)


def alert_failure(error_msg):
    try:
        send_telegram(f"News Agent Failed\n{error_msg[:300]}")
    except Exception:
        pass


def main():
    log.info("=== News Agent starting ===")

    articles = fetch_news()
    if not articles:
        raise RuntimeError("No articles fetched — all RSS feeds failed")
    log.info(f"Total articles: {len(articles)}")

    digest = summarize(articles)
    date_str = datetime.utcnow().strftime("%B %d, %Y")
    header = f"Daily News Digest — {date_str}\n\n"
    message = header + digest + build_links_section(articles)

    errors = []

    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        try:
            send_telegram(message)
        except Exception as exc:
            errors.append(f"Telegram: {exc}")
            log.error(errors[-1])

    if os.environ.get("GMAIL_USER"):
        try:
            send_email(f"Daily News Digest — {date_str}", message)
        except Exception as exc:
            errors.append(f"Email: {exc}")
            log.error(errors[-1])

    if errors:
        raise RuntimeError(" | ".join(errors))

    log.info("=== News Agent complete ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log.critical(f"Fatal: {exc}")
        alert_failure(str(exc))
        raise SystemExit(1)
