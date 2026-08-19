import logging
import os
import random
import asyncio
import smtplib
import html
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv
from scraper import search_leads
from database import add_marketing_lead, get_unsent_marketing_leads, mark_lead_as_sent

load_dotenv()

logger = logging.getLogger(__name__)

# Predefined targeting targets in USA, Canada, UK, and Europe
MARKETING_NICHES = ["Plumber", "Dentist", "Roofer", "Electrician", "HVAC", "Movers", "Cleaning Service", "Lawyer", "Accountant"]
MARKETING_CITIES = [
    "New York, USA", "Los Angeles, USA", "Chicago, USA", "Houston, USA", "Dallas, USA", 
    "London, UK", "Birmingham, UK", "Manchester, UK",
    "Toronto, Canada", "Vancouver, Canada", 
    "Dublin, Ireland", "Berlin, Germany"
]

# SMTP credentials from environment
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_EMAIL = os.getenv("SMTP_EMAIL")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
BOT_USERNAME = "newledgenuisai_bot"  # Target Telegram bot username

def send_cold_email(target_email: str, company_name: str) -> bool:
    """Sends a personalized HTML cold email to a B2B business lead."""
    if not SMTP_EMAIL or not SMTP_PASSWORD or SMTP_EMAIL == "YOUR_SENDER_EMAIL_HERE":
        logger.warning("SMTP credentials are not configured. Skipping email delivery.")
        return False
        
    try:
        # Create message container
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"Automated B2B Leads Discovery for {company_name}"
        msg['From'] = f"LeadGen Assistant Bot <{SMTP_EMAIL}>"
        msg['To'] = target_email
        
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
          <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333333;">
            <p>Hi <strong>{html.escape(company_name)} Team</strong>,</p>
            
            <p>We discovered your website while researching top service providers in your local area.</p>
            
            <p>We wanted to share a <strong>free, automated B2B tool</strong> that can help your business find fresh customer leads and B2B client contacts instantly.</p>
            
            <p>Our automated Telegram Bot searches the web in real-time, extracts verified phone numbers, public emails, and direct WhatsApp links, compiling them into styled Excel spreadsheets in seconds.</p>
            
            <p style="margin: 25px 0;">
              <a href="https://t.me/{BOT_USERNAME}" 
                 style="background-color: #1B365D; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 4px; font-weight: bold;">
                 👉 Start Free Lead Search Now
              </a>
            </p>
            
            <p>Or copy this link to start: <a href="https://t.me/{BOT_USERNAME}">https://t.me/{BOT_USERNAME}</a></p>
            
            <br>
            <p>Best regards,<br>
            <strong>LeadGen Assistant Bot Team</strong></p>
          </body>
        </html>
        """
        
        msg.attach(MIMEText(text_content, 'plain'))
        msg.attach(MIMEText(html_content, 'html'))
        
        # Connect to SMTP server
        if SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT)
        else:
            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
            server.starttls()
            
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.sendmail(SMTP_EMAIL, target_email, msg.as_string())
        server.quit()
        logger.info(f"Successfully sent cold email to: {target_email}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send email to {target_email}: {e}")
        return False

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
        
        # 2. Send emails to unsent leads (up to 4 per run to prevent SMTP rate-limits)
        unsent = get_unsent_marketing_leads(limit=4)
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
            
        # Run every 1 hour (Sending ~100 emails/day total)
        logger.info("Background Marketing Worker sleeping for 1 hour...")
        await asyncio.sleep(1 * 3600)

async def send_test_email(target_email: str) -> bool:
    """Helper function to test SMTP settings from command line."""
    print(f"Testing SMTP credentials by sending email to {target_email}...")
    success = send_cold_email(target_email, "Test Business Co")
    print("SMTP connection test result:", "SUCCESS" if success else "FAILED")
    return success
