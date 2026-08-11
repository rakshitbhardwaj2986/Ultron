# ULTRON

A conversational AI backend that goes beyond chat — it detects intent, grounds its answers in live web search, and can carry out real actions like sending an email, checking the weather, or pulling a stock quote, always confirming with you before anything is actually sent.

Built with **FastAPI + PostgreSQL**, containerized with **Docker**.

## How it works

Every message goes through an LLM-powered intent-classification layer that decides whether it's small talk, a question that needs live context, or a request to *do* something — then routes it accordingly.

### AI / RAG architecture

- **LLM intent classification + generation** — Groq's Llama 3.3 70B parses each command into a structured JSON intent (`chat`, `send_email`, `get_weather`, `get_stock`), rather than relying on brittle keyword matching for the actual response generation.
- **Retrieval-Augmented Generation (RAG)** — non-small-talk queries trigger a live retrieval step via the Tavily search API (page content, not just headlines), which is injected into the LLM's prompt as grounding context before generation. The model is explicitly instructed to trust retrieved facts over its own training data, so answers reflect current information rather than a stale training cutoff. A Google News RSS scrape acts as a second-tier fallback retriever if Tavily is unavailable.
- **Multi-provider LLM fallback chain** — Groq → local Ollama → a deterministic canned response, so the system degrades gracefully instead of erroring out if a provider is down or unconfigured.
- **Resilient JSON parsing** — strips code-fence wrapping and other formatting quirks from LLM output before parsing, with a safe fallback response if the model returns malformed JSON.

### Actions & memory

- **Action requests** — "email John about the meeting," "what's the weather in Delhi," "AAPL stock price" — get parsed into structured intents by the LLM. Actions that touch the outside world (like sending an email) are drafted first and only executed after you explicitly confirm — the LLM never fires an action directly.
- **Conversation memory** — recent exchanges per user are persisted to Postgres and fed back into the LLM prompt as context, so follow-ups like "what about tomorrow?" or "yes, send it" resolve correctly.
- **Pending-action state** — a drafted-but-unconfirmed action is stored server-side per user until confirmed or cancelled, surviving across requests.

## Tech stack

| Layer | Tech |
|---|---|
| API | FastAPI |
| Database | PostgreSQL + SQLAlchemy |
| LLM | Groq (Llama 3.3 70B) / Ollama fallback |
| Web search grounding | Tavily API (Google News RSS fallback) |
| Integrations | Gmail SMTP, OpenWeatherMap, Alpha Vantage |
| Frontend | Static HTML served by FastAPI |
| Deployment | Docker |

## Getting started

```bash
git clone https://github.com/<your-username>/ULTRON.git
cd ULTRON
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```
DATABASE_URL=postgresql+psycopg2://user:password@host:5432/dbname
GROQ_API_KEY=your_groq_key
GROQ_MODEL=llama-3.3-70b-versatile
TAVILY_API_KEY=your_tavily_key
GMAIL_ADDRESS=you@gmail.com
GMAIL_APP_PASSWORD=your_gmail_app_password
OPENWEATHER_API_KEY=your_openweathermap_key
ALPHAVANTAGE_API_KEY=your_alphavantage_key
```

Run locally:

```bash
uvicorn app.main:app --reload
```

Or with Docker:

```bash
docker build -t ultron .
docker run -p 7860:7860 --env-file .env ultron
```

Visit `http://localhost:7860` for the UI.

## API

**`POST /message`**

```json
{
  "user_id": "abc123",
  "command": "email sam@example.com about tomorrow's meeting"
}
```

Returns the assistant's reply, the detected intent, and — once an action is confirmed — the result of executing it (email sent, weather fetched, stock quote returned).

## License

MIT
