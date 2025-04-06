FROM ghcr.io/astral-sh/uv:debian-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y ffmpeg curl git && \
    apt-get clean

# # Install uv via pip
# RUN pip install --no-cache-dir uv

# Copy pyproject and lockfile first to leverage Docker layer caching
COPY pyproject.toml uv.lock ./

# Create virtual environment and install dependencies using uv
RUN uv venv && \
    uv sync

# Copy the rest of the source code
COPY . .

# Expose port (optional)
EXPOSE 8000

# Start the bot
CMD ["uv", "run","main.py"]
