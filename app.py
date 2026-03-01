#!/usr/bin/env python3
"""
Shopify App Reviews Scraper - Web App
Run with: python3 app.py
Then open: http://localhost:5000
"""
import csv
import io
import re
import time
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, jsonify, Response
from openpyxl import Workbook

app = Flask(__name__)
BASE_URL = "https://apps.shopify.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def extract_slug(input_str):
    """Extract the app slug from a URL or return it directly."""
    input_str = input_str.strip().rstrip("/")
    match = re.search(r"apps\.shopify\.com/([^/?#]+)", input_str)
    if match:
        return match.group(1)
    return input_str


def parse_rating(review_el):
    rating_el = review_el.find(attrs={"aria-label": re.compile(r"out of 5 stars")})
    if rating_el:
        match = re.search(r"(\d+)", rating_el["aria-label"])
        if match:
            return int(match.group(1))
    return None


def parse_review(review_el):
    author_el = review_el.select_one("div.tw-text-heading-xs")
    author = author_el.get_text(strip=True) if author_el else "Unknown"
    rating = parse_rating(review_el)
    date_el = review_el.select_one("div.tw-text-body-xs.tw-text-fg-tertiary")
    date = date_el.get_text(strip=True) if date_el else "Unknown"
    body_el = review_el.select_one("[data-truncate-content-copy], [data-truncate-review]")
    body = ""
    if body_el:
        for btn in body_el.find_all("button"):
            btn.decompose()
        body = body_el.get_text(strip=True)
    helpful_el = review_el.select_one(".review-helpfulness__helpful-count")
    helpful_count = 0
    if helpful_el:
        text = helpful_el.get_text(strip=True)
        match = re.search(r"(\d+)", text)
        if match:
            helpful_count = int(match.group(1))
    reply_el = review_el.select_one("[data-reply-id]")
    developer_reply = ""
    if reply_el:
        for btn in reply_el.find_all("button"):
            btn.decompose()
        developer_reply = reply_el.get_text(strip=True)
    return {
        "author": author,
        "rating": rating,
        "date": date,
        "body": body,
        "helpful_count": helpful_count,
        "developer_reply": developer_reply,
    }


def fetch_page(session, url):
    for attempt in range(3):
        try:
            resp = session.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                return resp.text
            elif resp.status_code == 403:
                return None
            elif resp.status_code == 429:
                time.sleep(2 ** (attempt + 1))
                continue
            else:
                return None
        except requests.RequestException:
            time.sleep(2 ** (attempt + 1))
    return None


def scrape_app_info(soup):
    title_el = soup.select_one("h1")
    title = title_el.get_text(strip=True) if title_el else "Unknown App"
    overall_rating_el = soup.select_one("#reviews-link, [data-review-count]")
    review_count_text = overall_rating_el.get_text(strip=True) if overall_rating_el else ""
    count_match = re.search(r"([\d,]+)\s+total\s+reviews", review_count_text, re.IGNORECASE)
    if not count_match:
        all_nums = re.findall(r"[\d,]+", review_count_text)
        count_match_val = int(all_nums[-1].replace(",", "")) if all_nums else 0
    else:
        count_match_val = int(count_match.group(1).replace(",", ""))
    return {"title": title, "total_reviews": count_match_val}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/scrape", methods=["POST"])
def scrape():
    data = request.get_json()
    url_input = data.get("url", "").strip()
    max_pages = data.get("max_pages", 5)
    if not url_input:
        return jsonify({"error": "Please enter a Shopify app URL"}), 400
    slug = extract_slug(url_input)
    if not slug or not re.match(r"^[a-zA-Z0-9\-]+$", slug):
        return jsonify({"error": "Invalid Shopify app URL or slug"}), 400
    session = requests.Session()
    all_reviews = []
    app_info = {"title": slug, "total_reviews": 0}
    page = 1
    while page <= max_pages:
        url = f"{BASE_URL}/{slug}/reviews?page={page}"
        html = fetch_page(session, url)
        if not html:
            if page == 1:
                return jsonify({
                    "error": "Could not fetch reviews. Shopify may be blocking this request. "
                             "Make sure you're running this on your local machine (not a cloud server)."
                }), 403
            break
        soup = BeautifulSoup(html, "lxml")
        if page == 1:
            app_info = scrape_app_info(soup)
        review_elements = soup.select("[data-merchant-review]")
        if not review_elements:
            break
        for el in review_elements:
            review = parse_review(el)
            review["page"] = page
            all_reviews.append(review)
        next_link = soup.select_one('[rel="next"]')
        if not next_link:
            break
        page += 1
        time.sleep(1.5)
    return jsonify({
        "app": app_info,
        "reviews": all_reviews,
        "pages_scraped": page if all_reviews else 0,
    })


@app.route("/download/csv", methods=["POST"])
def download_csv():
    data = request.get_json()
    reviews = data.get("reviews", [])
    if not reviews:
        return jsonify({"error": "No reviews to download"}), 400
    output = io.StringIO()
    fieldnames = ["author", "rating", "date", "body", "helpful_count", "developer_reply"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for r in reviews:
        writer.writerow({k: r.get(k, "") for k in fieldnames})
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=reviews.csv"},
    )


@app.route("/download/excel", methods=["POST"])
def download_excel():
    data = request.get_json()
    reviews = data.get("reviews", [])
    if not reviews:
        return jsonify({"error": "No reviews to download"}), 400
    wb = Workbook()
    ws = wb.active
    ws.title = "Reviews"
    headers = ["Author", "Rating", "Date", "Review", "Helpful Count", "Developer Reply"]
    ws.append(headers)
    for r in reviews:
        ws.append([
            r.get("author", ""),
            r.get("rating", ""),
            r.get("date", ""),
            r.get("body", ""),
            r.get("helpful_count", 0),
            r.get("developer_reply", ""),
        ])
    for col in ws.columns:
        max_length = max(len(str(cell.value or "")) for cell in col)
        ws.column_dimensions[col[0].column_letter].width = min(max_length + 2, 60)
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=reviews.xlsx"},
    )


if __name__ == "__main__":
    print("\n Shopify Reviews Scraper")
    print(" Open http://localhost:5000 in your browser\n")
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", debug=True, port=port)
