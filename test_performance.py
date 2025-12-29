#!/usr/bin/env python3
"""
Performance test script för att mäta N+1 query-optimiseringen.
Kör detta script för att se prestandaförbättringen.
"""

import time

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.schedule import generate_month_data

# Setup
from app.database.database import Base

# Skapa in-memory databas för test
engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

# Query counter
query_count = 0
query_times = []


@event.listens_for(engine, "before_cursor_execute")
def receive_before_cursor_execute(conn, cursor, statement, params, context, executemany):
    global query_count
    query_count += 1
    conn.info.setdefault("query_start_time", []).append(time.time())
    print(f"Query #{query_count}: {statement[:100]}...")


@event.listens_for(engine, "after_cursor_execute")
def receive_after_cursor_execute(conn, cursor, statement, params, context, executemany):
    total = time.time() - conn.info["query_start_time"].pop()
    query_times.append(total)
    print(f"  -> Took {total * 1000:.2f}ms")


def test_performance():
    """Testa generate_month_data prestanda."""
    global query_count, query_times

    session = Session()

    print("\n" + "=" * 70)
    print("PERFORMANCE TEST: generate_month_data()")
    print("=" * 70)

    # Reset counters
    query_count = 0
    query_times = []

    # Mät tid
    start = time.time()
    data = generate_month_data(2025, 1, person_id=1, session=session)
    elapsed = time.time() - start

    print("\n" + "=" * 70)
    print("RESULTAT:")
    print("=" * 70)
    print(f"Total tid: {elapsed * 1000:.2f}ms")
    print(f"Antal queries: {query_count}")
    if query_times:
        print(f"Genomsnittlig query-tid: {sum(query_times) / len(query_times) * 1000:.2f}ms")
    print(f"Genererade {len(data)} dagar")
    print("=" * 70)

    session.close()


if __name__ == "__main__":
    print("\n🚀 Testar prestanda för månadsgenerering...")
    test_performance()

    print("\n✅ Med batch-fetching bör du se MYCKET färre queries!")
    print("   Före: ~30-60 queries för en månad")
    print("   Efter: ~5-10 queries för en månad")
