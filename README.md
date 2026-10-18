# ULTRON

A conversational AI assistant backend that goes beyond chat. It detects intent, grounds its answers in live web search, and can carry out real actions like sending an email, checking the weather, or pulling a stock quote, always confirming with you before anything is actually sent.

**Live demo:** https://ultron-z6ts.onrender.com

> The demo runs on a free Render instance, so the first load after a period of inactivity can take up to ~50 seconds while it wakes up.
> **Email sending is disabled on the public demo.** Ultron will draft the email and show it to you, but won't send it. To send real emails, clone the repo and run it locally with your own Gmail credentials (see [Getting started](#getting-started)).


Built with **FastAPI + PostgreSQL**, containerized with **Docker**.

## What it can do

- **Voice interface:** say "Ultron" to activate it, speak a command, and get the answer back as text and speech.
- **Ask anything:** general questions are answered using live web search results, not just the model's training data.
- **Weather:** "what's the weather in Delhi"
- **Stock prices:** "AAPL stock price", "Reliance stock price" (US and Indian NSE/BSE stocks)
- **Email:** "email sam@example.com about tomorrow's meeting" drafts the email first and only sends it after you confirm.
- **Follow-ups:** it remembers recent messages, so "what about tomorrow?" and "yes, send it" work.

## How it works

Every message goes through an intent layer that decides whether it's small talk, a question that needs live context, or a request to *do* something, then routes it accordingly.

### AI / RAG architecture

- **Hybrid intent detection:** clear commands (weather, stock, email) are matched by fast rule-based parsing, so they're instant and don't depend on the LLM. Ambiguous requests go to the LLM, which returns a structured JSON intent (`chat`, `send_email`, `get_weather`, `get_stock`).
- **Retrieval-Augmented Generation (RAG):** non-small-talk questions trigger a live retrieval step via the Tavily search API (page content, not just headlines). The results are injected into the LLM's prompt as grounding context. The model is instructed to trust retrieved facts over its own training data, so answers reflect current information. A Google News RSS scrape acts as a second-tier fallback retriever if Tavily is unavailable.
- **Resilient LLM chain:** Groq (the model set in `GROQ_MODEL`, default `openai/gpt-oss-120b`) → other chat models available on your Groq key, discovered automatically → local Ollama (local development only) → a plain-text fallback reply. If Groq retires or restricts a model, Ultron finds a working one instead of going down.
- **Resilient JSON parsing:** strips code-fence wrapping and other formatting quirks from LLM output before parsing, with a safe fallback response if the model returns malformed JSON.
- **Observable failures:** every provider error (Groq, Tavily, RSS) is logged with its status code and message, so problems show up in the logs instead of failing silently.

### Actions & memory

- **Confirmed actions:** actions that touch the outside world (like sending an email) are drafted first and only executed after you explicitly confirm. The LLM never fires an action directly.
- **Conversation memory:** recent exchanges per user are saved to Postgres and fed back into the LLM prompt as context.
- **Pending-action state:** a drafted but unconfirmed action is stored server-side per user until confirmed or cancelled, surviving across requests.

## Tech stack

| Layer | Tech |
|---|---|
| API | FastAPI |
| Database | PostgreSQL + SQLAlchemy |
| LLM | Groq (`openai/gpt-oss-120b` by default) / Ollama for local development |
| Web search grounding | Tavily API (Google News RSS fallback) |
| Integrations | Gmail SMTP, OpenWeatherMap, Finnhub (US stocks), Yahoo Finance (NSE/BSE stocks) |
| Frontend | Static HTML/JS served by FastAPI |
| Deployment | Docker, Render |

## Getting started

```bash
git clone https://github.com/rakshitbhardwaj2986/Ultron.git
cd Ultron
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```
DATABASE_URL=postgresql+psycopg2://user:password@host:5432/dbname
GROQ_API_KEY=your_groq_key
GROQ_MODEL=openai/gpt-oss-120b
TAVILY_API_KEY=your_tavily_key
GMAIL_ADDRESS=you@gmail.com
GMAIL_APP_PASSWORD=your_gmail_app_password
OPENWEATHER_API_KEY=your_openweathermap_key
FINNHUB_API_KEY=your_finnhub_key
```

| Variable | Notes |
|---|---|
| `GROQ_MODEL` | Any chat model available on your Groq key. Groq retires models over time, so check the [Groq models list](https://console.groq.com/docs/models) if you see `model_not_found` errors. |
| `GMAIL_APP_PASSWORD` | A Gmail [app password](https://support.google.com/accounts/answer/185833), not your normal password. Real email sending only works when `LIVE_DEMO` is not set. |
| `LIVE_DEMO` | Set to `true` on public deployments to disable real email sending. Leave unset locally. |
| `AI_PROVIDER` | Optional. Force `groq` or `ollama`. |

Run locally:

```bash
uvicorn app.main:app --reload
```

Then visit `http://localhost:8000` for the UI.

Or with Docker:

```bash
docker build -t ultron .
docker run -p 7860:7860 --env-file .env ultron
```

Then visit `http://localhost:7860`.

## API

**`POST /message`**

```json
{
  "user_id": "abc123",
  "command": "email sam@example.com about tomorrow's meeting"
}
```

Returns the assistant's reply, the detected intent, and, once an action is confirmed, the result of executing it (email sent, weather fetched, stock quote returned).

**`GET /health`** returns `{"status": "ok"}`. Use it with an uptime monitor to keep a free-tier deployment awake.

## Deployment notes

The live demo runs on Render's free tier with `LIVE_DEMO=true`, so email sending is disabled there. Set `GROQ_API_KEY`, `GROQ_MODEL`, `TAVILY_API_KEY`, `OPENWEATHER_API_KEY`, `FINNHUB_API_KEY` and `DATABASE_URL` in the service's environment settings. On startup, the logs print which AI provider is active and whether the keys are set.

## License

MIT