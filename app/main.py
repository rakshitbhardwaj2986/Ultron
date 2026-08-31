from fastapi import FastAPI, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
import requests
import json
import os
import smtplib
import re
from typing import Optional
from bs4 import BeautifulSoup
from email.message import EmailMessage
from dotenv import load_dotenv
from app.models import MessageRequest, MessageResponse, AIResponse, Message, PendingAction, Base
from app.database import engine, get_db

load_dotenv()

app = FastAPI()

# SERVE THE FRONTEND
@app.get("/")
def serve_ui():
    return FileResponse("ultron_ui.html")

# DATABASE SETUP
Base.metadata.create_all(bind=engine)

GROQ_API_KEY = (os.getenv("GROQ_API_KEY") or "").strip()
AI_PROVIDER = (os.getenv("AI_PROVIDER") or ("groq" if GROQ_API_KEY else "ollama")).strip().lower()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")


def _extract_command(prompt: str) -> str:
    marker = "User Command:"
    if marker not in prompt:
        return ""

    command = prompt.split(marker, 1)[1].strip()
    return command.strip('"').strip()


def _build_fallback_response(command: str) -> str:
    """Used when both Groq and Ollama fail — returns PLAIN TEXT, not JSON.
    The action-request path (which expects JSON) already handles plain-text
    fallback gracefully via its own try/except below; returning JSON here
    was the bug — it displayed the raw dict verbatim in normal chat replies."""
    if command:
        return f"I heard you say: {command}. I'm having trouble reaching my AI service right now — try again in a moment."
    return "I'm online, but having trouble reaching my AI service right now — try again in a moment."


def call_ollama(prompt: str, system_prompt: str = "You are a helpful assistant.") -> str:
    """Route requests to Groq when configured, otherwise fall back to local Ollama or a simple response."""

    try:
        if AI_PROVIDER == "groq":
            if not GROQ_API_KEY:
                raise RuntimeError("GROQ_API_KEY is missing")

            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.2,
                "max_tokens": 300
            }

            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "")

        # default: local Ollama
        url = "http://localhost:11434/api/generate"
        payload = {
            "model": "llama3",
            "prompt": prompt,
            "stream": False
        }

        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return response.json()["response"]
    except Exception:
        return _build_fallback_response(_extract_command(prompt))

# INTENT DETECTION

def _looks_like_action_request(command: str) -> bool:
    text = command.lower()
    action_keywords = ["email", "weather", "stock", "forecast", "send", "price", "buy", "sell"]
    return any(keyword in text for keyword in action_keywords)


# CONVERSATION MEMORY

def get_recent_history(db: Session, user_id: str, limit: int = 6) -> str:
    """Last few exchanges for this user, oldest first — gives the model
    context for follow-ups ('what about tomorrow', 'yes send it', etc.)."""
    rows = (
        db.query(Message)
        .filter(Message.user_id == user_id)
        .order_by(Message.time_created.desc())
        .limit(limit)
        .all()
    )
    rows = list(reversed(rows))

    if not rows:
        return ""

    lines = []
    for row in rows:
        lines.append(f"User: {row.command}")
        lines.append(f"Ultron: {row.response}")

    return "\n".join(lines)


# PENDING ACTION CONFIRMATION (e.g. a drafted email waiting on "yes")

def get_pending_action(db: Session, user_id: str) -> Optional[PendingAction]:
    return db.query(PendingAction).filter(PendingAction.user_id == user_id).first()


def save_pending_action(db: Session, user_id: str, intent: str, action_data: dict, response: str):
    clear_pending_action(db, user_id)
    pending = PendingAction(
        user_id=user_id,
        intent=intent,
        action_data=json.dumps(action_data or {}),
        response=response
    )
    db.add(pending)
    db.commit()


def clear_pending_action(db: Session, user_id: str):
    db.query(PendingAction).filter(PendingAction.user_id == user_id).delete()
    db.commit()


