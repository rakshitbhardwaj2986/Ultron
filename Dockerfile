
FROM python:3.11-slim
 
WORKDIR /code
 
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
 
COPY . .
 
# Render/Koyeb inject $PORT at runtime; defaults to 7860 for local/HF use
ENV PORT=7860
EXPOSE 7860

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
 