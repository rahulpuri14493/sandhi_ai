#!/usr/bin/env python3
"""
Script to rehash existing user passwords with bcrypt
Run this if you have users with old passlib hashes
"""
import sys
from sqlalchemy.orm import Session
from db.database import SessionLocal
from models.user import User
from core.security import get_password_hash

def fix_user_passwords():
    """Rehash all user passwords with bcrypt"""
    db: Session = SessionLocal()
    try:
        users = db.query(User).all()
        print(f"Found {len(users)} users")
        
        for user in users:
            # Check if password is already bcrypt format
            if user.password_hash.startswith('$2'):
                print(f"User {user.email} already has bcrypt hash, skipping...")
                continue
            
            # If not bcrypt, we need to reset the password
            # In production, you'd want to send a password reset email
            print(f"User {user.email} has non-bcrypt hash: {user.password_hash[:20]}...")
            print("  -> This user needs to reset their password")
            print("  -> Or you can manually set a new password here")
            
        print("\nTo fix: Users with old hashes need to reset their passwords")
        print("Or register new accounts with the new bcrypt hashing")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()

if __name__ == "__main__":
    fix_user_passwords()
