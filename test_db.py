import sqlite3
import pandas as pd

def check_database():
    # Connect to database
    conn = sqlite3.connect('unabated_odds.db')
    
    # List all tables
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print("\nAvailable tables:", tables)
    
    # For each table, show row count and sample data
    for table in tables:
        table_name = table[0]
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        count = cursor.fetchone()[0]
        print(f"\nTable: {table_name}")
        print(f"Row count: {count}")
        
        if count > 0:
            # Show sample data
            df = pd.read_sql(f"SELECT * FROM {table_name} LIMIT 5", conn)
            print("\nSample data:")
            print(df)
    
    conn.close()

if __name__ == "__main__":
    check_database() 