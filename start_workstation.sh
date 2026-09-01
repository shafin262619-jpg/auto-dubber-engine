#!/bin/bash
cd /home/shafin/Desktop/BlueprintTube_Project/Claude-workstation
source .venv/bin/activate

# Check if Streamlit is already running on port 8501, if not start it
if ! lsof -i:8501 > /dev/null; then
    streamlit run app.py &
    sleep 3
fi

xdg-open http://localhost:8501
