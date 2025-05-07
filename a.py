import logging
import requests
import sqlite3
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode # برای استفاده از MarkdownV2 که امکانات قالب‌بندی بهتری دارد
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# --- پیکربندی لاگینگ ---
# لاگینگ به ما کمک می‌کند تا رفتار ربات و خطاهای احتمالی را بهتر ردیابی کنیم.
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- ثابت‌ها ---
# بهتر است مقادیر ثابت مانند توکن، آدرس API و نام دیتابیس در یکجا تعریف شوند.
BOT_TOKEN = "6475997034:AAFZcdfltQvaZuxU9GlD-oxAY0u1a4bV6Yg" # توکن ربات شما
API_URL = "https://amirplus.alfahost.space/test/1.php"
DB_NAME = "quiz_bot_database.db" # نام فایل دیتابیس SQLite
MAX_QUESTIONS_PER_QUIZ = 10 # می‌توانید تعداد سوالات هر آزمون را اینجا تنظیم کنید

# --- توابع مربوط به دیتابیس (SQLite) ---

def init_db():
    """ایجاد جداول دیتابیس در صورت عدم وجود."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # جدول کاربران: برای ذخیره اطلاعات و بالاترین امتیاز هر کاربر
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT NOT NULL,
            last_name TEXT,
            username TEXT,
            high_score INTEGER DEFAULT 0,         -- بالاترین امتیاز کسب شده توسط کاربر در یک آزمون
            total_score INTEGER DEFAULT 0,        -- مجموع تمام امتیازاتی که کاربر تاکنون کسب کرده
            quizzes_taken INTEGER DEFAULT 0,      -- تعداد کل آزمون‌هایی که کاربر شرکت کرده
            questions_answered INTEGER DEFAULT 0, -- تعداد کل سوالاتی که کاربر پاسخ داده
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    logger.info(f"Database '{DB_NAME}' initialized.")

def add_or_update_user(user_id: int, first_name: str, last_name: str = None, username: str = None):
    """افزودن کاربر جدید یا به‌روزرسانی اطلاعات کاربر موجود."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    timestamp = datetime.now()
    try:
        cursor.execute("""
            INSERT INTO users (user_id, first_name, last_name, username, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                username = excluded.username,
                last_seen = excluded.last_seen
        """, (user_id, first_name, last_name, username, timestamp))
        conn.commit()
        logger.info(f"User {user_id} ({first_name}) added or updated in DB.")
    except sqlite3.Error as e:
        logger.error(f"Database error in add_or_update_user for {user_id}: {e}")
    finally:
        conn.close()

def update_user_quiz_stats(user_id: int, current_quiz_score: int, questions_in_current_quiz: int):
    """به‌روزرسانی آمار کاربر پس از اتمام یک آزمون."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT high_score, total_score, quizzes_taken, questions_answered FROM users WHERE user_id = ?", (user_id,))
        user_stats = cursor.fetchone()
        if user_stats:
            new_high_score = max(user_stats[0], current_quiz_score)
            new_total_score = user_stats[1] + current_quiz_score
            new_quizzes_taken = user_stats[2] + 1
            new_questions_answered = user_stats[3] + questions_in_current_quiz

            cursor.execute("""
                UPDATE users
                SET high_score = ?, total_score = ?, quizzes_taken = ?, questions_answered = ?, last_seen = ?
                WHERE user_id = ?
            """, (new_high_score, new_total_score, new_quizzes_taken, new_questions_answered, datetime.now(), user_id))
            conn.commit()
            logger.info(f"Stats updated for user {user_id}: HS={new_high_score}, TotalScore={new_total_score}")
        else:
            logger.warning(f"User {user_id} not found for stat update.")
    except sqlite3.Error as e:
        logger.error(f"Database error in update_user_quiz_stats for {user_id}: {e}")
    finally:
        conn.close()

def get_leaderboard(limit: int = 10):
    """دریافت لیست کاربران برتر بر اساس بالاترین امتیاز."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    leaders = []
    try:
        # مرتب‌سازی ابتدا بر اساس high_score و سپس total_score (برای امتیازهای برابر)
        cursor.execute("""
            SELECT first_name, username, high_score
            FROM users
            WHERE high_score > 0 -- فقط کاربرانی که امتیازی دارند
            ORDER BY high_score DESC, total_score DESC
            LIMIT ?
        """, (limit,))
        leaders = cursor.fetchall()
    except sqlite3.Error as e:
        logger.error(f"Database error in get_leaderboard: {e}")
    finally:
        conn.close()
    return leaders

