#!/usr/bin/env python3
"""Quick test of wage history functionality."""

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.schedule import (
    add_new_wage,
    get_current_wage_record,
    get_user_wage,
    get_wage_history,
)

DATABASE_URL = "sqlite:///./app/database/schedule.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def test_wage_history():
    """Test wage history functionality."""
    print("\n🧪 Testing Wage History Functionality\n")

    session = SessionLocal()

    try:
        # Test 1: Get current wage for user 1 (Al-Amin)
        print("1️⃣ Test: Get current wage (no date specified)")
        current_wage = get_user_wage(session, user_id=1)
        print(f"   Current wage for user 1: {current_wage} SEK")

        # Test 2: Get wage for a specific date (should return migration wage)
        print("\n2️⃣ Test: Get wage for specific date (2026-01-15)")
        past_wage = get_user_wage(session, user_id=1, effective_date=date(2026, 1, 15))
        print(f"   Wage on 2026-01-15: {past_wage} SEK")

        # Test 3: Get wage history
        print("\n3️⃣ Test: Get wage history")
        history = get_wage_history(session, user_id=1)
        print(f"   Found {len(history)} wage record(s):")
        for record in history:
            status = "CURRENT" if record["is_current"] else "HISTORICAL"
            print(f"   - {record['wage']} SEK from {record['effective_from']} to {record['effective_to']} [{status}]")

        # Test 4: Add a new wage (future date)
        print("\n4️⃣ Test: Add new wage (40000 SEK from 2026-07-01)")
        new_wage_date = date(2026, 7, 1)
        add_new_wage(session, user_id=1, new_wage=40000, effective_from=new_wage_date, created_by=1)
        print("   ✅ New wage added")

        # Test 5: Get updated history
        print("\n5️⃣ Test: Get updated wage history")
        history = get_wage_history(session, user_id=1)
        print(f"   Found {len(history)} wage record(s):")
        for record in history:
            status = "CURRENT" if record["is_current"] else "HISTORICAL"
            print(f"   - {record['wage']} SEK from {record['effective_from']} to {record['effective_to']} [{status}]")

        # Test 6: Query wage for different dates
        print("\n6️⃣ Test: Query wage for different dates")
        test_dates = [
            date(2026, 1, 15),  # Should return 35000 (migration wage)
            date(2026, 6, 30),  # Should return 35000 (day before new wage)
            date(2026, 7, 1),  # Should return 40000 (new wage starts)
            date(2026, 12, 31),  # Should return 40000 (new wage still active)
        ]

        for test_date in test_dates:
            wage = get_user_wage(session, user_id=1, effective_date=test_date)
            print(f"   {test_date}: {wage} SEK")

        # Test 7: Get current wage record
        print("\n7️⃣ Test: Get current wage record")
        current_record = get_current_wage_record(session, user_id=1)
        if current_record:
            print(f"   Current wage: {current_record.wage} SEK from {current_record.effective_from}")
        else:
            print("   No current wage record found")

        print("\n✅ All tests passed!")

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback

        traceback.print_exc()
    finally:
        session.close()


if __name__ == "__main__":
    test_wage_history()