def _is_confirmation(command: str) -> bool:
    text = command.lower().strip().rstrip("!?.")
    confirm_phrases = {
        "yes", "yeah", "yep", "yup", "confirm", "confirmed", "correct",
        "send it", "go ahead", "do it", "sure", "okay", "ok", "please do"
    }
    return text in confirm_phrases or text.startswith("yes")


def _is_cancellation(command: str) -> bool:
    text = command.lower().strip().rstrip("!?.")
    cancel_phrases = {
        "no", "nope", "cancel", "cancelled", "canceled", "don't", "dont",
        "stop", "never mind", "nevermind"
    }
    return text in cancel_phrases or text.startswith("no")


def _is_smalltalk(command: str) -> bool:
    """Quick greetings/chit-chat that don't need a web search — everything
    else gets grounded in real search results (facts, theories, live data,
    historical data — the model's own memory is unreliable for all of it)."""
    text = command.lower().strip().rstrip("!?.")
    smalltalk_phrases = [
        "hi", "hello", "hey", "yo", "sup",
        "how are you", "hows it going", "what's up", "whats up",
        "thanks", "thank you", "thankyou",
        "who are you", "what are you", "what can you do",
        "good morning", "good night", "good evening", "good afternoon",
        "bye", "goodbye", "see you"
    ]
    return any(text == phrase or text.startswith(phrase + " ") for phrase in smalltalk_phrases)


TAVILY_API_KEY = (os.getenv("TAVILY_API_KEY") or "").strip()


def fetch_web_context(query: str, max_results: int = 5) -> str:
    """Real web search grounding via Tavily — built for feeding results to
    an LLM (returns actual page content, not just headlines). Falls back to
    the old Google News RSS scrape if Tavily isn't configured or fails."""

    if TAVILY_API_KEY:
        try:
            response = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": max_results
                },
                timeout=20
            )
            response.raise_for_status()
            results = response.json().get("results", [])

            snippets = []
            for r in results[:max_results]:
                title = r.get("title", "")
                content = (r.get("content") or "")[:500]
                url = r.get("url", "")
                if title or content:
                    snippets.append(f"{title}\n{content}\nSource: {url}")

            if snippets:
                return "Web search results:\n\n" + "\n\n".join(snippets)
        except Exception:
            pass  # fall through to the RSS fallback below

    # fallback: Google News RSS — narrower (news only), needs no API key
    try:
        rss_url = "https://news.google.com/rss/search?q=" + requests.utils.quote(query)
        response = requests.get(rss_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        items = soup.find_all("item")[:max_results]
        snippets = []
        for item in items:
            title = item.find("title")
            link = item.find("link")
            title_text = title.get_text(" ", strip=True) if title else ""
            link_text = link.get_text(" ", strip=True) if link else ""
            if title_text:
                snippets.append(f"{title_text} | {link_text}")

        if not snippets:
            return ""

        return "Search results:\n" + "\n".join(snippets[:max_results])
    except Exception:
        return ""


def _clean_json_response(raw: str) -> str:
    """Groq sometimes wraps its JSON reply in ```json ... ``` code fences
    despite being told to return raw JSON only — strip that off before parsing,
    otherwise json.loads() fails and the whole draft silently gets lost."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return text.strip()


def detect_intent(command: str, history: str = "") -> AIResponse:

    if _looks_like_action_request(command):
        prompt = f"""
You are Ultron, an AI assistant.

Return ONLY valid JSON. Do not wrap it in code fences, markdown, or any other text —
your entire reply must be nothing but the JSON object itself, starting with {{ and ending with }}.

Format:
{{
  "intent": "chat | send_email | get_weather | get_stock",
  "response": "natural reply",
  "action_data": {{
    "to": "",
    "subject": "",
    "body": "",
    "city": "",
    "stock_name": ""
  }},
  "requires_confirmation": false
}}

Rules:
- For "send_email": ALWAYS set intent to "send_email" (not "chat") and requires_confirmation to true.
  Fill action_data with your best draft of to/subject/body from the command and conversation so far.
  The "response" should summarize the draft and ask the user to confirm — do NOT say it was sent,
  it has not been sent yet.
- For "get_weather" and "get_stock": requires_confirmation is false, these run immediately.
- For "get_stock": "stock_name" MUST be the stock's ticker symbol (e.g. "NVDA" for Nvidia, "AAPL"
  for Apple, "MSFT" for Microsoft, "GOOGL" for Google/Alphabet, "AMZN" for Amazon, "TSLA" for Tesla,
  "META" for Meta), NOT the company's plain name — the stock lookup only accepts exact ticker symbols.
  For Indian companies listed on the NSE, append ".NS" to the ticker (e.g. "RELIANCE.NS" for
  Reliance, "TCS.NS" for Tata Consultancy Services, "INFY.NS" for Infosys).
