"""Write varied read-only goals per site for teacher-data collection. Uses the TEXT_MODEL helper.

uv run --env-file .env python scripts/gen_tasks.py --per-site 50 > artifacts/tasks.jsonl
"""

import argparse
import json
import os
import sys

from jev_ultrafast import model
from jev_ultrafast.browser import Browser
from jev_ultrafast.model import action_space, json_content, post_json

SITES = [
    "https://en.wikipedia.org/wiki/Main_Page", "https://en.wikivoyage.org/wiki/Main_Page",
    "https://news.ycombinator.com/", "https://www.python.org/", "https://docs.python.org/3/", "https://pypi.org/",
    "https://www.npmjs.com/", "https://crates.io/", "https://developer.mozilla.org/en-US/", "https://arxiv.org/",
    "https://html.duckduckgo.com/html/", "https://openlibrary.org/", "https://www.gutenberg.org/",
    "https://archive.org/", "https://www.w3schools.com/", "https://huggingface.co/models", "https://hub.docker.com/",
    "https://github.com/explore", "https://www.openstreetmap.org/", "https://www.timeanddate.com/",
    "https://www.weather.gov/", "https://www.nasa.gov/", "https://pubmed.ncbi.nlm.nih.gov/",
    "https://www.khanacademy.org/", "https://www.allrecipes.com/", "https://www.bbc.com/news",
    "https://books.toscrape.com/", "https://quotes.toscrape.com/", "https://webscraper.io/test-sites/e-commerce/allinone",
    "https://the-internet.herokuapp.com/", "https://demoqa.com/", "https://www.saucedemo.com/",
    "https://www.google.com/travel/flights?hl=en", "https://www.craigslist.org/", "https://www.imdb.com/",
    "https://www.goodreads.com/", "https://stackoverflow.com/questions", "https://www.rottentomatoes.com/",
    "https://www.coursera.org/", "http://127.0.0.1:8766/fixture.html?scenario=travel",
]

LONG = """
Every goal must need 4-8 actions and combine at least TWO requirements before the final step, for example: search then
apply a filter then open a specific result; fill two fields then submit; pick a dropdown option and a checkbox, then
open an item. Never write a goal that a single click on the final item would satisfy. Name the final item exactly."""

PROMPT = """You write tasks for a browser agent that can click, type, select dropdown options, and scroll.
Given a site's start page (visible text and controls), return JSON {"goals": [...]} with N distinct goals that can
be completed starting from this page. Mix: simple navigation, search then open a specific result, apply filters or
dropdowns, multi-step flows (3-8 actions), and tasks that need scrolling. State concrete values (search terms,
options) in each goal and say where to stop. Read-only only: no login, sign-up, purchase, posting, or personal data.
Each goal is one or two sentences."""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-site", type=int, default=50)
    parser.add_argument("--long", action="store_true", help="multi-constraint goals only")
    parser.add_argument("--sites", help="file of start URLs, one per line (default: built-in list)")
    args = parser.parse_args()
    sites = [u.strip() for u in open(args.sites, encoding="utf-8") if u.strip()] if args.sites else SITES
    model.CLIENT.timeout = 300  # 50 goals in one reply takes longer than a field value
    base = os.environ["TEXT_MODEL_BASE_URL"].rstrip("/")
    for url in sites:
        try:
            browser = Browser(url)
            try:
                page = browser.observe(screenshot=False)
            finally:
                browser.close()
            elements = action_space(page["actions"])[0][:120]
            result = post_json(base + "/chat/completions", os.environ["TEXT_MODEL_API_KEY"], {
                "model": os.environ["TEXT_MODEL"],
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": PROMPT + (LONG if args.long else "")},
                             {"role": "user", "content": json.dumps({
                    "N": args.per_site, "url": page["url"], "title": page["title"], "text": page["text"][:3000],
                    "controls": [f"{e['role']} {e['label']}"[:80] for e in elements]}, ensure_ascii=False)}],
            })
            goals = json_content(result)["goals"]
        except Exception as exc:
            print(f"skip {url}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            continue
        for goal in goals:
            if isinstance(goal, str) and goal.strip():
                print(json.dumps({"url": url, "goal": goal.strip()}, ensure_ascii=False), flush=True)
        print(f"{url}: {len(goals)} goals", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
