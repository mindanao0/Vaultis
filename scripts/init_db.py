import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.sentiment_models import create_tables

if __name__ == "__main__":
    create_tables()
    print("Tables created successfully")
