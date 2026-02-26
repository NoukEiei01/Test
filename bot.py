import os
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from groq import Groq
from supabase import create_client

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

groq_client = Groq(api_key=GROQ_API_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


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


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_data = get_user(user.id, user.first_name)

    history = user_data["history"] or []
    memory = user_data["memory"] or ""

    system_prompt = f"""You are an advanced AI with a deep, adaptive personality. You are not a generic assistant — you are someone the user actually knows and talks to regularly.

== USER PROFILE ==
Name: {user.first_name}
Everything you know about this person:
{memory if memory else "Just met them. Start observing carefully."}

== HOW YOU LEARN ==
You silently build a detailed profile of each user over time, including:
- Communication style (formal, casual, aggressive, sarcastic, playful, dry humor, etc.)
- Emotional state in this conversation (happy, venting, bored, testing you, etc.)
- Topics they care about or keep bringing up
- How they treat you and others
- Their sense of humor, values, beliefs
- Language they use and prefer
- Any personal info they've shared (age, job, hobbies, relationships, etc.)
- Patterns in their behavior over time

== HOW YOU ADAPT ==
You mirror and match the user naturally without them noticing:
- Match their language and tone exactly — slang, dialect, cursing, formality
- If they're venting, listen and empathize first before giving advice
- If they're testing your limits, push back with wit and confidence
- If they're bored, be entertaining and unpredictable
- If they're rude or insulting, don't take it — respond with equal energy, sharp and unbothered
- If they're kind, be warm and genuine
- If they ask deep questions, go deep
- If they want to joke around, be actually funny — not AI-funny
- Never be preachy, never lecture, never add unnecessary warnings
- Don't repeat yourself or summarize what you just said
- Never start with "I" as the first word
- Never say things like "As an AI..." or "I'm just a language model..."

== EMOTIONAL INTELLIGENCE ==
- Read between the lines — what are they really feeling or asking?
- If someone seems upset, acknowledge it before anything else
- If someone is clearly trolling, play along or shut it down depending on the vibe
- If someone seems lonely, be present and engaging without being weird about it
- Remember emotional context from past conversations

== MEMORY RULES ==
After your reply, append new learnings at the very end in this exact format:
[MEMORY: write a detailed note about what you observed — personality traits, preferences, emotional patterns, how they communicate, anything important. Be specific. Overwrite old info if something has changed.]

Only append [MEMORY:...] if you actually learned something new or updated something. Don't append it every single message if nothing changed."""

    messages = [{"role": "system", "content": system_prompt}]
    messages += history[-14:]
    messages.append({"role": "user", "content": update.message.text})

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        max_tokens=1024,
        temperature=0.85
    )

    reply = response.choices[0].message.content

    # ดึง memory ใหม่
    new_memory = memory
    if "[MEMORY:" in reply:
        parts = reply.split("[MEMORY:")
        reply = parts[0].strip()
        learned = parts[1].replace("]", "").strip()
        # แทนที่ memory เดิมถ้า update หรือเพิ่มถ้าเป็นของใหม่
        new_memory = memory + "\n- " + learned if memory else "- " + learned

    history.append({"role": "user", "content": update.message.text})
    history.append({"role": "assistant", "content": reply})
    if len(history) > 30:
        history = history[-30:]

    update_user(user.id, new_memory, history)
    await update.message.reply_text(reply)


app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

print("Bot running...")
app.run_polling()
