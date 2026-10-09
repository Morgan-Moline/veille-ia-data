
import json
import re
import html
import urllib.parse
import urllib.request
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

def main():
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

    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "articles": articles,
    }

    Path("data.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f'{len(articles)} articles enregistrés dans data.json')

if __name__ == "__main__":
    main()
