
import json
import os
import re
import html
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from email.utils import parsedate_to_datetime

CATEGORIES = [
    {
        "name": "Comptabilité & fiscalité",
        "query": "comptabilité OR fiscalité OR TVA France",
    },
    {
        "name": "BTS Comptabilité-Gestion",
        "query": '"BTS Comptabilité Gestion" OR "BTS CG"',
    },
    {
        "name": "Secteur professionnel",
        "query": '"matériel agricole" OR "location matériel agricole" OR concessionnaire agricole France',
    },
]

def clean_text(value):
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()

def read_text(element, tag):
    found = element.find(tag)
    return found.text.strip() if found is not None and found.text else ""

def fetch_category(category):
    params = urllib.parse.urlencode({
        "q": category["query"],
        "hl": "fr",
        "gl": "FR",
        "ceid": "FR:fr",
    })
    url = "https://news.google.com/rss/search?" + params
    request = urllib.request.Request(
        url, headers={"User-Agent": "VeilleIA/1.0"}
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        xml_data = response.read()

    root = ET.fromstring(xml_data)
    articles = []

    for item in root.findall(".//item"):
        title = clean_text(read_text(item, "title"))
        link = read_text(item, "link")
        if not title or not link:
            continue

        date_text = read_text(item, "pubDate")
        try:
            published = parsedate_to_datetime(date_text).isoformat()
        except (ValueError, TypeError, OverflowError):
            published = None

        articles.append({
            "title": title,
            "link": link,
            "description": clean_text(read_text(item, "description")),
            "source": read_text(item, "source") or "Google Actualités",
            "published_at": published,
            "category": category["name"],
        })

    return articles

def summarize_with_gemini(article, api_key):
    prompt = (
        "Tu es un assistant de veille professionnelle pour un étudiant "
        "en BTS Comptabilité-Gestion travaillant dans le secteur du "
        "matériel agricole et de la location. "
        "Résume en français l'actualité suivante en 2 à 4 phrases. "
        "Explique l'information essentielle et son intérêt pratique. "
        "N'invente aucun fait. Si les informations sont insuffisantes, "
        "précise-le. Utilise uniquement le titre et la description fournis.\n\n"
        f"Catégorie : {article['category']}\n"
        f"Titre : {article['title']}\n"
        f"Description : {article['description'][:2500]}"
    )

    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 180,
        },
    }

    request = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/"
        "models/gemini-3.5-flash-lite:generateContent",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.loads(response.read().decode("utf-8"))

    return result["candidates"][0]["content"]["parts"][0]["text"].strip()

def main():
    data_path = Path("data.json")
    previous = {"articles": []}

    if data_path.exists():
        try:
            previous = json.loads(data_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    old_summaries = {
        article["link"]: article.get("summary", "")
        for article in previous.get("articles", [])
        if article.get("link") and article.get("summary")
    }

    all_articles = {}
    errors = []

    for category in CATEGORIES:
        try:
            for article in fetch_category(category):
                all_articles.setdefault(article["link"], article)
            print(f'OK : {category["name"]}')
        except Exception as error:
            errors.append(f'{category["name"]}: {error}')
            print(f'ERREUR : {category["name"]}: {error}')

    if not all_articles:
        raise RuntimeError(
            "Aucun article récupéré. Le fichier existant est conservé. "
            + " | ".join(errors)
        )

    articles = list(all_articles.values())
    articles.sort(
        key=lambda article: article["published_at"] or "",
        reverse=True,
    )

    for article in articles:
        article["summary"] = old_summaries.get(article["link"], "")

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    limit = 8
    summarized = 0

    if not api_key:
        print("ATTENTION : secret GEMINI_API_KEY absent.")
    else:
        for article in articles:
            if summarized >= limit:
                break
            if article["summary"]:
                continue

            try:
                article["summary"] = summarize_with_gemini(
                    article, api_key
                )
                summarized += 1
                print(f"Résumé créé : {article['title']}")
            except Exception as error:
                print(f"Résumé non créé : {error}")

    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "articles": articles,
    }

    data_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"{len(articles)} articles enregistrés.")
    print(f"{summarized} nouveaux résumés générés.")

if __name__ == "__main__":
    main()
