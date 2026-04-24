import sys
import os

# Make the package importable when run from repo root on Streamlit Cloud
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from career_ops.dashboard import main

main()
