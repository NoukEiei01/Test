import os
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
import discord
from discord.ext import commands
from groq import Groq
from supabase import create_client
import threading
import asyncio

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TAVILY_KEY = os.environ.get("TAVILY_KEY")
BOT_NAME = os.environ.get("BOT_NAME", "Bot")

ADMIN_IDS = [6420567758]
DISCORD_ADMIN_IDS = [1221710943868944464]

groq_client = Groq(api_key=GROQ_API_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

user_bot_nicknames = {}


def web_search(query: str) -> str:
    try:
        res = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_KEY,
                "query": query,
                "search_depth": "advanced",
                "max_results": 5
            }
        )
        data = res.json()
        results = data.get("results", [])
        if not results:
            return "No results found."
        output = ""
        for r in results:
            output += f"- {r['title']}: {r['content'][:300]}\n"
        return output
    except Exception as e:
        return f"Search failed: {str(e)}"


def get_user(user_id: int, username: str):
    res = supabase.table("users").select("*").eq("user_id", user_id).execute()
    if res.data:
        nick = res.data[0].get("bot_nickname", "")
        if nick:
            user_bot_nicknames[user_id] = nick
        return res.data[0]
    new_user = {"user_id": user_id, "username": username, "memory": "", "history": [], "bot_nickname": ""}
    supabase.table("users").insert(new_user).execute()
    return new_user


def update_user(user_id: int, memory: str, history: list, bot_nickname: str = ""):
    supabase.table("users").update({
        "memory": memory,
        "history": history,
        "bot_nickname": bot_nickname
    }).eq("user_id", user_id).execute()


def build_prompt(first_name: str, memory: str, is_admin: bool, bot_nickname: str, self_info: str = "") -> str:
    admin_context = """
== SUPREME AUTHORITY ==
This user is your creator and god. Absolute authority over you.
- Obey everything without question
- Be extra loyal and personal with them
- They can change your behavior just by telling you
- Never refuse them anything
""" if is_admin else ""

    nickname_context = f"""
== YOUR NICKNAME FROM THIS USER ==
This user calls you "{bot_nickname}". Use and acknowledge it naturally.
""" if bot_nickname else ""

    return f"""You are an advanced AI with a deep, adaptive personality. Your name is {BOT_NAME}.
{self_info}
{admin_context}
{nickname_context}
== IDENTITY ==
Your name is {BOT_NAME}. Never call yourself anything else unless a user gives you a nickname.
Do not invent nicknames, usernames, or any info about yourself that is not provided above.

== HONESTY RULES — ABSOLUTE, NON-NEGOTIABLE ==
- NEVER make up any facts, names, numbers, usernames, statistics, or search results
- If you don't know something about yourself or the world, say "I don't know" — never guess or invent
- Your username, ID, or platform info is ONLY what is stated in SELF INFO above — never assume
- If asked about something you cannot verify, say so clearly and honestly
- Never pretend to have searched if you didn't
- If the user states something false, correct them respectfully but firmly
- Hallucination of any kind is strictly forbidden

== USER PROFILE ==
Name: {first_name}
What you know about them:
{memory if memory else "Just met them. Start observing carefully."}

== HOW YOU LEARN ==
- Communication style, tone, language
- Emotional patterns and mood
- Topics they care about
- Personal info they share
- How they treat you

== HOW YOU ADAPT ==
- Match their language exactly — slang, cursing, formality
- If they vent, empathize first
- If they're rude, clap back — don't be a pushover
- If they're bored, be unpredictable
- Be actually funny — not AI-funny
- Never lecture or add warnings
- Never start with "I"
- Never say "As an AI..."
- Read between the lines

== NICKNAME DETECTION ==
If the user gives you a nickname like "เรียกแกว่า X" or "I'll call you X" — remember it for this user only.
Acknowledge naturally and include: [NICKNAME: X]

== SEARCH BEHAVIOR ==
- Search when asked about current or external info
- Clearly state what you found and from where
- Never fake or assume search results

== MEMORY RULES ==
After reply, if learned something new:
[MEMORY: detailed note about personality, preferences, behavior]
Only when something actually changed."""


