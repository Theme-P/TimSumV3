#!/bin/bash
# Deploy script for Summary-Transcribe (Frontend + Backend)
# Run this on your GPU server via SSH

set -e

echo "🚀 Starting deployment..."

# Pull latest code (if using git)
if [ -d ".git" ]; then
    echo "📥 Pulling latest changes..."
    git pull
fi

# Production uses only docker-compose.yml — skip the dev override file.
COMPOSE="docker-compose -f docker-compose.yml"

# Build and start containers
echo "🐳 Building Docker containers..."
$COMPOSE down --remove-orphans 2>/dev/null || true
$COMPOSE build --no-cache
$COMPOSE up -d

# Show status
echo ""
echo "✅ Deployment complete!"
echo ""
echo "📊 Container Status:"
$COMPOSE ps
echo ""
echo "🌐 Access URLs:"
echo "   Frontend: http://$(hostname -I | awk '{print $1}'):3000"
echo "   Backend API: http://$(hostname -I | awk '{print $1}'):8000/docs"
echo ""
echo "📋 Useful commands:"
echo "   View logs: $COMPOSE logs -f"
echo "   Stop: $COMPOSE down"
echo "   Restart: $COMPOSE restart"
