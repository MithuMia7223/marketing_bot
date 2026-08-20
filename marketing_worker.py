import logging
import os
import random
import asyncio
import smtplib
import html
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv
import httpx
from scraper import search_leads
from database import add_marketing_lead, get_unsent_marketing_leads, mark_lead_as_sent

load_dotenv()

logger = logging.getLogger(__name__)

# Predefined targeting targets in USA, Canada, UK, and Europe
MARKETING_NICHES = [
    "Plumber", "Dentist", "Roofer", "Electrician", "HVAC", "Movers", "Cleaning Service", 
    "Lawyer", "Accountant", "Real Estate Agent", "Digital Marketing Agency", "Web Designer", 
    "Gym", "Restaurant", "Beauty Salon", "Catering Service", "Auto Repair", "Pest Control", 
    "Architect", "Photographer"
]
MARKETING_CITIES = [
    "New York, USA", "Los Angeles, USA", "Chicago, USA", "Houston, USA", "Dallas, USA", 
    "Phoenix, USA", "Philadelphia, USA", "San Antonio, USA", "San Diego, USA", "San Jose, USA", 
    "London, UK", "Birmingham, UK", "Manchester, UK", "Leeds, UK", "Glasgow, UK", 
    "Toronto, Canada", "Vancouver, Canada", "Montreal, Canada", "Ottawa, Canada", 
    "Sydney, Australia", "Melbourne, Australia", "Brisbane, Australia", "Perth, Australia", 
    "Dublin, Ireland", "Auckland, New Zealand"
]

# SMTP credentials from environment
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_EMAIL = os.getenv("SMTP_EMAIL")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
BOT_USERNAME = "newledgenuisai_bot"  # Target Telegram bot username

# Google Apps Script Web App Config to bypass SMTP Blocks
GOOGLE_SCRIPT_URL = os.getenv("GOOGLE_SCRIPT_URL")
GOOGLE_SCRIPT_TOKEN = os.getenv("GOOGLE_SCRIPT_TOKEN", "MySecretToken123")

def send_via_google_script(target_email: str, company_name: str, subject: str, text_content: str, html_content: str) -> bool:
    """Sends email using Google Apps Script Web App (Port 443) to bypass SMTP blocks."""
    if not GOOGLE_SCRIPT_URL:
        return False
    try:
        payload = {
            "token": GOOGLE_SCRIPT_TOKEN,
            "to": target_email,
            "subject": subject,
            "body": text_content,
            "htmlBody": html_content
        }
        response = httpx.post(GOOGLE_SCRIPT_URL, json=payload, follow_redirects=True, timeout=15.0)
        if response.status_code == 200:
            result = response.json()
            if result.get("status") == "success":
                logger.info(f"Successfully sent email via Google Script API to: {target_email}")
                return True
            else:
                logger.error(f"Google Script returned error status: {result}")
        else:
            logger.error(f"Google Script Web App returned status code {response.status_code}: {response.text}")
    except Exception as e:
        logger.error(f"Failed to send email via Google Script Web App to {target_email}: {e}")
    return False

def send_via_smtp(target_email: str, company_name: str, subject: str, text_content: str, html_content: str) -> bool:
    """Sends email using standard SMTP port 587."""
    if not SMTP_EMAIL or not SMTP_PASSWORD or SMTP_EMAIL == "YOUR_SENDER_EMAIL_HERE":
        logger.warning("SMTP credentials are not configured. Skipping email delivery.")
        return False
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = f"LeadGen Assistant Bot <{SMTP_EMAIL}>"
        msg['To'] = target_email
        
        msg.attach(MIMEText(text_content, 'plain'))
        msg.attach(MIMEText(html_content, 'html'))
        
        if SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        else:
            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
            server.starttls()
            
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.sendmail(SMTP_EMAIL, target_email, msg.as_string())
        server.quit()
        logger.info(f"Successfully sent cold email via SMTP to: {target_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email via SMTP to {target_email}: {e}")
        return False