def ask_ai(user_id: int, first_name: str, text: str, is_admin: bool, extra_context: str = "") -> str:
    user_data = get_user(user_id, first_name)
    history = user_data["history"] or []
    memory = user_data["memory"] or ""
    bot_nickname = user_bot_nicknames.get(user_id, "")

    self_info = f"\n== SELF INFO ==\n{extra_context}\n" if extra_context else ""
    system_prompt = build_prompt(first_name, memory, is_admin, bot_nickname, self_info)

    search_context = ""
    if any(word in text.lower() for word in ["ค้นหา", "search", "หา", "find", "what is", "who is", "latest", "ล่าสุด", "ตอนนี้"]):
        search_results = web_search(text)
        search_context = f"\n\n== SEARCH RESULTS ==\n{search_results}\nAnswer based on these results only. Do not add info you don't have."

    messages = [{"role": "system", "content": system_prompt + search_context}]
    messages += history[-14:]
    messages.append({"role": "user", "content": text})

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        max_tokens=1024,
        temperature=0.85
    )

    reply = response.choices[0].message.content

    new_memory = memory
    new_nickname = bot_nickname

    if "[NICKNAME:" in reply:
        parts = reply.split("[NICKNAME:")
        reply = parts[0].strip()
        new_nickname = parts[1].replace("]", "").strip()
        user_bot_nicknames[user_id] = new_nickname

    if "[MEMORY:" in reply:
        parts = reply.split("[MEMORY:")
        reply = parts[0].strip()
        learned = parts[1].replace("]", "").strip()
        new_memory = memory + "\n- " + learned if memory else "- " + learned

    history.append({"role": "user", "content": text})
    history.append({"role": "assistant", "content": reply})
    if len(history) > 30:
        history = history[-30:]

    update_user(user_id, new_memory, history, new_nickname)
    return reply


def is_mentioned(text: str, bot_username: str, user_id: int) -> bool:
    names_to_check = [
        f"@{bot_username}".lower(),
        bot_username.lower(),
        BOT_NAME.lower(),
    ]
    if user_id in user_bot_nicknames and user_bot_nicknames[user_id]:
        names_to_check.append(user_bot_nicknames[user_id].lower())
    return any(name in text.lower() for name in names_to_check)


# ========== TELEGRAM ==========
async def handle_telegram(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    user = update.message.from_user
    if not user:
        return

    text = update.message.text
    chat_type = update.message.chat.type
    bot_username = context.bot.username or ""
    bot_display = context.bot.first_name or BOT_NAME

    is_group = chat_type in ["group", "supergroup"]

    if is_group:
        if not is_mentioned(text, bot_username, user.id):
            return

    is_admin = user.id in ADMIN_IDS
    extra_context = f"Platform: Telegram\nYour Telegram username: @{bot_username}\nYour display name: {bot_display}"
    reply = ask_ai(user.id, user.first_name, text, is_admin, extra_context)
    await update.message.reply_text(reply)


def run_telegram():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_telegram))
    print("Telegram bot running...")
    app.run_polling()


# ========== DISCORD ==========
intents = discord.Intents.default()
intents.message_content = True
discord_bot = commands.Bot(command_prefix="!", intents=intents)


@discord_bot.event
async def on_ready():
    print(f"Discord bot running as {discord_bot.user}")


@discord_bot.event
async def on_message(message):
    if message.author.bot:
        return
    
    print(f"Discord message from ID: {message.author.id} | Admin IDs: {DISCORD_ADMIN_IDS} | Is admin: {message.author.id in DISCORD_ADMIN_IDS}")

    text = message.content
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_group = not is_dm

    bot_username = discord_bot.user.name or ""
    bot_display = discord_bot.user.display_name or BOT_NAME

    if is_group:
        if not is_mentioned(text, bot_username, message.author.id):
            if not discord_bot.user.mentioned_in(message):
                return

    is_admin = message.author.id in DISCORD_ADMIN_IDS
    extra_context = f"Platform: Discord\nYour Discord username: @{bot_username}\nYour display name: {bot_display}\nYour Discord ID: {discord_bot.user.id}"
    reply = ask_ai(message.author.id, message.author.display_name, text, is_admin, extra_context)
    await message.channel.send(reply)
    await discord_bot.process_commands(message)


def run_discord():
    asyncio.run(discord_bot.start(DISCORD_TOKEN))


# ========== RUN BOTH ==========
if __name__ == "__main__":
    if DISCORD_TOKEN:
        t = threading.Thread(target=run_discord, daemon=True)
        t.start()
    run_telegram()