def get_user_stats(user_id: int):
    """دریافت آمار کلی یک کاربر."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    stats = None
    try:
        cursor.execute("SELECT first_name, high_score, total_score, quizzes_taken, questions_answered FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if row:
            stats = {"name": row[0], "high_score": row[1], "total_score": row[2], "quizzes_taken": row[3], "questions_answered": row[4]}
    except sqlite3.Error as e:
        logger.error(f"Database error in get_user_stats for {user_id}: {e}")
    finally:
        conn.close()
    return stats

# --- توابع کمکی برای MarkdownV2 ---
def escape_markdown_v2(text: str) -> str:
    """Escape کردن کاراکترهای خاص برای MarkdownV2."""
    if not isinstance(text, str): # اگر ورودی رشته نیست، به رشته تبدیل شود
        text = str(text)
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return "".join(f'\\{char}' if char in escape_chars else char for char in text)

# --- توابع اصلی ربات ---

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, message_override: str = None):
    """نمایش منوی اصلی به کاربر."""
    user = update.effective_user
    user_id = user.id
    add_or_update_user(user_id, user.first_name, user.last_name, user.username) # اطمینان از وجود کاربر در دیتابیس

    user_stats = get_user_stats(user_id)
    welcome_name = user.first_name if user.first_name else "کاربر" # استفاده از نام کوچک در صورت وجود

    if message_override:
        text = message_override
    else:
        text = f"سلام {escape_markdown_v2(welcome_name)} عزیز\\! 👋\nبه ربات *آزمونک* خوش آمدید\\."

    if user_stats:
        text += (
            f"\n\n📊 **آمار شما تا این لحظه:**\n"
            f"🏆 بالاترین امتیاز در یک آزمون: `{user_stats.get('high_score', 0)}`\n"
            f"💰 مجموع امتیازات کسب شده: `{user_stats.get('total_score', 0)}`\n"
            f"▶️ تعداد آزمون‌های شرکت کرده: `{user_stats.get('quizzes_taken', 0)}`\n"
            f"✔️ تعداد کل سوالات پاسخ داده شده: `{user_stats.get('questions_answered', 0)}`"
        )
    else:
        text += "\n\nهنوز آماری برای شما ثبت نشده است\\. با شرکت در آزمون شروع کنید\\!"

    keyboard = [
        [InlineKeyboardButton("🚀 شروع آزمون جدید", callback_data="start_quiz_new")],
        [InlineKeyboardButton("🏆 جدول قهرمانان", callback_data="leaderboard_show")],
        [InlineKeyboardButton("💡 راهنما", callback_data="help_show")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query: # اگر از یک دکمه به منو آمده‌ایم
        try:
            await update.callback_query.message.edit_text(
                text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
            )
            await update.callback_query.answer()
        except Exception as e: # اگر پیام قابل ویرایش نبود (مثلا خیلی قدیمی)
            logger.error(f"Error editing message for main menu (callback): {e}")
            await context.bot.send_message(
                chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
            )
    else: # اگر با دستور /start آمده‌ایم
        await update.message.reply_text(
            text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2
        )

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """هنگامی که کاربر دستور /start را ارسال می‌کند."""
    user = update.effective_user
    logger.info(f"User {user.id} ({user.first_name}) started the bot.")
    add_or_update_user(user.id, user.first_name, user.last_name, user.username)
    context.user_data.clear() # پاک کردن داده‌های آزمون قبلی از حافظه موقت
    await show_main_menu(update, context)

async def start_quiz_new_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع یک آزمون جدید از طریق دکمه در منوی اصلی."""
    query = update.callback_query
    await query.answer("🚀 در حال آماده‌سازی اولین سوال...")

    context.user_data.clear()
    context.user_data['current_score'] = 0
    context.user_data['current_question_number'] = 0 # از صفر شروع، قبل از ارسال اولین سوال ۱ می‌شود
    context.user_data['questions_in_this_quiz'] = 0 # شمارنده سوالات در آزمون فعلی
    logger.info(f"User {update.effective_user.id} starting a new quiz.")
    await send_question(update, context, is_first_question=True)

