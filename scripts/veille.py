import json
import os
import re
import html
import urllib.parse
import urllib.request
import urllib.error
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from email.utils import parsedate_to_datetime

# Sources sélectionnées pour leur sérieux et leur intérêt professionnel.
# Google Actualités reste le moteur de collecte, mais les recherches sont
# limitées à ces domaines et les titres sont filtrés avant publication.
CATEGORIES = [
    {
        "name": "Comptabilité & fiscalité",
        "query": (
            '(comptabilité OR fiscalité OR TVA OR impôt OR "facturation électronique" '
            'OR "plan comptable" OR URSSAF OR paie) '
            '(site:bofip.impots.gouv.fr OR site:impots.gouv.fr OR site:anc.gouv.fr '
            'OR site:legifrance.gouv.fr OR site:economie.gouv.fr '
            'OR site:compta-online.com OR site:revue-fiduciaire.com OR site:efl.fr '
            'OR site:lesechos.fr OR site:banque-france.fr OR site:insee.fr)'
        ),
        "keywords": (
            "comptab", "fiscal", "tva", "impôt", "impot", "facturation",
            "facture électronique", "plan comptable", "normes comptables",
            "bilan", "résultat", "resultat", "urssaf", "paie", "salaire",
            "déclaration", "declaration", "redressement", "entreprise",
            "loi de finances", "impôt sur", "impot sur", "bofip", "pcg",
            "expert-comptable", "expert comptable", "cotisation"
        ),
        "sources": (
            "bofip", "impots.gouv", "impôts.gouv", "légifrance", "legifrance",
            "autorité des normes comptables", "anc", "ministère de l'économie",
            "ministere de l'economie", "compta online", "compta-online",
            "revue fiduciaire", "efl", "les echos", "les échos",
            "banque de france", "insee"
        ),
    },
    {
        "name": "BTS Comptabilité-Gestion",
        "query": (
            '("BTS comptabilité gestion" OR "BTS CG" OR "comptabilité gestion" '
            'OR "contrôle de gestion" OR alternance OR référentiel OR examen) '
            '(site:compta-online.com OR site:studyrama.com OR site:education.gouv.fr '
            'OR site:eduscol.education.fr OR site:francecompetences.fr '
            'OR site:onisep.fr OR site:apec.fr OR site:insee.fr '
            'OR site:banque-france.fr OR site:economie.gouv.fr)'
        ),
        "keywords": (
            "bts", "comptabilité", "comptabilite", "gestion", "comptable",
            "contrôle de gestion", "controle de gestion", "alternance",
            "apprentissage", "référentiel", "referentiel", "examen",
            "diplôme", "diplome", "formation", "dcg", "dscg", "épreuve",
            "epreuve", "étudiant", "etudiant", "éducation", "education",
            "compétence", "competence", "emploi", "métier", "metier"
        ),
        "sources": (
            "compta online", "compta-online", "studyrama", "éducation nationale",
            "education nationale", "éduscol", "eduscol", "france compétences",
            "france competences", "onisep", "apec", "insee",
            "banque de france", "ministère de l'économie", "ministere de l'economie"
        ),
    },
    {
        "name": "Secteur professionnel",
        "query": (
            '("matériel agricole" OR machinisme OR tracteur OR concessionnaire '
            'OR agroéquipement OR "location de matériel" OR moissonneuse) '
            '(site:terre-net.fr OR site:lafranceagricole.fr OR site:reussir.fr '
            'OR site:materielagricole.com OR site:axema.fr OR site:sedima.fr '
            'OR site:usinenouvelle.com OR site:agri-mutuel.com)'
        ),
        "keywords": (
            "matériel agricole", "materiel agricole", "machinisme", "tracteur",
            "concession", "concessionnaire", "agroéquipement", "agroequipement",
            "location", "moissonneuse", "ensileuse", "récolte", "recolte",
            "agricole", "agroéquipement", "agroequipement", "axema", "sedima",
            "distributeur", "atelier", "pièce détachée", "piece detachee",
            "maintenance", "constructeur", "agriculture", "équipement",
            "equipement", "marché", "marche"
        ),
        "sources": (
            "terre-net", "terre net", "la france agricole", "réussir",
            "reussir", "matériel agricole", "materiel agricole", "axema",
            "sedima", "l'usine nouvelle", "usine nouvelle", "agri-mutuel",
            "agri mutuel"
        ),
    },
]

MAX_ARTICLES_PER_CATEGORY = 100
TARGET_SUMMARIES_PER_CATEGORY = 8
MAX_DESCRIPTION_LENGTH = 2500


def clean_text(value):
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize(value):
    value = (value or "").lower().replace("’", "'")
    return re.sub(r"\s+", " ", value).strip()


