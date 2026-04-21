"""
Pytest configuration: adds nifty_fair_value package root to sys.path so that
`from engine import ...`, `from config import settings`, etc. all resolve.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
