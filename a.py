import logging
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "6917910897:AAGyLchqeN92mssI4CPTGddrNhzzJSuWsjU"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data['score'] = 0
    context.user_data['question_number'] = 1
    await send_question(update, context, is_start=True)

async def send_question(update: Update, context: ContextTypes.DEFAULT_TYPE, is_start=False):
‎    # نمایش انیمیشن بارگذاری
    if is_start:
        await update.message.reply_chat_action(action="upload_photo")
    else:
        await update.callback_query.message.reply_chat_action(action="upload_photo")

    response = requests.get("https://amirplus.alfahost.space/test/1.php")
    data = response.json()

    question = data['question']
    options = data['options']
    image_url = data['image_url']
    correct = data['correct_answer']

    context.user_data['correct'] = correct

    keyboard = [[InlineKeyboardButton(opt, callback_data=opt)] for opt in options]
    reply_markup = InlineKeyboardMarkup(keyboard)

    caption = (
        f"**سوال {context.user_data['question_number']}**\n\n"
        f"{question}\n\n"
        f"امتیاز شما: {context.user_data['score']} ✅"
    )

    if is_start:
        await update.message.reply_photo(photo=image_url, caption=caption, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.callback_query.message.edit_media(
            media={"type": "photo", "media": image_url},
            reply_markup=reply_markup
        )
        await update.callback_query.message.edit_caption(caption=caption, parse_mode="Markdown", reply_markup=reply_markup)

async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    selected = query.data
    correct = context.user_data.get('correct', '')
    context.user_data['question_number'] += 1

    if selected == correct:
        context.user_data['score'] += 1
        response = "✅ عالی! پاسخ صحیح بود."
    else:
        response = f"❌ پاسخ نادرست بود.\n✔ پاسخ صحیح: {correct}"

    keyboard = [
        [InlineKeyboardButton("سوال بعدی", callback_data="next_question")],
        [InlineKeyboardButton("منوی اصلی", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_caption(
        caption=f"{query.message.caption}\n\n{response}",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def next_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_question(update, context)

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    score = context.user_data.get("score", 0)
    question_num = context.user_data.get("question_number", 1)

    msg = (
        f"**بازگشت به منو**\n\n"
        f"شما به {question_num - 1} سوال پاسخ دادید.\n"
        f"امتیاز شما: {score} از {question_num - 1} ✅"
    )

    keyboard = [[InlineKeyboardButton("شروع آزمون جدید", callback_data="restart")]]
    await query.edit_message_caption(caption=msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['score'] = 0
    context.user_data['question_number'] = 1
    await send_question(update, context)

if __name__ == "__main__":
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_answer, pattern="^(?!next_question$|main_menu$|restart$).*"))
    app.add_handler(CallbackQueryHandler(next_question, pattern="^next_question$"))
    app.add_handler(CallbackQueryHandler(main_menu, pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(restart, pattern="^restart$"))

    app.run_polling()
