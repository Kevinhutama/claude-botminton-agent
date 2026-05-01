FROM python:3.12-slim

# Install Node.js (required for Claude CLI)
RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Claude CLI globally
RUN npm install -g @anthropic-ai/claude-code

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the bot server
COPY server.py .

# Create a non-root user (Claude CLI refuses --dangerously-skip-permissions as root)
RUN useradd -m -u 1000 botuser \
    && mkdir -p /app/data /home/botuser/.claude \
    && chown -R botuser:botuser /app /home/botuser/.claude

USER botuser

# CLAUDE.md is mounted at runtime (see docker-compose.yml)

CMD ["python3", "-u", "server.py"]
