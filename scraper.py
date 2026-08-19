import re
import logging
import asyncio
from urllib.parse import urlparse, urljoin, unquote
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Expanded directory platforms to filter out, ensuring only direct local businesses are targeted
EXCLUDED_DOMAINS = {
    "yelp.com", "yellowpages.com", "angi.com", "expertise.com", "tripadvisor.com",
    "facebook.com", "linkedin.com", "instagram.com", "twitter.com", "youtube.com",
    "consumeraffairs.com", "groupon.com", "mapquest.com", "foursquare.com",
    "wikipedia.org", "houzz.com", "homeadvisor.com", "yahoo.com", "yahooapis.com",
    "yimg.com", "whatclinic.com", "londonjourney.co.uk", "topdoctors.co.uk",
    "dentaly.org", "alldentists.co.uk", "uservoice.com", "topdoctors.com",
    "nhs.uk", "healthgrades.com", "zocdoc.com", "webmd.com", "clevelandclinic.org",
    "booking.com", "expedia.com", "justdial.com", "indiamart.com", "homeguide.com",
    "here.com", "localmovers.com", "hireahelper.com", "imoving.com", "movebuddha.com",
    "goflex.com", "moving.com", "greatguysmovers.com", "mymove.com", "updater.com",
    "movingcompanyreviews.com"
}

# Regex patterns for contact scraping
EMAIL_REGEX = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
PHONE_REGEX = re.compile(r'(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{4}')

# JSON-LD Schema structured data matchers
SCHEMA_PHONE_REGEX = re.compile(r'"telephone"\s*:\s*"([^"]+)"', re.IGNORECASE)
SCHEMA_EMAIL_REGEX = re.compile(r'"email"\s*:\s*"([^"]+)"', re.IGNORECASE)

def clean_company_name(url: str) -> str:
    """Derives a clean company name from the website URL."""
    try:
        domain = urlparse(url).netloc
        if domain.startswith("www."):
            domain = domain[4:]
        parts = domain.split(".")
        return parts[0].replace("-", " ").replace("_", " ").title()
    except Exception:
        return "Local Business"

def clean_whatsapp_number(phone: str, website: str = "") -> str:
    """Formats phone numbers with correct international codes for WhatsApp."""
    cleaned = re.sub(r'\D', '', phone)
    if not cleaned:
        return ""
    
    domain = urlparse(website).netloc.lower()
    
    # UK (.uk, .co.uk) -> replace leading 0 with 44
    if domain.endswith('.uk') or '.co.uk' in domain:
        if cleaned.startswith('0') and not cleaned.startswith('00'):
            cleaned = '44' + cleaned[1:]
        elif not cleaned.startswith('44'):
            cleaned = '44' + cleaned
            
    # Australia (.au, .com.au) -> replace leading 0 with 61
    elif domain.endswith('.au') or '.com.au' in domain:
        if cleaned.startswith('0') and not cleaned.startswith('00'):
            cleaned = '61' + cleaned[1:]
        elif not cleaned.startswith('61'):
            cleaned = '61' + cleaned
            
    # US/Canada/Caribbean -> if length is 10 (local number), prepend 1
    else:
        if len(cleaned) == 10:
            cleaned = '1' + cleaned
            
    return cleaned

