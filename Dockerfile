# Hugging Face Spaces (Docker SDK). Builds the Flask app and serves it with
# gunicorn on port 7860 (the port HF Spaces exposes).
FROM python:3.12-slim

# Run as a non-root user (Hugging Face Spaces best practice).
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /home/user/app

# Install dependencies first for better layer caching.
COPY --chown=user requirements.txt ./
RUN pip install --no-cache-dir --user -r requirements.txt

# Copy the application code.
COPY --chown=user . ./

EXPOSE 7860

# gthread workers so a streaming SSE response holds a thread, not the worker.
# Keys come from Space secrets (NEWSDATA_KEY / GEMINI_API_KEY) via env vars.
CMD ["gunicorn", "--chdir", "app", \
     "--worker-class", "gthread", "--workers", "1", "--threads", "8", \
     "--timeout", "120", "--bind", "0.0.0.0:7860", "app:app"]
