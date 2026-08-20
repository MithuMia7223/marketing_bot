import logging
import os
import html
import asyncio
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from scraper import search_leads
from excel_generator import generate_leads_excel
from database import (
    init_db,
    get_search_count,
    increment_search_count,
    get_subscription_status,
    set_subscription_status,
    get_marketing_campaign_stats,
    FREE_LIMIT,
    register_user,
    add_referral,
    get_referral_count
)
from marketing_worker import start_marketing_worker

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Fetch Token
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Payment details
BINANCE_PAY_ID = os.getenv("BINANCE_PAY_ID", "YOUR_BINANCE_PAY_ID_HERE")
USDT_TRC20_ADDRESS = os.getenv("USDT_TRC20_ADDRESS", "YOUR_USDT_TRC20_ADDRESS_HERE")

admin_val = os.getenv("ADMIN_TELEGRAM_ID")
ADMIN_TELEGRAM_ID = int(admin_val) if admin_val and admin_val.isdigit() else None

if not ADMIN_TELEGRAM_ID:
    logger.warning("Warning: ADMIN_TELEGRAM_ID is not set in the .env file. Payment approvals will fail!")

def get_payment_message(current_searches: int, remaining: int, is_limit_reached: bool = False, allowed_limit: int = FREE_LIMIT) -> str:
    """Generates the payment details message based on available environment variables."""
    # Base title
    if is_limit_reached:
        msg = (
            "⚠️ <b>Free Limit Reached!</b>\n\n"
            f"You have used all {allowed_limit} of your free B2B search queries.\n"
            "To unlock <b>Unlimited Searches</b>, please subscribe to one of our Premium Plans below.\n\n"
        )
    else:
        msg = (
            "💳 <b>Your Subscription:</b>\n\n"
            "• Plan: <b>Free Plan</b>\n"
            f"• Usage: {current_searches} of {allowed_limit} searches used ({remaining} remaining).\n\n"
            "💰 <b>Choose a Premium Plan to unlock unlimited searches:</b>\n"
        )
    
    msg += (
        "🌟 <b>Weekly Trial:</b> $5 for 7 Days\n"
        "🚀 <b>Monthly Premium:</b> $15 for 30 Days (50% Launch Week Discount, normally $30)\n\n"
        "💳 <b>Payment Details:</b>\n"
    )
    
    # Details
    details_added = False
    if BINANCE_PAY_ID and BINANCE_PAY_ID != "YOUR_BINANCE_PAY_ID_HERE":
        msg += f"• <b>Binance Pay ID / UID:</b> <code>{html.escape(BINANCE_PAY_ID)}</code>\n"
        details_added = True
        
    if USDT_TRC20_ADDRESS and USDT_TRC20_ADDRESS != "YOUR_USDT_TRC20_ADDRESS_HERE":
        msg += f"• <b>USDT (TRC-20):</b> <code>{html.escape(USDT_TRC20_ADDRESS)}</code>\n"
        details_added = True
        
    # Support link
    msg += "• <b>Support Contact:</b> @Mdmithun731\n"
    
    msg += (
        "\nPlease pay <b>$5</b> or <b>$15</b>. After making the payment, click the button below "
        "to submit your Binance TxID or upload a screenshot of your transaction directly to this bot, "
        "or contact support at @Mdmithun731."
    )
    return msg