async def send_question(update: Update, context: ContextTypes.DEFAULT_TYPE, is_first_question: bool = False):
    """ارسال سوال فعلی به کاربر."""
    chat_id = update.effective_chat.id
    action_requester = update.message if is_first_question or not update.callback_query else update.callback_query.message

    # نمایش انیمیشن "در حال آپلود عکس"
    await context.bot.send_chat_action(chat_id=chat_id, action="upload_photo")

    try:
        response = requests.get(API_URL, timeout=15) # افزایش timeout
        response.raise_for_status() # بررسی خطاهای HTTP (4xx, 5xx)
        api_data = response.json()
        logger.debug(f"API response for question: {api_data}")
    except requests.exceptions.Timeout:
        logger.error("API request timed out.")
        error_message = "⌛️ متاسفانه ارتباط با سرور سوالات زمان زیادی برد\\. لطفا کمی بعد دوباره تلاش کنید\\."
        await _handle_api_error(update, context, error_message)
        return
    except requests.exceptions.RequestException as e:
        logger.error(f"API request failed: {e}")
        error_message = "😕 متاسفانه در دریافت سوال از سرور مشکلی پیش آمد\\. لطفا بعدا تلاش کنید\\."
        await _handle_api_error(update, context, error_message)
        return
    except ValueError as e: # اگر JSON معتبر نباشد
        logger.error(f"API JSON decode failed: {e}. Response was: {response.text[:200]}")
        error_message = "⚠️ خطایی در پردازش اطلاعات دریافتی از سرور رخ داد\\."
        await _handle_api_error(update, context, error_message)
        return

    question_text = api_data.get('question')
    options_list = api_data.get('options')
    image_url = api_data.get('image_url')
    correct_answer_text = api_data.get('correct_answer') # متن پاسخ صحیح

    if not all([question_text, isinstance(options_list, list) and len(options_list) > 1, image_url, correct_answer_text]):
        logger.error(f"API data is incomplete or malformed: {api_data}")
        error_message = " اطلاعات سوال دریافت شده از سرور ناقص است\\. در حال تلاش مجدد\\.\\.\\. "
        # می‌توان در اینجا یک بار دیگر تلاش کرد یا به منو بازگرداند
        await _handle_api_error(update, context, error_message, should_retry=False) # فعلا retry نمی‌کنیم
        return

    context.user_data['correct_answer_text'] = correct_answer_text
    context.user_data['current_question_number'] += 1
    context.user_data['questions_in_this_quiz'] +=1

    # ایجاد دکمه‌ها با ایموجی‌های جذاب‌تر
    option_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    keyboard_buttons = []
    for i, opt_text in enumerate(options_list):
        emoji = option_emojis[i] if i < len(option_emojis) else "▫️"
        # callback_data باید یکتا و قابل شناسایی باشد. از پیشوند "ans:" استفاده می‌کنیم.
        keyboard_buttons.append([InlineKeyboardButton(f"{emoji} {escape_markdown_v2(opt_text)}", callback_data=f"ans:{escape_markdown_v2(opt_text)}")])

    reply_markup = InlineKeyboardMarkup(keyboard_buttons)

    q_num = context.user_data['current_question_number']
    current_score = context.user_data['current_score']

    caption = (
        f"🧠 **سوال {q_num} از {MAX_QUESTIONS_PER_QUIZ}** 🧠\n\n"
        f"_{escape_markdown_v2(question_text)}_\n\n"
        f"⭐ *امتیاز شما در این آزمون: {current_score}*"
    )

    try:
        if is_first_question or not update.callback_query : # اگر اولین سوال است یا از /start آمده
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=image_url,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        else: # ویرایش پیام قبلی برای سوالات بعدی
            # برای ویرایش، باید مدیا را چک کنیم. اگر تصویر ثابت است، edit_caption کافیست.
            # اما چون API برای هر سوال ممکن است تصویر متفاوتی بدهد، edit_media بهتر است.
            await update.callback_query.message.edit_media(
                media={"type": "photo", "media": image_url, "caption": caption, "parse_mode": ParseMode.MARKDOWN_V2},
                reply_markup=reply_markup
            )
            # گاهی edit_media کپشن را آپدیت نمی‌کند، این خط برای اطمینان است (بسته به نسخه کتابخانه)
            # await update.callback_query.message.edit_caption(caption=caption, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)
            await update.callback_query.answer() # بستن نوتیفیکیشن لودینگ دکمه
    except Exception as e:
        logger.error(f"Error sending/editing question photo message: {e}")
        # اگر ارسال عکس با کپشن و دکمه ناموفق بود، حداقل متن سوال را بفرستیم
        fallback_text = caption + "\n\n" + "\n".join([f"{b[0].text}" for b in keyboard_buttons]) + "\n\n(خطا در نمایش تصویر)"
        if update.callback_query:
            await update.callback_query.message.edit_text(fallback_text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=reply_markup)
        else:
            await context.bot.send_message(chat_id, fallback_text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=reply_markup)


