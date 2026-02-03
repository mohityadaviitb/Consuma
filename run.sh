#!/bin/bash
# Run script for the Sync vs Async API

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}🚀 Sync vs Async API Server${NC}"
echo ""

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install dependencies
echo -e "${YELLOW}Installing dependencies...${NC}"
pip install -q -r requirements.txt

# Run the server
echo ""
echo -e "${GREEN}Starting server on http://localhost:8000${NC}"
echo -e "${GREEN}API docs available at http://localhost:8000/docs${NC}"
echo ""
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
