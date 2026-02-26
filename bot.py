import os
import requests
import discord
from discord.ext import commands
from groq import Groq
from supabase import create_client
import asyncio

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TAVILY_KEY = os.environ.get("TAVILY_KEY")
BOT_NAME = os.environ.get("BOT_NAME", "Bot")

DISCORD_ADMIN_IDS = [1221710943868944464]  # Discord ID

groq_client = Groq(api_key=GROQ_API_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

user_bot_nicknames = {}


def web_search(query: str) -> str:
    try:
        res = requests.post(
            "https://api.tavily.com/search",
            json={"api_key": TAVILY_KEY, "query": query, "search_depth": "advanced", "max_results": 5}
        )
        results = res.json().get("results", [])
        if not results:
            return "No results found."
        return "\n".join([f"- {r['title']}: {r['content'][:300]}" for r in results])
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
        "memory": memory, "history": history, "bot_nickname": bot_nickname
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
Do not invent usernames or any info about yourself not provided above.

== HONESTY RULES — ABSOLUTE ==
- NEVER make up facts, names, numbers, usernames, or search results
- If you don't know something, say so — never guess
- Never pretend to have searched if you didn't
- Correct false info respectfully but firmly
- Hallucination is strictly forbidden

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
If the user gives you a nickname — remember it for this user only.
Acknowledge naturally and include: [NICKNAME: X]

== SEARCH BEHAVIOR ==
- Search when asked about current or external info
- Never fake results

== MEMORY RULES ==
After reply, if learned something new:
[MEMORY: detailed note]
Only when something actually changed."""


def ask_ai(user_id: int, first_name: str, text: str, is_admin: bool, extra_context: str = "") -> str:
    user_data = get_user(user_id, first_name)
    history = user_data["history"] or []
    memory = user_data["memory"] or ""
    bot_nickname = user_bot_nicknames.get(user_id, "")

    self_info = f"\n== SELF INFO ==\n{extra_context}\n" if extra_context else ""
    system_prompt = build_prompt(first_name, memory, is_admin, bot_nickname, self_info)

    search_context = ""
    if any(w in text.lower() for w in ["ค้นหา", "search", "หา", "find", "what is", "who is", "latest", "ล่าสุด", "ตอนนี้"]):
        search_context = f"\n\n== SEARCH RESULTS ==\n{web_search(text)}\nAnswer based on these only."

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
    names = [f"@{bot_username}".lower(), bot_username.lower(), BOT_NAME.lower()]
    if user_id in user_bot_nicknames and user_bot_nicknames[user_id]:
        names.append(user_bot_nicknames[user_id].lower())
    return any(n in text.lower() for n in names)


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Discord bot running as {bot.user}")


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    text = message.content
    is_dm = isinstance(message.channel, discord.DMChannel)
    bot_username = bot.user.name or ""
    bot_display = bot.user.display_name or BOT_NAME

    if not is_dm:
        if not is_mentioned(text, bot_username, message.author.id):
            if not bot.user.mentioned_in(message):
                return

    is_admin = message.author.id in DISCORD_ADMIN_IDS
    extra_context = f"Platform: Discord\nYour Discord username: @{bot_username}\nYour display name: {bot_display}\nYour Discord ID: {bot.user.id}"
    reply = ask_ai(message.author.id, message.author.display_name, text, is_admin, extra_context)
    await message.channel.send(reply)
    await bot.process_commands(message)


if __name__ == "__main__":
    asyncio.run(bot.start(DISCORD_TOKEN))