async def _handle_api_error(update: Update, context: ContextTypes.DEFAULT_TYPE, message: str, should_retry: bool = False):
    """مدیریت خطاهای مربوط به API و نمایش پیام مناسب."""
    if update.callback_query:
        try:
            await update.callback_query.message.edit_text(message, parse_mode=ParseMode.MARKDOWN_V2)
            await update.callback_query.answer(text="⚠️ خطا", show_alert=True)
        except Exception: # اگر ویرایش پیام هم خطا داد
             await context.bot.send_message(chat_id=update.effective_chat.id, text=message, parse_mode=ParseMode.MARKDOWN_V2)
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=message, parse_mode=ParseMode.MARKDOWN_V2)

    # اگر قرار بر retry نیست، یا به منوی اصلی برگردان یا گزینه‌هایی بده
    if not should_retry:
        await show_main_menu(update, context, message_override=message + "\n\nلطفا از منوی اصلی ادامه دهید:")


async def handle_answer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پردازش پاسخ انتخاب شده توسط کاربر."""
    query = update.callback_query
    # callback_data به فرم "ans:متن_گزینه_انتخاب_شده" است
    selected_option_unescaped = query.data.split("ans:", 1)[1]
    # چون callback_data را escape کرده بودیم، برای مقایسه باید unescape کنیم یا پاسخ صحیح را هم escape کنیم
    # ساده‌تر است که پاسخ صحیح ذخیره شده در user_data هم escape شده باشد یا callback_data را unescape نکنیم
    # در اینجا فرض می‌کنیم که متن پاسخ صحیح در user_data خام است و selected_option هم خام است.
    # با توجه به اینکه callback_data را escape کردیم، باید آن را unescape کنیم
    # اما در این پیاده‌سازی، callback_data را با متن گزینه *بعد از* escape کردن ساختیم.
    # پس selected_option_unescaped همان متن گزینه است.

    correct_answer = context.user_data.get('correct_answer_text', '')
    current_q_caption = query.message.caption # کپشن فعلی سوال برای حفظ آن

    is_correct = (selected_option_unescaped == correct_answer) # مقایسه مستقیم متن‌ها

    result_message: str
    if is_correct:
        context.user_data['current_score'] += 1
        result_icon = "✅"
        result_text = "*عالی\\! پاسخ شما صحیح بود\\.*"
    else:
        result_icon = "❌"
        result_text = f"*افسوس\\! پاسخ شما نادرست بود\\.*\n✔️ پاسخ صحیح: `{escape_markdown_v2(correct_answer)}`"

    full_result_message = f"{result_icon} {result_text}"

    # حذف دکمه‌های گزینه‌ها از پیام سوال
    try:
        await query.message.edit_caption(
            caption=query.message.caption + f"\n\n{full_result_message}", # اضافه کردن نتیجه به کپشن سوال
            reply_markup=None, # حذف دکمه ها
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        logger.warning(f"Could not edit caption to remove buttons: {e}")
        # اگر ویرایش کپشن برای حذف دکمه‌ها ناموفق بود، مهم نیست، ادامه می‌دهیم

    # بررسی اینکه آیا آزمون تمام شده یا سوال بعدی وجود دارد
    current_q_num = context.user_data.get('current_question_number', 0)
    if current_q_num >= MAX_QUESTIONS_PER_QUIZ:
        await query.answer(f"{result_icon} پاسخ ثبت شد. آزمون تمام شد!", show_alert=False)
        await end_quiz(update, context, triggered_by_completion=True, result_of_last_q=full_result_message)
    else:
        await query.answer(f"{result_icon} پاسخ ثبت شد.", show_alert=False)
        # ارسال پیام جدید برای دکمه "سوال بعدی" یا "پایان آزمون"
        keyboard = [
            [InlineKeyboardButton("⬅️ سوال بعدی", callback_data="next_question_req")],
            [InlineKeyboardButton("🏁 پایان و مشاهده نتیجه", callback_data="end_quiz_manual")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        # به جای ویرایش پیام قبلی، یک پیام جدید برای این دکمه‌ها می‌فرستیم تا تمیزتر باشد
        await query.message.reply_text(
            text=full_result_message + "\n\nچه کار کنیم؟ 👇", # متن پیام قبلی هم اینجا تکرار می‌شود
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN_V2,
            quote=False # به پیام سوال ریپلای نکن که شلوغ نشود
        )


async def next_question_req_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """درخواست برای نمایش سوال بعدی."""
    query = update.callback_query
    await query.answer("⏳ بارگذاری سوال بعدی...")
    # پیام قبلی (که دکمه "سوال بعدی" داشت) را ویرایش می‌کنیم تا دکمه‌هایش غیرفعال شوند یا متنش تغییر کند
    try:
        await query.edit_message_text(
            text=query.message.text + "\n\n*در حال رفتن به سوال بعدی\\.\\.\\.*",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=None # حذف دکمه‌ها
        )
    except Exception as e:
        logger.warning(f"Could not edit previous message text for next_question_req: {e}")

    await send_question(update, context)


async def end_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE, triggered_by_completion: bool = False, result_of_last_q: str = ""):
    """پایان دادن به آزمون، محاسبه و نمایش نتایج نهایی."""
    query = update.callback_query # ممکن است از دکمه "پایان آزمون" یا بعد از آخرین سوال آمده باشد

    user_id = update.effective_user.id
    final_score = context.user_data.get("current_score", 0)
    num_questions_answered = context.user_data.get("questions_in_this_quiz", 0)

    if num_questions_answered > 0: # فقط اگر حداقل یک سوال جواب داده شده، آمار را آپدیت کن
        update_user_quiz_stats(user_id, final_score, num_questions_answered)

    # پیام نتیجه نهایی
    result_emoji = "🎉"
    percentage = 0
    if num_questions_answered > 0:
        percentage = (final_score / num_questions_answered) * 100
        if percentage >= 80: result_emoji = "🏆🥳"
        elif percentage >= 50: result_emoji = "👍😊"
        elif percentage > 0 : result_emoji = "😐"
        else: result_emoji = "😔"
    else: # اگر هیچ سوالی جواب نداده و آزمون را تمام کرده
        result_emoji = "🤷‍♂️"

    final_message_parts = []
    if triggered_by_completion and result_of_last_q:
         # اگر بعد از پاسخ به آخرین سوال آمده‌ایم، نتیجه آخرین سوال را هم نمایش بده
        final_message_parts.append(result_of_last_q)
        final_message_parts.append("\n➖➖➖➖➖") # جداکننده

    final_message_parts.append(f"{result_emoji} **آزمون شما به پایان رسید\\!** {result_emoji}")
    final_message_parts.append(f"تعداد کل سوالات پرسیده شده: `{num_questions_answered}`")
    final_message_parts.append(f"تعداد پاسخ‌های صحیح شما: `{final_score}`")
    if num_questions_answered > 0:
        final_message_parts.append(f"درصد موفقیت شما: `{percentage:.1f}%`")

    # پاک کردن اطلاعات آزمون فعلی از حافظه موقت
    logger.info(f"Quiz ended for user {user_id}. Score: {final_score}/{num_questions_answered}")
    context.user_data.clear()

    # نمایش منوی اصلی با پیام نتیجه
    # await show_main_menu(update, context, message_override="\n".join(final_message_parts) + "\n\nچه کار می‌کنید؟")
    # یا فقط دکمه‌های بازگشت به منو و شروع مجدد
    keyboard = [
        [InlineKeyboardButton("🚀 آزمون جدید", callback_data="start_quiz_new")],
        [InlineKeyboardButton("🏠 بازگشت به منوی اصلی", callback_data="main_menu_return")],
        [InlineKeyboardButton("🏆 جدول قهرمانان", callback_data="leaderboard_show")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    final_text_to_send = "\n".join(final_message_parts) + "\n\nاکنون می‌توانید آزمون جدیدی را شروع کنید یا به منوی اصلی بازگردید."

    if query: # اگر از callback آمده (چه پایان دستی چه خودکار)
        await query.answer("🏁 آزمون تمام شد!")
        try:
            # اگر پیام قبلی (که دکمه "سوال بعدی" یا "پایان" داشت) قابل ویرایش است، آن را ویرایش کن
            await query.message.edit_text(
                text=final_text_to_send,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        except Exception as e:
            logger.error(f"Error editing message for end_quiz: {e}")
            # اگر ویرایش پیام قبلی ممکن نبود، یک پیام جدید بفرست
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=final_text_to_send,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN_V2
            )
    else: # این حالت نباید رخ دهد چون end_quiz همیشه از یک callback فراخوانی می‌شود
         await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=final_text_to_send,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN_V2
            )

async def end_quiz_manual_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """کاربر به صورت دستی درخواست پایان آزمون را داده است."""
    query = update.callback_query
    await query.answer("در حال پایان دادن به آزمون...")
    await end_quiz(update, context, triggered_by_completion=False)


async def show_leaderboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش جدول امتیازات (قهرمانان)."""
    query = update.callback_query
    await query.answer("🏆 در حال بارگذاری جدول قهرمانان...")

    leaders = get_leaderboard(limit=10) # دریافت ۱۰ نفر برتر
    if not leaders:
        leaderboard_text = "هنوز هیچ قهرمانی در جدول ثبت نشده است\\. شما اولین نفر باشید\\! 🥇"
    else:
        leaderboard_text_parts = ["🏆 **جدول قهرمانان آزمونک** 🏆\n"]
        rank_emojis = ["🥇", "🥈", "🥉"]
        for i, (name, username, score) in enumerate(leaders):
            rank_display = rank_emojis[i] if i < len(rank_emojis) else f"🏅 \\({i+1}\\)"
            user_display_name = escape_markdown_v2(name)
            if username:
                user_display_name += f" \\(@{escape_markdown_v2(username)}\\)"
            leaderboard_text_parts.append(f"{rank_display} {user_display_name} ➖ امتیاز: `{score}`")
        leaderboard_text = "\n".join(leaderboard_text_parts)

    keyboard = [[InlineKeyboardButton("🏠 بازگشت به منوی اصلی", callback_data="main_menu_return")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await query.message.edit_text(leaderboard_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"Error editing message for leaderboard: {e}")
        await context.bot.send_message(chat_id=update.effective_chat.id, text=leaderboard_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)

async def main_menu_return_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فراخوانی نمایش منوی اصلی از طریق دکمه "بازگشت به منو"."""
    await show_main_menu(update, context)

async def help_show_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش پیام راهنما."""
    query = update.callback_query
    await query.answer()

    help_text = (
        "💡 **راهنمای ربات آزمونک** 💡\n\n"
        "سلام\\! من یک ربات برای برگزاری آزمون‌های تصویری چندگزینه‌ای هستم\\.\n\n"
        "🔹 با زدن دکمه **'🚀 شروع آزمون جدید'**، یک آزمون با تعدادی سوال \\(مثلا " + str(MAX_QUESTIONS_PER_QUIZ) + " سوال\\) برای شما شروع می‌شود\\.\n"
        "🔹 به سوالات پاسخ دهید و امتیاز کسب کنید\\.\n"
        "🔹 با دکمه **'🏆 جدول قهرمانان'** می‌توانید رتبه خود و دیگر بازیکنان برتر را ببینید\\.\n"
        "🔹 بالاترین امتیاز شما در یک آزمون، در جدول قهرمانان ملاک رتبه‌بندی خواهد بود\\.\n"
        "🔹 در پایان هر آزمون، امتیاز نهایی شما محاسبه و آمار کلی شما در دیتابیس به‌روز می‌شود\\.\n\n"
        "از شرکت در آزمون‌ها لذت ببرید و دانش خود را محک بزنید\\! 👍"
    )
    keyboard = [[InlineKeyboardButton("🏠 بازگشت به منوی اصلی", callback_data="main_menu_return")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await query.message.edit_text(help_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"Error editing message for help: {e}")
        # اگر ویرایش پیام ممکن نبود، پیام جدید ارسال کن
        await context.bot.send_message(chat_id=update.effective_chat.id, text=help_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN_V2)

# --- تابع اصلی و راه‌اندازی ربات ---
def main():
    """تابع اصلی برای راه‌اندازی ربات."""
    init_db() # ابتدا دیتابیس را مقداردهی اولیه می‌کنیم

    application = Application.builder().token(BOT_TOKEN).build()

    # Handler برای دستور /start
    application.add_handler(CommandHandler("start", start_command))

    # CallbackQuery Handlers برای دکمه‌های مختلف
    # ترتیب مهم است: الگوهای خاص‌تر باید قبل از الگوهای عمومی‌تر باشند.
    application.add_handler(CallbackQueryHandler(start_quiz_new_callback, pattern="^start_quiz_new$"))
    application.add_handler(CallbackQueryHandler(handle_answer_callback, pattern="^ans:.*")) # پاسخ به سوالات
    application.add_handler(CallbackQueryHandler(next_question_req_callback, pattern="^next_question_req$"))
    application.add_handler(CallbackQueryHandler(end_quiz_manual_callback, pattern="^end_quiz_manual$"))
    application.add_handler(CallbackQueryHandler(show_leaderboard_callback, pattern="^leaderboard_show$"))
    application.add_handler(CallbackQueryHandler(main_menu_return_callback, pattern="^main_menu_return$"))
    application.add_handler(CallbackQueryHandler(help_show_callback, pattern="^help_show$"))

    # یک fallback برای callback_data های ناشناس (اختیاری ولی خوب است)
    async def unknown_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.callback_query:
            await update.callback_query.answer("این دکمه دیگر فعال نیست یا دستور نامشخص است!", show_alert=True)
            logger.warning(f"Unknown callback_data received: {update.callback_query.data} from user {update.effective_user.id}")
    application.add_handler(CallbackQueryHandler(unknown_callback)) # بدون pattern، همه callback های دیگر را می‌گیرد

    logger.info("Bot is starting to poll...")
    application.run_polling()
    logger.info("Bot has stopped.")

if __name__ == "__main__":
    main()
