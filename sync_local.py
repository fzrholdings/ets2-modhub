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
PROGRESS_FILE = "mods_progress.txt"

def extract_mod_page_data(page_url):
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

    # ----- 2. Image URL -----
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

    # ----- 3. Description -----
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

    # ----- 4. Category -----
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

    # ----- 5. Game version -----
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

def get_all_mod_urls(use_cache=True, cache_file="mod_urls.txt"):
    if use_cache and os.path.exists(cache_file):
        with open(cache_file, 'r') as f:
            urls = [line.strip() for line in f if line.strip()]
        logging.info(f"Loaded {len(urls)} URLs from cache {cache_file}")
        return urls

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
        
        with open(cache_file, 'w') as f:
            for url in all_urls:
                f.write(url + "\n")
        return all_urls
    except Exception as e:
        logging.error(f"Sitemap error: {e}")
        return []

def save_mods(all_mods, final=False):
    temp_file = MODS_JSON_PATH if final else TEMP_JSON_PATH
    with open(temp_file, 'w', encoding='utf-8') as f:
        json.dump(all_mods, f, indent=2, ensure_ascii=False)
    if not final:
        logging.info(f"💾 Intermediate save: {len(all_mods)} mods saved to {temp_file}")
    else:
        logging.info(f"🎉 Final save: {len(all_mods)} mods saved to {temp_file}")

def sync_mods(start_idx=None):
    logging.info("🟢 Starting mod sync with resume support...")
    
    mod_urls = get_all_mod_urls(use_cache=True)
    exclude_keywords = ['/tag/', '/author/', '/category/', '/page/', '/feed/']
    mod_urls = [url for url in mod_urls if not any(k in url for k in exclude_keywords)]
    total = len(mod_urls)
    
    # Determine starting index
    if start_idx is None and os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r') as f:
            last_url = f.read().strip()
            for i, url in enumerate(mod_urls):
                if url == last_url:
                    start_idx = i + 1
                    break
        if start_idx is None:
            start_idx = 0
    elif start_idx is None:
        start_idx = 0
    
    # Load already processed mods
    all_mods = []
    if os.path.exists(TEMP_JSON_PATH) and start_idx == 0:
        try:
            with open(TEMP_JSON_PATH, 'r', encoding='utf-8') as f:
                all_mods = json.load(f)
                start_idx = len(all_mods)
                logging.info(f"Resuming from index {start_idx} (already have {len(all_mods)} mods)")
        except:
            pass
    
    logging.info(f"Total mod pages: {total}, Starting from: {start_idx+1}")
    
    for idx, url in enumerate(mod_urls[start_idx:], start=start_idx + 1):
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
        
        # Save progress after each successful mod
        with open(PROGRESS_FILE, 'w') as f:
            f.write(url)
        
        if idx % 100 == 0:
            save_mods(all_mods, final=False)
        
        time.sleep(0.5)
    
    save_mods(all_mods, final=True)
    if os.path.exists(TEMP_JSON_PATH):
        os.replace(TEMP_JSON_PATH, MODS_JSON_PATH)
    logging.info("✅ Sync complete.")

if __name__ == "__main__":
    import sys
    start = None
    if len(sys.argv) > 1:
        try:
            start = int(sys.argv[1])
            logging.info(f"Manually set start index to: {start}")
        except:
            pass
    sync_mods(start_idx=start)
