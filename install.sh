#!/bin/bash
# Simple Earthwall installer + runner for Linux Mint

set -e

echo "=== EarthWall Quick Setup ==="

# Create venv and install
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "=== Setup complete! Starting Earthwall... ==="

# Run the app
./run_gui.sh