def send_cold_email(target_email: str, company_name: str) -> bool:
    """Sends a personalized HTML cold email to a B2B business lead."""
    subject = f"🚀 Free B2B Leads Finder for {company_name}"
    
    # Email Body (HTML & Text)
    text_content = (
        f"Hi {company_name} Team,\n\n"
        "We found your website while researching top service companies in your area.\n\n"
        "We wanted to share a free automated tool that can help you find fresh B2B leads, customers, and client contacts instantly.\n\n"
        "Our automated Telegram Bot parses websites, extracts direct phone numbers, email addresses, and WhatsApp links in real-time. You can download styled leads directly into Microsoft Excel spreadsheets.\n\n"
        f"Try it completely free today: https://t.me/{BOT_USERNAME}\n\n"
        "Best regards,\n"
        "LeadGen Assistant Bot Team"
    )
    
    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333333; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e0e0e0; border-radius: 8px;">
        <h2 style="color: #1B365D; border-bottom: 2px solid #1B365D; padding-bottom: 10px;">🚀 Free B2B Lead Generator for {html.escape(company_name)}</h2>
        
        <p>Hi <strong>{html.escape(company_name)} Team</strong>,</p>
        
        <p>We found your business online and noticed you are doing amazing work in your area! To help you get even more clients and grow your revenue, we want to share a <strong>100% free automated tool</strong>.</p>
        
        <p>Our automated B2B Lead Finder searches the web in real-time to extract direct phone numbers, public emails, and WhatsApp contacts of potential B2B clients in any city, exporting them directly into styled <strong>Excel sheets</strong>.</p>
        
        <div style="background-color: #f7f9fc; padding: 15px; border-left: 4px solid #1B365D; margin: 20px 0;">
          <strong>What the Bot does for you:</strong>
          <ul style="margin: 5px 0 0 20px; padding: 0;">
            <li>Scrapes verified emails & phone numbers in real-time.</li>
            <li>Finds active WhatsApp links.</li>
            <li>Downloads clean Excel spreadsheets in seconds.</li>
            <li>Runs completely inside Telegram (No login required!).</li>
          </ul>
        </div>
        
        <p style="text-align: center; margin: 30px 0;">
          <a href="https://t.me/{BOT_USERNAME}" 
             style="background-color: #1B365D; color: #ffffff; padding: 14px 28px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block; font-size: 16px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
             👉 Start Free Lead Search Now
          </a>
        </p>
        
        <p style="text-align: center; font-size: 13px; color: #666666;">
          Or copy-paste this link in your browser: <a href="https://t.me/{BOT_USERNAME}" style="color: #1B365D;">https://t.me/{BOT_USERNAME}</a>
        </p>
        
        <br>
        <p style="border-top: 1px solid #e0e0e0; padding-top: 15px; font-size: 14px; color: #555555;">
          Best regards,<br>
          <strong>LeadGen Assistant Bot Team</strong>
        </p>
      </body>
    </html>
    """
    
    # Send using Google Script Web App if configured (bypasses SMTP blocks)
    if GOOGLE_SCRIPT_URL and GOOGLE_SCRIPT_URL.startswith("https://script.google.com"):
        return send_via_google_script(target_email, company_name, subject, text_content, html_content)
    
    # Fallback to standard SMTP
    return send_via_smtp(target_email, company_name, subject, text_content, html_content)

async def run_marketing_campaign() -> None:
    """Executes a single marketing campaign run: harvests new leads, then sends outreach emails."""
    try:
        # 1. Harvest leads (autonomously pick a random target query)
        niche = random.choice(MARKETING_NICHES)
        city = random.choice(MARKETING_CITIES)
        query = f"{niche} in {city}"
        
        logger.info(f"Background marketing worker: Harvesting leads for '{query}'...")
        # Scrape 10 leads to get a good pool of emails
        leads = await search_leads(query, limit=10)
        
        # Save unique emails to database
        saved_count = 0
        for lead in leads:
            email = lead["email"]
            if email and email != "Not found":
                add_marketing_lead(email, lead["name"], lead["website"])
                saved_count += 1
                
        logger.info(f"Background marketing worker: Harvested {saved_count} new emails for campaign.")
        
        # 2. Send emails to unsent leads (up to 3 per run to prevent SMTP rate-limits)
        unsent = get_unsent_marketing_leads(limit=3)
        if not unsent:
            logger.info("No unsent leads available in database. Skipping outreach.")
            return
            
        for lead in unsent:
            email = lead["email"]
            name = lead["company_name"]
            logger.info(f"Sending outreach email to: {email} ({name})...")
            
            # Send (Runs synchronously in threadpool to prevent SMTP blocking)
            success = await asyncio.to_thread(send_cold_email, email, name)
            if success:
                mark_lead_as_sent(email, "sent")
            else:
                mark_lead_as_sent(email, "failed")
                
    except Exception as e:
        logger.error(f"Error during marketing campaign run: {e}")

async def start_marketing_worker(application) -> None:
    """Starts the background worker scheduler."""
    logger.info("Background Marketing Worker starting...")
    # Initial sleep to allow bot startup updates to settle
    await asyncio.sleep(60)
    
    while True:
        try:
            await run_marketing_campaign()
        except Exception as e:
            logger.error(f"Error in marketing worker execution loop: {e}")
            
        # Run every 15 minutes (Sending ~250 emails/day total)
        logger.info("Background Marketing Worker sleeping for 15 minutes...")
        await asyncio.sleep(15 * 60)

async def send_test_email(target_email: str) -> bool:
    """Helper function to test SMTP settings from command line."""
    print(f"Testing SMTP credentials by sending email to {target_email}...")
    success = send_cold_email(target_email, "Test Business Co")
    print("SMTP connection test result:", "SUCCESS" if success else "FAILED")
    return success
