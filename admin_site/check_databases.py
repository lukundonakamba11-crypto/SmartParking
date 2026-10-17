#!/usr/bin/env python3

import os
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'client-portal'))

from database import DatabaseManager

def check_database_files():
    """Check which database files exist and are being used"""
    
    print("Checking database files...")
    
    # Check admin portal database
    admin_db_path = os.path.join(os.path.dirname(__file__), 'parking_system.db')
    print(f"Admin portal DB path: {admin_db_path}")
    print(f"Admin portal DB exists: {os.path.exists(admin_db_path)}")
    
    # Check client portal database path
    client_db_path = os.path.join(os.path.dirname(__file__), 'parking_system.db')
    print(f"Client portal DB path: {client_db_path}")
    print(f"Client portal DB exists: {os.path.exists(client_db_path)}")
    
    # Check if they're the same file
    if os.path.exists(admin_db_path) and os.path.exists(client_db_path):
        admin_size = os.path.getsize(admin_db_path) 
        client_size = os.path.getsize(client_db_path)
        print(f"Admin DB size: {admin_size} bytes")
        print(f"Client DB size: {client_size} bytes")
        print(f"Same file: {admin_db_path == client_db_path}")
    
    # Test both databases
    print("\nTesting admin portal database:")
    try:
        admin_db = DatabaseManager(admin_db_path)
        admin_requests = admin_db.get_all_requests()
        admin_clients = admin_db.get_all_clients()
        print(f"  Requests: {len(admin_requests)}")
        print(f"  Clients: {len(admin_clients)}")
    except Exception as e:
        print(f"  Error: {e}")
    
    print("\nTesting client portal database:")
    try:
        client_db = DatabaseManager(client_db_path)
        client_requests = client_db.get_all_requests()
        client_clients = client_db.get_all_clients()
        print(f"  Requests: {len(client_requests)}")
        print(f"  Clients: {len(client_clients)}")
    except Exception as e:
        print(f"  Error: {e}")

if __name__ == "__main__":
    check_database_files()