- Use the conversation so far to fill in details the user gave earlier (e.g. a recipient or topic
  mentioned a few messages ago) if the current command doesn't repeat them.

Conversation so far:
{history if history else "(no prior messages)"}

User Command:
"{command}"
"""
        raw_output = call_ollama(prompt, system_prompt="You are Ultron, a helpful assistant that can process actions and return JSON only for action requests.")

        try:
            parsed = json.loads(_clean_json_response(raw_output))
            return AIResponse(**parsed)
        except Exception:
            # last resort — don't ever show raw/broken JSON as a spoken response
            return AIResponse(
                intent="chat",
                response="Sorry, I had trouble processing that request — could you rephrase it?",
                action_data={}
            )

    web_context = ""
    if not _is_smalltalk(command):
        web_context = fetch_web_context(command)

    prompt = f"""
You are Ultron, a direct and helpful conversational assistant with access to live web search results below.
Answer the user's request naturally, clearly, and concisely.
Important behavior:
- Be conversational and assistant-like, not robotic.
- Use the conversation so far for context — if this message is a follow-up ("what about
  tomorrow", "and the second one"), resolve it against what was discussed before.
- Treat the web context below as ground truth for facts, dates, prices, scores, names, or events —
  it is more current and more reliable than anything you already "know," even for things that
  happened before your training cutoff. Prefer it over your own memory whenever they conflict.
- Never say phrases like "as of my last update," "I don't have real-time access," or
  "my training data only goes up to X" — you DO have current information below when it's provided.
- If the web context is empty or doesn't actually answer the question, say plainly that you
  couldn't find reliable current information on that, rather than guessing.
- Do not invent facts. If you're not sure, say so briefly instead of making something up.
- For live or recent questions, summarize the latest information clearly and directly, citing
  the source name inline if useful (not the raw URL).

Conversation so far:
{history if history else "(no prior messages)"}

Web context:
{web_context if web_context else "(no search results — none were found or this didn't need a search)"}

User Command:
"{command}"
"""
    raw_output = call_ollama(prompt, system_prompt="You are Ultron, a polished conversational assistant that always trusts and uses the provided web context over its own memory for facts, dates, and current information.")
    response_text = (raw_output or "I can help with that.").strip()

    return AIResponse(
        intent="chat",
        response=response_text,
        action_data={}
    )

# ACTIONS

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")
ALPHAVANTAGE_API_KEY = os.getenv("ALPHAVANTAGE_API_KEY")  # no longer used for stock lookups, kept in case you revert
FINNHUB_API_KEY = (os.getenv("FINNHUB_API_KEY") or "").strip()


LIVE_DEMO = (os.getenv("LIVE_DEMO") or "").strip().lower() == "true"


def send_email(data):
    to = data.get("to")
    subject = data.get("subject") or "Message from Ultron"
    body = data.get("body") or ""

    if not to:
        return "Couldn't send the email — no recipient was given."

    if LIVE_DEMO:
        # Set only on the public Render deployment (not locally). Avoids
        # exposing an unauthenticated endpoint that can fire real emails to
        # arbitrary addresses, and sidesteps outbound SMTP port blocks on
        # free-tier hosts entirely.
        return (
            f"✅ Email drafted and would be sent to {to} — real sending is "
            f"disabled in this public demo. Clone the repo and run it "
            f"locally to enable real sending."
        )

    try:
        msg = EmailMessage()
        msg["From"] = GMAIL_ADDRESS
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            smtp.send_message(msg)

        return f"Email sent to {to}"

    except Exception as e:
        return f"Failed to send email: {e}"


def get_weather(data):
    city = data.get("city")

    if not city:
        return "Couldn't get the weather — no city was given."

    try:
        url = "https://api.openweathermap.org/data/2.5/weather"
        params = {"q": city, "appid": OPENWEATHER_API_KEY, "units": "metric"}

        response = requests.get(url, params=params)
        response.raise_for_status()
        result = response.json()

        temp = result["main"]["temp"]
        feels_like = result["main"]["feels_like"]
        description = result["weather"][0]["description"]

        return f"Weather in {city}: {temp}°C, feels like {feels_like}°C, {description}"

    except Exception as e:
        return f"Couldn't fetch weather for {city}: {e}"


# Safety net in case the LLM returns a plain company name instead of a
# ticker despite the prompt instruction — covers the most commonly asked
# companies without needing an extra API call for the typical case.
COMMON_TICKERS = {
    "nvidia": "NVDA", "apple": "AAPL", "microsoft": "MSFT",
    "google": "GOOGL", "alphabet": "GOOGL", "amazon": "AMZN",
    "tesla": "TSLA", "meta": "META", "facebook": "META",
    "netflix": "NFLX", "amd": "AMD", "intel": "INTC",

    # Indian companies (NSE) — Finnhub's free tier doesn't cover NSE/BSE,
    # so these are routed to Yahoo Finance instead (see get_stock below).
    "tcs": "TCS.NS", "tata consultancy": "TCS.NS", "tata consultancy services": "TCS.NS",
    "infosys": "INFY.NS", "reliance": "RELIANCE.NS", "reliance industries": "RELIANCE.NS",
    "hdfc bank": "HDFCBANK.NS", "hdfc": "HDFCBANK.NS", "icici bank": "ICICIBANK.NS",
    "icici": "ICICIBANK.NS", "wipro": "WIPRO.NS", "tata motors": "TATAMOTORS.NS",
    "itc": "ITC.NS", "state bank of india": "SBIN.NS", "sbi": "SBIN.NS",
    "bharti airtel": "BHARTIARTL.NS", "airtel": "BHARTIARTL.NS",
    "hindustan unilever": "HINDUNILVR.NS", "larsen": "LT.NS", "larsen and toubro": "LT.NS",
    "maruti": "MARUTI.NS", "maruti suzuki": "MARUTI.NS", "bajaj finance": "BAJFINANCE.NS",
    "adani enterprises": "ADANIENT.NS", "ntpc": "NTPC.NS"
}


def _resolve_ticker(stock: str) -> str:
    """Best-effort: if this already looks like a ticker (short, no spaces),
    use it as-is. Otherwise check the common name map, and fall back to
    Finnhub's own symbol search."""
    cleaned = stock.strip()
    lower = cleaned.lower()

    if lower in COMMON_TICKERS:
        return COMMON_TICKERS[lower]

    # Looks like it's probably already a ticker (short, no spaces)
    if len(cleaned) <= 5 and " " not in cleaned:
        return cleaned.upper()

    # Last resort: ask Finnhub to search for the best-matching symbol
    try:
        response = requests.get(
            "https://finnhub.io/api/v1/search",
            params={"q": cleaned, "token": FINNHUB_API_KEY},
            timeout=10
        )
        response.raise_for_status()
        matches = response.json().get("result", [])
        if matches:
            return matches[0].get("symbol", cleaned)
    except Exception:
        pass

    return cleaned


def _get_stock_yahoo(stock):
    """Yahoo Finance's public chart endpoint — no API key needed, and covers
    exchanges Finnhub's free tier doesn't (like NSE .NS / BSE .BO)."""
    try:
        response = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{stock}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10
        )
        response.raise_for_status()
        result = response.json().get("chart", {}).get("result")

        if not result:
            return f"Couldn't find stock data for {stock}"

        price = result[0].get("meta", {}).get("regularMarketPrice")
        currency = result[0].get("meta", {}).get("currency", "")

        if not price:
            return f"Couldn't find stock data for {stock}"

        return f"Stock price of {stock} is {currency} {float(price):.2f}"

    except Exception as e:
        return f"Couldn't fetch stock price for {stock}: {e}"