def read_text(element, tag):
    found = element.find(tag)
    return found.text.strip() if found is not None and found.text else ""


def matches_category(article, category):
    title = normalize(article.get("title", ""))
    description = normalize(article.get("description", ""))
    source = normalize(article.get("source", ""))
    text = f"{title} {description}"

    # Écarte les résultats dont le titre n'a aucun rapport avec le sujet.
    if not any(keyword in title for keyword in category["keywords"]):
        return False

    # Ne conserve que les médias et organismes de la liste de référence.
    if not any(normalize(name) in source for name in category["sources"]):
        return False

    # Les mots-clés de la catégorie doivent apparaître dans le titre.
    # Cela évite les articles de faits divers qui ne partagent qu'un mot
    # générique comme « France », « entreprise » ou « budget ».
    return True


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

    # Google Actualités peut renvoyer temporairement HTTP 503.
    # On retente plusieurs fois avant de considérer la catégorie indisponible.
    xml_data = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                xml_data = response.read()
            break
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            if attempt == 3:
                raise
            wait_seconds = 2 ** (attempt + 1)
            print(
                f"Flux temporairement indisponible pour {category['name']}. "
                f"Nouvel essai dans {wait_seconds}s ({attempt + 1}/3)."
            )
            time.sleep(wait_seconds)

    if xml_data is None:
        raise RuntimeError("Flux Google Actualités indisponible après plusieurs essais.")

    root = ET.fromstring(xml_data)
    articles = []
    seen = set()

    for item in root.findall(".//item"):
        title = clean_text(read_text(item, "title"))
        link = read_text(item, "link")
        if not title or not link or link in seen:
            continue

        source = read_text(item, "source") or "Google Actualités"
        article = {
            "title": title,
            "link": link,
            "description": clean_text(read_text(item, "description")),
            "source": source,
            "published_at": None,
            "category": category["name"],
        }

        if not matches_category(article, category):
            continue

        date_text = read_text(item, "pubDate")
        try:
            published = parsedate_to_datetime(date_text).isoformat()
        except (ValueError, TypeError, OverflowError):
            published = None
        article["published_at"] = published

        articles.append(article)
        seen.add(link)

        if len(articles) >= MAX_ARTICLES_PER_CATEGORY:
            break

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
        f"Source : {article['source']}\n"
        f"Titre : {article['title']}\n"
        f"Description : {article['description'][:MAX_DESCRIPTION_LENGTH]}"
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

    previous_articles = previous.get("articles", [])

    for category in CATEGORIES:
        try:
            category_articles = fetch_category(category)
            for article in category_articles:
                all_articles.setdefault(article["link"], article)
            print(
                f'OK : {category["name"]} — '
                f'{len(category_articles)} articles retenus après filtrage'
            )
        except Exception as error:
            errors.append(f'{category["name"]}: {error}')
            print(f'ERREUR : {category["name"]}: {error}')

            # En cas de panne du flux, on conserve les articles déjà publiés
            # de cette catégorie au lieu de la faire disparaître du fichier.
            fallback_articles = [
                article for article in previous_articles
                if article.get("category") == category["name"]
            ]
            for article in fallback_articles:
                all_articles.setdefault(article["link"], article)
            print(
                f'Repli : {len(fallback_articles)} anciens articles conservés '
                f'pour {category["name"]}.'
            )

    if not all_articles:
        raise RuntimeError(
            "Aucun article retenu. Le fichier existant est conservé. "
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
    summarized = 0

    if not api_key:
        print("ATTENTION : secret GEMINI_API_KEY absent.")
    else:
        # Chaque catégorie dispose de son propre objectif : une catégorie
        # déjà à 8 résumés ne bloque plus les autres.
        for category in CATEGORIES:
            category_articles = [
                article for article in articles
                if article["category"] == category["name"]
            ]
            already_summarized = sum(
                1 for article in category_articles
                if (article.get("summary") or "").strip()
            )
            remaining = max(
                0, TARGET_SUMMARIES_PER_CATEGORY - already_summarized
            )
            created_for_category = 0

            for article in category_articles:
                if created_for_category >= remaining:
                    break
                if (article.get("summary") or "").strip():
                    continue

                try:
                    summary = summarize_with_gemini(article, api_key)
                    if summary.strip():
                        article["summary"] = summary
                        summarized += 1
                        created_for_category += 1
                        print(f"Résumé créé : {article['title']}")
                except Exception as error:
                    print(
                        f"Résumé non créé ({category['name']}) : {error}"
                    )

            total_for_category = already_summarized + created_for_category
            print(
                f"Objectif {category['name']} : "
                f"{total_for_category}/{TARGET_SUMMARIES_PER_CATEGORY} résumés"
            )

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