# Keyboards
def get_main_keyboard(user_id: int = 0):
    keyboard = [
        [KeyboardButton("🔍 Search Leads"), KeyboardButton("💳 Subscription")],
        [KeyboardButton("ℹ️ Info"), KeyboardButton("👥 Refer & Earn")]
    ]
    if user_id == ADMIN_TELEGRAM_ID:
        keyboard.append([KeyboardButton("📊 Marketing Status")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message and interactive keyboard when /start is issued."""
    user = update.effective_user
    username = user.first_name if user else "there"
    user_id = user.id if user else 0
    tg_username = user.username if user and user.username else "NoUsername"
    
    # Register user in database
    register_user(user_id, f"{username} (@{tg_username})")
    
    # Handle Referral deep linking (e.g. /start ref_12345)
    args = context.args
    if args and args[0].startswith("ref_"):
        try:
            referrer_id = int(args[0].split("_")[1])
            success = add_referral(user_id, referrer_id)
            if success:
                # Notify the referrer
                try:
                    await context.bot.send_message(
                        chat_id=referrer_id,
                        text=f"🎉 <b>New Referral!</b> <code>{html.escape(username)}</code> has joined using your link. You've earned <b>+2 Free Searches</b>!",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"Failed to notify referrer: {e}")
        except Exception as e:
            logger.error(f"Error registering referral: {e}")
            
    # Check status
    sub_status = get_subscription_status(user_id)
    if sub_status == "premium":
        status_text = "Premium Plan (Unlimited searches unlocked! 🎉)"
    else:
        current_searches = get_search_count(user_id)
        referrals = get_referral_count(user_id)
        allowed_limit = FREE_LIMIT + (referrals * 2)
        remaining = max(0, allowed_limit - current_searches)
        status_text = f"Free Plan ({remaining} of {allowed_limit} searches remaining)"
    
    welcome_text = (
        f"🤖 <b>Welcome to LeadGen Assistant Bot, {html.escape(username)}!</b> \n\n"
        "I am your automated assistant designed to scrape premium B2B business leads and "
        "execute smart outreach campaigns directly from Telegram.\n\n"
        f"💳 <b>Your Status:</b> {status_text}\n\n"
        "✨ <b>Features available:</b>\n"
        "• Real-time business data search\n"
        "• Professional Excel/CSV exports\n"
        "• Automated cold email outreach\n\n"
        "🚀 Tap <b>🔍 Search Leads</b> below to find real-time local business contacts!"
    )
    
    await update.message.reply_text(
        text=welcome_text, 
        parse_mode="HTML", 
        reply_markup=get_main_keyboard(user_id)
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles text messages, search queries, and payment submissions."""
    text = update.message.text
    user = update.effective_user
    user_id = user.id if user else 0
    username = user.first_name if user else "Unknown User"
    tg_username = user.username if user and user.username else "NoUsername"
    
    # Register/update user details
    register_user(user_id, f"{username} (@{tg_username})")
    
    # 1. Handle payment proof submission (Text/TxID)
    if context.user_data.get("awaiting_payment_proof"):
        if not ADMIN_TELEGRAM_ID:
            await update.message.reply_text("⚠️ System Error: Admin ID is not configured. Please contact the developer.")
            context.user_data["awaiting_payment_proof"] = False
            return
            
        proof_text = text if text else "Screenshot uploaded (see above)"
        
        # Notify Admin
        keyboard = [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user_id}"),
                InlineKeyboardButton("❌ Decline", callback_data=f"decline_{user_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        admin_msg = (
            f"🔔 <b>New Payment Submission!</b>\n\n"
            f"👤 User: {html.escape(username)} (@{html.escape(tg_username)})\n"
            f"🆔 Telegram ID: <code>{user_id}</code>\n"
            f"📝 TxID/Details: <code>{html.escape(proof_text)}</code>"
        )
        
        # If user uploaded a photo/file as proof
        if update.message.photo:
            photo_file_id = update.message.photo[-1].file_id
            await context.bot.send_photo(
                chat_id=ADMIN_TELEGRAM_ID,
                photo=photo_file_id,
                caption=admin_msg,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        else:
            await context.bot.send_message(
                chat_id=ADMIN_TELEGRAM_ID,
                text=admin_msg,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            
        await update.message.reply_text(
            "📥 <b>Payment proof submitted successfully!</b>\n\n"
            "Your proof has been forwarded to the Admin for manual verification. "
            "We will instantly notify you here as soon as it is approved (usually takes 5-10 minutes). Thank you!",
            parse_mode="HTML"
        )
        context.user_data["awaiting_payment_proof"] = False
        return

    if text == "🔍 Search Leads":
        await update.message.reply_text(
            "💡 <b>Please enter what you are looking for.</b>\n\n"
            "Format: <code>[Service] in [City, Country]</code>\n"
            "Example: <code>Plumber in New York</code> or <code>Dentist in London</code>\n\n"
            "Simply send the search query as a message below:",
            parse_mode="HTML"
        )
        context.user_data["awaiting_search"] = True
        return

    if text == "💳 Subscription":
        sub_status = get_subscription_status(user_id)
        if sub_status == "premium":
            await update.message.reply_text(
                "💳 <b>Your Subscription:</b>\n\n"
                "• Plan: <b>Premium Plan</b>\n"
                "• Status: <b>Active (Unlimited Searches)</b>\n"
                "• Features: All B2B outreach tools unlocked!\n\n"
                "Thank you for being a premium B2B client! 🚀",
                parse_mode="HTML"
            )
        else:
            current_searches = get_search_count(user_id)
            referrals = get_referral_count(user_id)
            allowed_limit = FREE_LIMIT + (referrals * 2)
            remaining = max(0, allowed_limit - current_searches)
            keyboard = [
                [InlineKeyboardButton("💳 Submit Payment Proof", callback_data="start_payment_submission")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            payment_msg = get_payment_message(current_searches, remaining, allowed_limit=allowed_limit)
            await update.message.reply_text(
                text=payment_msg,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        return

    if text == "👥 Refer & Earn":
        bot_info = await context.bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"
        referrals = get_referral_count(user_id)
        bonus_searches = referrals * 2
        
        msg = (
            "👥 <b>Refer & Earn Program</b>\n\n"
            "Invite your friends to use this bot and get rewarded with <b>+2 Free Searches</b> for every friend who starts the bot!\n\n"
            f"• <b>Your Referrals:</b> {referrals} users invited\n"
            f"• <b>Bonus Searches Earned:</b> {bonus_searches} extra searches\n\n"
            f"🔗 <b>Your Unique Referral Link:</b>\n<code>{ref_link}</code>\n\n"
            "Copy and share this link in Telegram groups, Facebook, or with your friends!"
        )
        await update.message.reply_text(msg, parse_mode="HTML")
        return
        
    if text == "ℹ️ Info":
        sub_status = get_subscription_status(user_id)
        if sub_status == "premium":
            limit_text = "Unlimited searches unlocked! 🎉"
        else:
            current_searches = get_search_count(user_id)
            remaining = max(0, FREE_LIMIT - current_searches)
            limit_text = f"{current_searches}/{FREE_LIMIT} searches used ({remaining} remaining)"
            
        info_text = (
            "ℹ️ <b>LeadGen Assistant Bot</b>\n\n"
            "This bot finds real-time local businesses online and extracts public email addresses "
            "and phone numbers for B2B sales outreach.\n\n"
            f"📊 <b>Usage Limit:</b> {limit_text}\n\n"
            "⚡ <b>Developer Tip:</b> Be specific with your location for better search results!"
        )
        await update.message.reply_text(info_text, parse_mode="HTML")
        return

    if text == "📊 Marketing Status":
        if user_id != ADMIN_TELEGRAM_ID:
            await update.message.reply_text("❌ You are not authorized to run this command.")
            return
            
        stats = get_marketing_campaign_stats()
        
        # Get recent successfully sent emails
        from database import get_recent_sent_leads
        recent = get_recent_sent_leads(5)
        
        recent_text = ""
        if recent:
            recent_text = "\n\n📬 <b>Recently Emailed Leads:</b>\n"
            for i, lead in enumerate(recent, 1):
                # Clean timestamp formatting
                time_part = lead['sent_at'].split('T')[-1][:5] if 'T' in lead['sent_at'] else lead['sent_at'][:5]
                recent_text += f"{i}. <b>{html.escape(lead['company_name'])}</b> (<code>{html.escape(lead['email'])}</code>) at {time_part}\n"
        else:
            recent_text = "\n\n📬 <i>No emails sent yet.</i>"
            
        msg = (
            "📊 <b>B2B Marketing Campaign Status:</b>\n\n"
            f"• Total Leads Discovered: <code>{stats['total']}</code>\n"
            f"• Waiting to Email (Scraped): <code>{stats['scraped']}</code>\n"
            f"• Emails Sent Successfully: <code>{stats['sent']}</code>\n"
            f"• Email Delivery Failed: <code>{stats['failed']}</code>"
            f"{recent_text}\n\n"
            "🤖 <i>The background worker crawls B2B websites and runs outreach campaigns autonomously.</i>"
        )
        await update.message.reply_text(msg, parse_mode="HTML")
        return

    # Check if user is trying to search
    if context.user_data.get("awaiting_search") or text.startswith("/find"):
        query = text
        if text.startswith("/find"):
            query = text.replace("/find", "", 1).strip()
            
        if not query:
            await update.message.reply_text(
                "❌ Please specify a query! Example: <code>/find Plumber in New York</code>",
                parse_mode="HTML"
            )
            return

        # Check Subscription status
        sub_status = get_subscription_status(user_id)
        
        # Limit checks for free users
        if sub_status != "premium":
            current_searches = get_search_count(user_id)
            referrals = get_referral_count(user_id)
            allowed_limit = FREE_LIMIT + (referrals * 2)
            if current_searches >= allowed_limit:
                keyboard = [
                    [InlineKeyboardButton("💳 Submit Payment Proof", callback_data="start_payment_submission")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                payment_msg = get_payment_message(current_searches, 0, is_limit_reached=True, allowed_limit=allowed_limit)
                await update.message.reply_text(
                    text=payment_msg,
                    parse_mode="HTML",
                    reply_markup=reply_markup
                )
                context.user_data["awaiting_search"] = False
                return

        context.user_data["awaiting_search"] = False
        
        referrals = get_referral_count(user_id)
        allowed_limit = FREE_LIMIT + (referrals * 2)
        limit_status = "Premium" if sub_status == "premium" else f"{current_searches + 1} of {allowed_limit}"
        status_msg = await update.message.reply_text(
            f"🔍 <b>Searching real-time leads for: {html.escape(query)}...</b> (Search {limit_status})\n"
            "🌐 Exploring the web & fetching contact details. Please wait standard 10-15 seconds...",
            parse_mode="HTML"
        )
        
        # Scrape
        leads = await search_leads(query, limit=5)
        
        if not leads:
            await status_msg.edit_text(
                "❌ No active leads could be found with contact details. Please try a different query or make it more specific.",
                parse_mode="HTML"
            )
            return

        # Increment search count in database (only if they are a free user)
        if sub_status != "premium":
            new_count = increment_search_count(user_id, f"{username} (@{tg_username})")
            referrals = get_referral_count(user_id)
            allowed_limit = FREE_LIMIT + (referrals * 2)
            usage_caption = f"📊 Usage: {new_count} of {allowed_limit} free searches used."
            usage_header_info = f" ({new_count}/{allowed_limit} used)"
        else:
            usage_caption = "📊 Usage: Premium Plan (Unlimited)"
            usage_header_info = ""

        # Format and send results
        results_header = f"🏆 <b>Top Leads Found for:</b> <code>{html.escape(query)}</code>\n\n"
        results_body = ""
        
        for lead in leads:
            wa_link = lead.get('whatsapp', 'Not found')
            wa_text = f'<a href="{html.escape(wa_link)}">Chat on WhatsApp</a>' if wa_link != "Not found" else "<code>Not found</code>"
            
            results_body += (
                f"🏢 <b>{html.escape(lead['name'])}</b>\n"
                f"🌐 Website: <a href=\"{html.escape(lead['website'])}\">{html.escape(lead['website'])}</a>\n"
                f"📧 Email: <code>{html.escape(lead['email'])}</code>\n"
                f"📞 Phone: <code>{html.escape(lead['phone'])}</code>\n"
                f"💬 WhatsApp: {wa_text}\n"
                f"-----------------------------------\n\n"
            )
            
        full_message = results_header + results_body
        
        # Send in chunks if it exceeds Telegram limits (4096 chars)
        if len(full_message) > 4000:
            await status_msg.edit_text(f"Here are the top leads{usage_header_info}:")
            chunks = [full_message[i:i+4000] for i in range(0, len(full_message), 4000)]
            for chunk in chunks:
                await update.message.reply_text(chunk, parse_mode="HTML", disable_web_page_preview=True)
        else:
            await status_msg.edit_text(full_message, parse_mode="HTML", disable_web_page_preview=True)
            
        # Send Chat Action (uploading document)
        await update.message.reply_chat_action(action="upload_document")
        
        try:
            excel_file = generate_leads_excel(leads, query)
            
            # Send file to Telegram
            with open(excel_file, 'rb') as doc:
                await update.message.reply_document(
                    document=doc,
                    filename=os.path.basename(excel_file),
                    caption=(
                        f"📊 <b>Excel Spreadsheet Generated!</b>\n"
                        f"⚡ Total leads: {len(leads)}\n"
                        f"🔍 Query: <code>{html.escape(query)}</code>\n"
                        f"{usage_caption}"
                    ),
                    parse_mode="HTML",
                    write_timeout=60,
                    read_timeout=60
                )
                
            # Clean up local file
            if os.path.exists(excel_file):
                os.remove(excel_file)
        except Exception as e:
            logger.error(f"Error generating or sending Excel: {e}")
            await update.message.reply_text("⚠️ Could not generate Excel file, but you can copy the details from the chat above.")
            
    else:
        # Default fallback message
        await update.message.reply_text(
            "Use the menu buttons below to navigate, or search directly with `/find <query>`",
            reply_markup=get_main_keyboard(user_id)
        )

async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Replies with the user's numeric Telegram ID."""
    user = update.effective_user
    user_id = user.id if user else 0
    await update.message.reply_text(
        f"👤 <b>Your Telegram Details:</b>\n\n"
        f"• Name: <code>{html.escape(user.first_name)}</code>\n"
        f"• Username: @{html.escape(user.username or 'None')}\n"
        f"• Numeric User ID: <code>{user_id}</code>\n\n"
        f"Copy this ID and paste it into the <code>ADMIN_TELEGRAM_ID</code> field in your <code>.env</code> file!",
        parse_mode="HTML"
    )

async def marketing_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays statistics of the cold email marketing campaign (Admin only)."""
    user_id = update.effective_user.id
    if user_id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("❌ You are not authorized to run this command.")
        return
        
    stats = get_marketing_campaign_stats()
    msg = (
        "📊 <b>B2B Marketing Campaign Status:</b>\n\n"
        f"• Total Leads Discovered: <code>{stats['total']}</code>\n"
        f"• Waiting to Email (Scraped): <code>{stats['scraped']}</code>\n"
        f"• Emails Sent Successfully: <code>{stats['sent']}</code>\n"
        f"• Email Delivery Failed: <code>{stats['failed']}</code>\n\n"
        "🤖 <i>The background worker crawls B2B websites and runs outreach campaigns autonomously.</i>"
    )
    await update.message.reply_text(msg, parse_mode="HTML")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Broadcasts a replied-to message or specified text to all users (Admin only)."""
    user_id = update.effective_user.id
    if user_id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("❌ You are not authorized to run this command.")
        return
        
    # Check if this is a reply
    reply_to = update.message.reply_to_message
    
    # If not a reply and no text provided
    if not reply_to and not context.args:
        await update.message.reply_text(
            "❌ Please reply to a message with `/broadcast` to send it, or type `/broadcast <message>`.",
            parse_mode="HTML"
        )
        return
        
    # Get all users
    from database import get_all_users
    users = get_all_users()
    
    # Exclude admin from broadcast
    users = [u for u in users if u['user_id'] != ADMIN_TELEGRAM_ID]
    
    if not users:
        await update.message.reply_text("No other users registered in the database yet.")
        return
        
    status_msg = await update.message.reply_text(f"📢 Starting broadcast to {len(users)} users...")
    
    success_count = 0
    fail_count = 0
    
    for u in users:
        target_id = u['user_id']
        try:
            if reply_to:
                # Forward/copy the message
                await context.bot.copy_message(
                    chat_id=target_id,
                    from_chat_id=update.message.chat_id,
                    message_id=reply_to.message_id
                )
            else:
                # Send text
                broadcast_text = " ".join(context.args)
                await context.bot.send_message(chat_id=target_id, text=broadcast_text)
            success_count += 1
            await asyncio.sleep(0.05)  # Rate limiting protection
        except Exception as e:
            logger.error(f"Failed to send broadcast to {target_id}: {e}")
            fail_count += 1
            
    await status_msg.edit_text(
        f"📢 <b>Broadcast Completed!</b>\n\n"
        f"• Successfully sent: <code>{success_count}</code>\n"
        f"• Failed/Blocked: <code>{fail_count}</code>",
        parse_mode="HTML"
    )

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles inline button clicks (approvals, payment submission initiation)."""
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    
    await query.answer()
    
    if data == "start_payment_submission":
        context.user_data["awaiting_payment_proof"] = True
        await query.message.reply_text(
            "📝 <b>Payment Proof Submission:</b>\n\n"
            "Please paste your transaction ID (TxID) or upload a photo/screenshot of your "
            "successful $30 payment here. The Admin will verify it immediately:",
            parse_mode="HTML"
        )
        
    elif data.startswith("approve_"):
        # Admin approval click
        if user_id != ADMIN_TELEGRAM_ID:
            await query.message.reply_text("❌ You are not authorized to perform this action.")
            return
            
        target_user_id = int(data.split("_")[1])
        set_subscription_status(target_user_id, "premium")
        
        # Update Admin inline keyboard
        await query.edit_message_reply_markup(reply_markup=None)
        await query.edit_message_caption(
            caption=f"{query.message.caption}\n\n✅ <b>Approved by Admin</b>",
            parse_mode="HTML"
        )
        
        # Notify User
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=(
                    "🎉 <b>Premium Subscription Unlocked!</b>\n\n"
                    "Your payment has been successfully verified by the Admin. "
                    "You now have unlimited search limits. Happy lead generation! 🚀"
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to send activation notification to {target_user_id}: {e}")
            
    elif data.startswith("decline_"):
        # Admin decline click
        if user_id != ADMIN_TELEGRAM_ID:
            await query.message.reply_text("❌ You are not authorized to perform this action.")
            return
            
        target_user_id = int(data.split("_")[1])
        
        # Update Admin inline keyboard
        await query.edit_message_reply_markup(reply_markup=None)
        await query.edit_message_caption(
            caption=f"{query.message.caption}\n\n❌ <b>Declined by Admin</b>",
            parse_mode="HTML"
        )
        
        # Notify User
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=(
                    "❌ <b>Payment Verification Failed</b>\n\n"
                    "The payment proof you submitted could not be verified by the Admin. "
                    "Please double check your transaction details or contact support for help."
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to send rejection notification to {target_user_id}: {e}")

async def handle_photo_proof(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pass photo payment proof submissions through the main message handler."""
    if context.user_data.get("awaiting_payment_proof"):
        # Trigger handle_message directly to process the photo upload
        await handle_message(update, context)
    else:
        # Standard media warning
        await update.message.reply_text("Please send search queries as text messages.")

async def post_init_hook(app: Application) -> None:
    """Launches the background tasks after bot initialization."""
    # Start the marketing worker background task
    asyncio.create_task(start_marketing_worker(app))

def main() -> None:
    """Start the bot."""
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        logger.error("Error: TELEGRAM_BOT_TOKEN is not set in the .env file!")
        return

    # Initialize the SQLite Database
    logger.info("Initializing database...")
    init_db()

    # Create the Application
    application = Application.builder().token(BOT_TOKEN).post_init(post_init_hook).build()

    # Handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("myid", myid_command))
    application.add_handler(CommandHandler("marketing_status", marketing_status_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("find", handle_message))
    application.add_handler(CallbackQueryHandler(handle_callback_query))
    
    # Text handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Photo handler for payment proof
    application.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, handle_photo_proof))

    # Start Polling
    logger.info("Bot is starting... Press Ctrl+C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