def get_stock(data):
    stock = data.get("stock_name")

    if not stock:
        return "Couldn't get the stock price — no stock symbol was given."

    stock = _resolve_ticker(stock)

    # Finnhub's free tier only covers US exchanges — Indian tickers
    # (already tagged .NS/.BO by COMMON_TICKERS, or typed that way directly)
    # go to Yahoo Finance instead.
    if stock.upper().endswith(".NS") or stock.upper().endswith(".BO"):
        return _get_stock_yahoo(stock.upper())

    try:
        response = requests.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": stock, "token": FINNHUB_API_KEY},
            timeout=10
        )
        response.raise_for_status()
        payload = response.json()

        # Finnhub returns 0 for every field (not an error) when the symbol
        # doesn't exist or the request failed — that's the "no data" signal.
        price = payload.get("c")

        if not price:
            return f"Couldn't find stock data for {stock}"

        return f"Stock price of {stock} is ${float(price):.2f}"

    except Exception as e:
        return f"Couldn't fetch stock price for {stock}: {e}"


def execute_action(intent, action_data):

    if intent == "send_email":
        return send_email(action_data)

    elif intent == "get_weather":
        return get_weather(action_data)

    elif intent == "get_stock":
        return get_stock(action_data)

    return None

# MAIN ENDPOINT

