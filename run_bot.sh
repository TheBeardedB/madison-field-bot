#!/bin/bash

echo "Starting Madison Field Status Bot..."

# Check if .env file exists
if [ ! -f .env ]; then
    echo "ERROR: .env file not found!"
    echo "Please create a .env file with your configuration:"
    echo ""
    echo "Steps:"
    echo "1. Copy the example: cp .env.example .env"
    echo "2. Edit .env with your Discord bot token and channel ID"
    echo ""
    echo "Example .env content:"
    echo "DISCORD_BOT_TOKEN=your_bot_token_here"
    echo "DISCORD_CHANNEL_ID=your_channel_id_here"
    echo ""
    exit 1
fi

echo "Activating virtual environment..."
source venv/bin/activate

if [ $? -ne 0 ]; then
    echo "ERROR: Failed to activate virtual environment!"
    echo "Please run setup.sh first to create the virtual environment."
    exit 1
fi

echo "Starting bot..."
python field_status_bot.py
