#!/bin/bash
# Launches the Earthwall GUI using the project's virtual environment.
# Usage: ./run_gui.sh
cd "$(dirname "$0")"
source venv/bin/activate
python -m earthwall.gui