@app.post("/message", response_model=MessageResponse)
def handle_message(req: MessageRequest, db: Session = Depends(get_db)):

    pending = get_pending_action(db, req.user_id)

    # 1a. There's a drafted action waiting on this user's confirmation
    if pending:
        if _is_confirmation(req.command):
            action_data = json.loads(pending.action_data)
            action_result = execute_action(pending.intent, action_data)
            intent = pending.intent
            response_text = action_result or pending.response
            clear_pending_action(db, req.user_id)

        elif _is_cancellation(req.command):
            intent = "chat"
            response_text = "Okay, cancelled — I won't send that."
            action_result = None
            action_data = {}
            clear_pending_action(db, req.user_id)

        else:
            # user moved on to something else — drop the stale draft and
            # process this as a normal new message below
            clear_pending_action(db, req.user_id)
            pending = None

    # 1b. Normal message (or the pending draft was just dropped above)
    if not pending:
        history = get_recent_history(db, req.user_id)
        ai_result = detect_intent(req.command, history)
        intent = ai_result.intent
        response_text = ai_result.response
        action_data = ai_result.action_data or {}

        # Only send_email actually needs confirmation before running — weather
        # and stock lookups are read-only and safe to run immediately.
        # Groq's own requires_confirmation field is unreliable (it sometimes
        # sets it true for weather/stock too, despite the prompt telling it
        # not to), so this is enforced here in code rather than trusted from
        # the LLM's output.
        CONFIRMATION_REQUIRED_INTENTS = {"send_email"}

        if intent in CONFIRMATION_REQUIRED_INTENTS:
            # don't run it yet — save the draft and wait for the next message
            save_pending_action(db, req.user_id, intent, action_data, response_text)
            action_result = None
        else:
            action_result = execute_action(intent, action_data)
            if action_result:
                # The frontend only displays `response`, not `action_result` —
                # so for actions that run immediately (weather, stock), show
                # the real fetched data instead of the LLM's generic preview
                # text (e.g. "Let me check that for you").
                response_text = action_result

    # 2. Save to DB
    db_message = Message(
        user_id=req.user_id,
        command=req.command,
        intent=intent,
        response=response_text
    )

    db.add(db_message)
    db.commit()
    db.refresh(db_message)

    # 3. Return response
    return MessageResponse(
        user_id=req.user_id,
        intent=intent,
        response=response_text,
        action_data=action_data,
        action_result=action_result
    )