#!/usr/bin/env python3

import os

def check_paths():
    """Check the exact database paths"""
    
    print("Current working directory:", os.getcwd())
    
    # Admin portal path (relative to car-park-web-app)
    admin_path = os.path.abspath('parking_system.db')
    print(f"Admin portal DB path: {admin_path}")
    
    # Client portal path calculation
    # When client-portal/app.py runs, __file__ is the path to app.py
    client_portal_file = os.path.join(os.getcwd(), '..', 'client-portal', 'app.py')
    client_portal_dir = os.path.dirname(client_portal_file)
    parent_dir = os.path.dirname(client_portal_dir)
    client_path = os.path.join(parent_dir, 'car-park-web-app', 'parking_system.db')
    client_path = os.path.abspath(client_path)
    
    print(f"Client portal DB path: {client_path}")
    print(f"Same file: {admin_path == client_path}")
    
    # Check if files exist
    print(f"Admin DB exists: {os.path.exists(admin_path)}")
    print(f"Client DB exists: {os.path.exists(client_path)}")
    
    if os.path.exists(admin_path) and os.path.exists(client_path):
        admin_size = os.path.getsize(admin_path)
        client_size = os.path.getsize(client_path)
        print(f"Admin DB size: {admin_size} bytes")
        print(f"Client DB size: {client_size} bytes")

if __name__ == "__main__":
    check_paths()