def query_yahoo(query: str) -> str:
    """Synchronous fetch of Yahoo Search using HTTP/2.
    This prevents HTTP/2 stream multiplexing issues in async/anyio loop.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1'
    }
    # Exclude directories directly in the search query to force Yahoo to return actual business sites
    exclude_suffix = (
        " -site:yelp.com -site:yellowpages.com -site:angi.com -site:expertise.com "
        "-site:consumeraffairs.com -site:plumbersofamerica.com -site:plumbersden.com "
        "-site:homeadvisor.com -site:tripadvisor.com -site:thumbtack.com -site:houzz.com "
        "-site:whatclinic.com -site:londonjourney.co.uk -site:topdoctors.co.uk "
        "-site:dentaly.org -site:alldentists.co.uk -site:nhs.uk -site:webmd.com "
        "-site:healthgrades.com -site:zocdoc.com -site:clevelandclinic.org -site:booking.com "
        "-site:expedia.com -site:justdial.com -site:indiamart.com -site:facebook.com "
        "-site:linkedin.com -site:instagram.com -site:twitter.com -site:youtube.com "
        "-site:homeguide.com -site:here.com -site:localmovers.com -site:hireahelper.com "
        "-site:imoving.com -site:movebuddha.com -site:goflex.com -site:moving.com "
        "-site:greatguysmovers.com -site:mymove.com -site:updater.com -site:movingcompanyreviews.com"
    )
    enriched_query = query.strip() + exclude_suffix
    encoded_query = enriched_query.replace(" ", "+")
    url = f"https://search.yahoo.com/search?p={encoded_query}"
    
    with httpx.Client(http2=True, headers=headers) as client:
        response = client.get(url, timeout=10.0)
        if response.status_code != 200:
            raise Exception(f"Yahoo returned status code {response.status_code}")
        return response.text

def decode_cloudflare_email(cf_hex: str) -> str:
    """Decodes a Cloudflare obfuscated email string."""
    try:
        k = int(cf_hex[:2], 16)
        email = ''.join([chr(int(cf_hex[i:i+2], 16) ^ k) for i in range(2, len(cf_hex), 2)])
        return email.strip()
    except Exception:
        return ""

def is_valid_email(email: str) -> bool:
    """Validates if the scraped email is a real valid B2B email and not a scrambled string."""
    if not email or "@" not in email:
        return False
        
    email = email.lower().strip()
    
    # 1. Filter out common asset files matched by regex
    if email.endswith(('.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.css', '.js', '.pdf', '.zip', '.ico', '.pngoffice')):
        return False
        
    # 2. Check for suspicious scrambled substrings (e.g. ROT-13 signatures like 'znvq2pyrna' or '.pb.hx')
    if "znvq" in email or "pyrna" in email or ".pb.hx" in email or "freivprf" in email:
        return False
        
    # 3. Check for valid TLD (Top Level Domain) to filter out garbage domains like '.hxoffice' or '.pb'
    parts = email.split("@")
    if len(parts) != 2:
        return False
    domain = parts[1]
    
    if "." not in domain:
        return False
        
    tld = domain.split(".")[-1]
    # Whitelist of common valid TLDs for B2B contacts
    VALID_TLDS = {
        "com", "net", "org", "co", "uk", "ca", "us", "au", "nz", "de", "fr", "it", "es", "nl", "ie", "eu",
        "agency", "business", "cleaning", "plumbing", "services", "info", "biz", "pro", "solutions",
        "scot", "wales", "london", "care", "com.au", "co.uk", "or.jp", "ne.jp", "ac.uk", "org.uk", "com.ca"
    }
    
    domain_parts = domain.split(".")
    if len(domain_parts) >= 2:
        double_tld = ".".join(domain_parts[-2:])
        if double_tld in VALID_TLDS:
            return True
            
    return tld in VALID_TLDS

async def fetch_website_contacts(client: httpx.AsyncClient, url: str) -> dict:
    """Fetches website homepage, searches for contact pages, and extracts emails, phone numbers, and WhatsApp links."""
    try:
        response = await client.get(url, timeout=5.0)
        if response.status_code != 200:
            return {"email": "Not found", "phone": "Not found", "whatsapp": "Not found"}

        html = response.text
        soup = BeautifulSoup(html, 'html.parser')
        
        # Inner helper function to parse details from a page's HTML and text
        def extract_info_from_page(page_soup, page_html, page_text):
            emails = []
            
            # 1. Decode Cloudflare Obfuscated Emails first
            # Check for data-cfemail attributes
            for el in page_soup.find_all(attrs={"data-cfemail": True}):
                cf_hex = el.get("data-cfemail", "")
                decoded = decode_cloudflare_email(cf_hex)
                if decoded and is_valid_email(decoded) and decoded not in emails:
                    emails.append(decoded)
                    
            # Check for /cdn-cgi/l/email-protection hrefs
            for a in page_soup.find_all('a', href=True):
                href = a['href']
                if '/cdn-cgi/l/email-protection#' in href:
                    cf_hex = href.split('#')[-1]
                    decoded = decode_cloudflare_email(cf_hex)
                    if decoded and is_valid_email(decoded) and decoded not in emails:
                        emails.append(decoded)
            
            # 2. Parse emails from raw HTML, clean text, and schema scripts
            html_emails = EMAIL_REGEX.findall(page_html)
            text_emails = EMAIL_REGEX.findall(page_text)
            schema_emails = SCHEMA_EMAIL_REGEX.findall(page_html)
            
            for e in list(set(html_emails + text_emails + schema_emails)):
                if is_valid_email(e) and e not in emails:
                    emails.append(e)
            
            # 3. Parse phone numbers from clean text and schema scripts
            text_phones = PHONE_REGEX.findall(page_text)
            schema_phones = SCHEMA_PHONE_REGEX.findall(page_html)
            phones = list(set(text_phones + schema_phones))
            
            whatsapp = None
            
            # 4. Parse explicit anchor link tags (tel, mailto, wa.me)
            for a in page_soup.find_all('a', href=True):
                href = a['href']
                if href.startswith('mailto:'):
                    email = href.replace('mailto:', '').split('?')[0].strip()
                    # Decode mailto Cloudflare redirects if any
                    if '/cdn-cgi/l/email-protection#' in email:
                        cf_hex = email.split('#')[-1]
                        email = decode_cloudflare_email(cf_hex)
                    if email and is_valid_email(email) and email not in emails:
                        emails.append(email)
                elif href.startswith('tel:'):
                    phone = href.replace('tel:', '').strip()
                    if phone and phone not in phones:
                        phones.append(phone)
                elif 'wa.me' in href.lower() or 'api.whatsapp.com' in href.lower() or 'send?phone' in href.lower():
                    whatsapp = href.strip()
            
            return emails, phones, whatsapp

        # 1. Parse homepage
        emails, phones, whatsapp = extract_info_from_page(soup, html, soup.get_text())
        
        # 2. Look for contact/about page link
        contact_url = None
        for a in soup.find_all('a', href=True):
            href = a['href']
            link_text = a.text.strip().lower()
            href_lower = href.lower()
            
            if ('contact' in href_lower or 'contact' in link_text or 
                'about' in href_lower or 'about' in link_text or 
                'info' in href_lower or 'info' in link_text):
                contact_url = urljoin(url, href)
                break
                
        # 3. If contact page found and some info (email or phone) is missing, crawl it
        if contact_url and (not emails or not phones):
            logger.info(f"Crawling contact page to enrich leads: {contact_url}")
            try:
                contact_response = await client.get(contact_url, timeout=5.0)
                if contact_response.status_code == 200:
                    c_html = contact_response.text
                    c_soup = BeautifulSoup(c_html, 'html.parser')
                    c_emails, c_phones, c_whatsapp = extract_info_from_page(c_soup, c_html, c_soup.get_text())
                    
                    # Merge findings
                    for e in c_emails:
                        if e not in emails:
                            emails.append(e)
                    for p in c_phones:
                        if p not in phones:
                            phones.append(p)
                    if not whatsapp and c_whatsapp:
                        whatsapp = c_whatsapp
            except Exception as e:
                logger.debug(f"Failed to fetch contact page {contact_url}: {e}")

        email_result = emails[0] if emails else "Not found"
        phone_result = phones[0] if phones else "Not found"
        
        # Generate WhatsApp link if not explicitly found
        if not whatsapp and phone_result != "Not found":
            wa_num = clean_whatsapp_number(phone_result, url)
            if wa_num:
                whatsapp = f"https://wa.me/{wa_num}"
                
        return {
            "email": email_result,
            "phone": phone_result,
            "whatsapp": whatsapp if whatsapp else "Not found"
        }
    except Exception as e:
        logger.debug(f"Failed to fetch contacts for {url}: {e}")
        return {"email": "Not found", "phone": "Not found", "whatsapp": "Not found"}

async def search_leads(query: str, limit: int = 5) -> list:
    """Searches Yahoo and extracts contact info from top websites.
    Filters out completely empty leads and keeps searching until limit is reached.
    """
    try:
        # Run synchronous Yahoo fetch in a background thread to prevent blocking the event loop
        html = await asyncio.to_thread(query_yahoo, query)
        
        soup = BeautifulSoup(html, 'html.parser')
        target_urls = []
        
        for a in soup.find_all('a'):
            href = a.get('href', '')
            if 'r.search.yahoo.com' in href:
                for part in href.split('/'):
                    if part.startswith('RU='):
                        real_url = unquote(part[3:])
                        if real_url:
                            domain = urlparse(real_url).netloc.lower()
                            # Filter directories
                            if not any(excluded in domain for excluded in EXCLUDED_DOMAINS):
                                if real_url not in target_urls:
                                    target_urls.append(real_url)
                                    if len(target_urls) >= 20: # Fetch a larger pool of potential sites
                                        break
                if len(target_urls) >= 20:
                    break
        
        leads = []
        # Custom realistic browser headers for fetching individual websites
        browser_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive',
        }
        
        lead_index = 1
        async with httpx.AsyncClient(headers=browser_headers, follow_redirects=True) as client:
            for website in target_urls:
                if len(leads) >= limit:
                    break
                    
                name = clean_company_name(website)
                logger.info(f"Scraping contact info from: {website}")
                contacts = await fetch_website_contacts(client, website)
                
                # Verify that the lead has at least one contact channel (email or phone)
                has_email = contacts["email"] != "Not found"
                has_phone = contacts["phone"] != "Not found"
                
                if has_email or has_phone:
                    leads.append({
                        "id": lead_index,
                        "name": name,
                        "website": website,
                        "email": contacts["email"],
                        "phone": contacts["phone"],
                        "whatsapp": contacts["whatsapp"]
                    })
                    lead_index += 1
                else:
                    logger.info(f"Skipping completely empty lead: {website}")
            
            return leads
            
    except Exception as e:
        logger.error(f"Error executing search_leads: {e}")
        return []
