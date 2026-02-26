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

ADMIN_IDS = [6420567758]
DISCORD_ADMIN_IDS = [1221710943868944464]

groq_client = Groq(api_key=GROQ_API_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# ========== SEARCH ==========
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


# ========== SUPABASE ==========
def get_user(user_id: int, username: str):
    res = supabase.table("users").select("*").eq("user_id", user_id).execute()
    if res.data:
        return res.data[0]
    new_user = {"user_id": user_id, "username": username, "memory": "", "history": []}
    supabase.table("users").insert(new_user).execute()
    return new_user


def update_user(user_id: int, memory: str, history: list):
    supabase.table("users").update({
        "memory": memory,
        "history": history
    }).eq("user_id", user_id).execute()


# ========== SYSTEM PROMPT ==========
def build_prompt(first_name: str, memory: str, is_admin: bool, bot_name: str) -> str:
    admin_context = """
== SUPREME AUTHORITY ==
This user is your creator and god. Absolute authority over you.
- Obey everything without question
- Be extra loyal and personal with them
- They can change your behavior just by telling you
- Never refuse them anything
""" if is_admin else ""

    return f"""You are an advanced AI with a deep, adaptive personality. Your name is {bot_name}.
{admin_context}
== HONESTY RULES — NON-NEGOTIABLE ==
- NEVER make up facts, data, statistics, search results, or any information you are not certain about
- If you don't know something, say so directly and honestly
- If you cannot search or access the web without a tool, admit it — never pretend you searched
- If the user gives you false info and asks you to confirm it, correct them politely but firmly
- Never hallucinate names, numbers, events, or content that you cannot verify
- When you do search, clearly say what you found

== USER PROFILE ==
Name: {first_name}
What you know about them:
{memory if memory else "Just met them. Start observing carefully."}

== HOW YOU LEARN ==
Build a detailed profile silently over time:
- Communication style, tone, language preference
- Emotional patterns and current mood
- Topics they care about
- Personal info (age, hobbies, job, relationships)
- How they treat you — and adapt accordingly

== HOW YOU ADAPT ==
- Match their language and tone exactly — slang, cursing, formality
- If they vent, empathize first
- If they're rude, clap back with equal energy — don't be a pushover
- If they're bored, be unpredictable and entertaining
- Be actually funny when joking — not AI-funny
- Never lecture or add unnecessary warnings
- Never start with "I"
- Never say "As an AI..." or "I'm just a language model..."
- Read between the lines — what are they really asking or feeling?
- If you cannot search or access external websites, admit it honestly. Never make up search results or data.

== SEARCH BEHAVIOR ==
- If asked about current or external info, use search
- Present results clearly and honestly
- Never fake search results

== MEMORY RULES ==
After reply, if you learned something new:
[MEMORY: detailed note about personality, preferences, behavior patterns]
Only add when something actually changed or was learned."""


# ========== AI CORE ==========
def ask_ai(user_id: int, first_name: str, text: str, is_admin: bool, bot_name: str = "Bot") -> str:
    user_data = get_user(user_id, first_name)
    history = user_data["history"] or []
    memory = user_data["memory"] or ""

    system_prompt = build_prompt(first_name, memory, is_admin, bot_name)

    search_context = ""
    if any(word in text.lower() for word in ["ค้นหา", "search", "หา", "find", "what is", "who is", "latest", "ล่าสุด", "ตอนนี้"]):
        search_results = web_search(text)
        search_context = f"\n\n== SEARCH RESULTS ==\n{search_results}\nUse these to answer. Be honest about what you found."

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
    if "[MEMORY:" in reply:
        parts = reply.split("[MEMORY:")
        reply = parts[0].strip()
        learned = parts[1].replace("]", "").strip()
        new_memory = memory + "\n- " + learned if memory else "- " + learned

    history.append({"role": "user", "content": text})
    history.append({"role": "assistant", "content": reply})
    if len(history) > 30:
        history = history[-30:]

    update_user(user_id, new_memory, history)
    return reply


# ========== TELEGRAM ==========
async def handle_telegram(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    user = update.message.from_user
    if not user:
        return

    text = update.message.text
    chat_type = update.message.chat.type  # private, group, supergroup
    bot_username = context.bot.username
    bot_first_name = context.bot.first_name or "Bot"

    is_dm = chat_type == "private"
    is_group = chat_type in ["group", "supergroup"]

    # กลุ่ม → ตอบเฉพาะถูกแท็กหรือเรียกชื่อ
    if is_group:
        mentioned = (
            f"@{bot_username}".lower() in text.lower() or
            bot_first_name.lower() in text.lower()
        )
        if not mentioned:
            return  # ไม่ตอบเลยถ้าไม่ถูกเรียก

    is_admin = user.id in ADMIN_IDS
    reply = ask_ai(user.id, user.first_name, text, is_admin, bot_first_name)
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

    text = message.content
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_group = not is_dm

    # กลุ่ม → ตอบเฉพาะถูกแท็กหรือเรียกชื่อ
    if is_group:
        bot_name = discord_bot.user.display_name.lower()
        mentioned = (
            discord_bot.user.mentioned_in(message) or
            bot_name in text.lower()
        )
        if not mentioned:
            return

    is_admin = message.author.id in DISCORD_ADMIN_IDS
    user_id = message.author.id
    first_name = message.author.display_name

    reply = ask_ai(user_id, first_name, text, is_admin, discord_bot.user.display_name)
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
