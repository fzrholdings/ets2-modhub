import os
import json
import re
import time
import cloudscraper
import requests
import logging
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

MODS_JSON_PATH = "mods.json"
TEMP_JSON_PATH = "mods.json.tmp"
BASE_SITEMAP = "https://www.ets2world.com/sitemap.xml"

def extract_mod_page_data(page_url):
    """Fetch a mod page and extract all relevant fields."""
    scraper = cloudscraper.create_scraper()
    try:
        response = scraper.get(page_url, timeout=30, headers={'Referer': 'https://www.ets2world.com/'})
        if response.status_code != 200:
            return None, None, None, None, None, None
        html = response.text
    except Exception as e:
        logging.warning(f"Request failed: {e}")
        return None, None, None, None, None, None

    soup = BeautifulSoup(html, 'html.parser')

    # ----- 1. modsfile.com download link -----
    download_link = None
    match = re.search(r'href=["\'](https?://modsfile\.com/[^"\']+)["\']', html, re.IGNORECASE)
    if match:
        download_link = match.group(1)
    else:
        match2 = re.search(r'https?://modsfile\.com/[^\s"\']+', html)
        if match2:
            download_link = match2.group(0)

    # ----- 2. Image URL (og:image first, then thumbnail1, then wp-post-image) -----
    image_url = ''
    og_image = soup.find('meta', property='og:image')
    if og_image and og_image.get('content'):
        candidate = og_image['content'].strip()
        if not candidate.startswith('http'):
            candidate = urljoin(page_url, candidate)
        image_url = candidate
    if not image_url:
        thumbnail = soup.find('div', class_='thumbnail1')
        if thumbnail:
            img = thumbnail.find('img')
            if img and img.get('src'):
                candidate = img['src'].strip()
                if not candidate.startswith('http'):
                    candidate = urljoin(page_url, candidate)
                image_url = candidate
    if not image_url:
        post_img = soup.find('img', class_='wp-post-image')
        if post_img and post_img.get('src'):
            candidate = post_img['src'].strip()
            if not candidate.startswith('http'):
                candidate = urljoin(page_url, candidate)
            image_url = candidate

    # ----- 3. Description (from .entry-inner paragraphs) -----
    description_parts = []
    entry_inner = soup.find('div', class_='entry-inner') or soup.find('div', class_='entry')
    if entry_inner:
        for p in entry_inner.find_all('p'):
            text = p.get_text(strip=True)
            if text and len(text) > 20:
                description_parts.append(text)
    if not description_parts:
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc and meta_desc.get('content'):
            description_parts = [meta_desc['content']]
    description = '\n\n'.join(description_parts[:5])
    description = re.sub(r'\n{3,}', '\n\n', description)
    if len(description) > 800:
        description = description[:797] + '...'

    # ----- 4. Category (breadcrumbs, meta, or URL guess) -----
    category = 'ETS2 Mod'
    breadcrumb = soup.find('div', class_='breadcrumbs')
    if breadcrumb:
        links = breadcrumb.find_all('a')
        if len(links) >= 2:
            category = links[1].get_text(strip=True)
    else:
        cat_meta = soup.find('meta', property='article:section')
        if cat_meta and cat_meta.get('content'):
            category = cat_meta['content']
    # Fallback: guess from URL
    if '/ets2-trucks/' in page_url:
        category = 'ETS2 Trucks'
    elif '/ets2-parts-tuning/' in page_url:
        category = 'ETS2 Parts/Tuning'
    elif '/ets2-trailers/' in page_url:
        category = 'ETS2 Trailers'
    elif '/ets2-cars/' in page_url:
        category = 'ETS2 Cars'
    elif '/ets2-buses/' in page_url:
        category = 'ETS2 Buses'
    elif '/ets2-other/' in page_url:
        category = 'ETS2 Other'

    # ----- 5. Game version (find first version number like 1.59, 1.58, etc.) -----
    game_version = '1.59'
    version_pattern = re.compile(r'(\d+\.\d+(?:\.\d+)?)')
    if entry_inner:
        text_sample = entry_inner.get_text()[:2000]
        versions = version_pattern.findall(text_sample)
        for v in versions:
            if v.startswith('1.') and len(v) >= 4:
                game_version = v
                break
    ver_meta = soup.find('meta', attrs={'name': 'game-version'})
    if ver_meta and ver_meta.get('content'):
        game_version = ver_meta['content']

    # ----- 6. Author -----
    author = 'ETS2World'
    author_span = soup.find('span', class_='author vcard')
    if author_span:
        author = author_span.get_text(strip=True)
    else:
        author_link = soup.find('a', rel='author')
        if author_link:
            author = author_link.get_text(strip=True)
    author_meta = soup.find('meta', attrs={'name': 'author'})
    if author_meta and author_meta.get('content'):
        author = author_meta['content']

    return download_link, image_url, description, category, game_version, author

