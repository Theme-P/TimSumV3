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

# Build and start containers
echo "🐳 Building Docker containers..."
docker-compose down --remove-orphans 2>/dev/null || true
docker-compose build --no-cache
docker-compose up -d

# Show status
echo ""
echo "✅ Deployment complete!"
echo ""
echo "📊 Container Status:"
docker-compose ps
echo ""
echo "🌐 Access URLs:"
echo "   Frontend: http://$(hostname -I | awk '{print $1}'):3000"
echo "   Backend API: http://$(hostname -I | awk '{print $1}'):8000/docs"
echo ""
echo "📋 Useful commands:"
echo "   View logs: docker-compose logs -f"
echo "   Stop: docker-compose down"
echo "   Restart: docker-compose restart"
