#!/bin/sh
# Fix ownership of mounted volumes (they may be owned by root)
chown -R botuser:botuser /home/botuser/.claude /app/data 2>/dev/null || true
# Switch to botuser and run the server
exec su-exec botuser python3 -u server.py