def get_all_mod_urls():
    """Collect all post URLs from post-sitemap*.xml files."""
    scraper = cloudscraper.create_scraper()
    try:
        resp = scraper.get(BASE_SITEMAP, timeout=30)
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.content)
        ns = {'s': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        sitemaps = root.findall('s:sitemap', ns)
        all_urls = []
        for sitemap in sitemaps:
            loc = sitemap.find('s:loc', ns).text
            if 'post-sitemap' in loc:
                logging.info(f"Processing sitemap: {loc}")
                sitemap_resp = scraper.get(loc, timeout=30)
                if sitemap_resp.status_code == 200:
                    sitemap_root = ET.fromstring(sitemap_resp.content)
                    urls = [url.text for url in sitemap_root.findall('s:url/s:loc', ns)]
                    all_urls.extend(urls)
                time.sleep(0.5)
        return all_urls
    except Exception as e:
        logging.error(f"Sitemap error: {e}")
        return []

def save_mods(all_mods, final=False):
    """Save mods to JSON (temporary or final)."""
    temp_file = MODS_JSON_PATH if final else TEMP_JSON_PATH
    with open(temp_file, 'w', encoding='utf-8') as f:
        json.dump(all_mods, f, indent=2, ensure_ascii=False)
    if not final:
        logging.info(f"💾 Intermediate save: {len(all_mods)} mods saved to {temp_file}")
    else:
        logging.info(f"🎉 Final save: {len(all_mods)} mods saved to {temp_file}")

def sync_mods():
    """Main sync function: iterate over all mod pages, extract data, save every 100 mods."""
    logging.info("🟢 Starting full mod sync (incremental save every 100 mods)...")
    mod_urls = get_all_mod_urls()
    exclude_keywords = ['/tag/', '/author/', '/category/', '/page/', '/feed/']
    mod_urls = [url for url in mod_urls if not any(k in url for k in exclude_keywords)]
    total = len(mod_urls)
    logging.info(f"📄 Found {total} mod pages to process")

    all_mods = []
    for idx, url in enumerate(mod_urls, 1):
        logging.info(f"🔍 [{idx}/{total}] Processing: {url}")
        dl, img, desc, cat, ver, auth = extract_mod_page_data(url)
        if not dl:
            continue
        title = url.split('/')[-2].replace('-', ' ').title()
        doc_id = re.sub(r'[^a-zA-Z0-9]', '_', url)[:100]
        all_mods.append({
            'id': doc_id,
            'name': title,
            'category': cat,
            'gameVersion': ver,
            'author': auth,
            'downloadUrl': dl,
            'modsfileUrl': dl,
            'imageUrl': img,
            'description': desc,
            'sourceUrl': url,
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        })
        # Save every 100 mods (adjust if needed)
        if idx % 100 == 0:
            save_mods(all_mods, final=False)
        time.sleep(0.5)   # polite delay

    save_mods(all_mods, final=True)
    # If temporary file exists, replace final with it (already done)
    if os.path.exists(TEMP_JSON_PATH):
        os.replace(TEMP_JSON_PATH, MODS_JSON_PATH)
    logging.info("✅ Sync complete.")

if __name__ == "__main__":
    sync_mods()