#!/usr/bin/env python3
"""
IPC Message Bus Throughput Benchmark
Tests send(), poll(), and acknowledge() at various concurrency levels.
"""

import sys
import os
import tempfile
import time
import threading
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.ipc.message_bus import MessageBus
from core.ipc.models import MessageType, MessagePriority


def make_bus():
    """Create a fresh MessageBus with a temp DB (no registry validation)."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "bench_ipc.db")
    return MessageBus(db_path=db_path, profile_registry=None)


def benchmark_send(num_threads, msgs_per_thread):
    """Benchmark send() throughput: N threads each send M messages."""
    bus = make_bus()
    barrier = threading.Barrier(num_threads)
    thread_results = []

    def worker(thread_id):
        barrier.wait()
        t0 = time.perf_counter()
        for i in range(msgs_per_thread):
            bus.send(
                from_profile=f"sender_{thread_id}",
                to_profile=f"receiver_{thread_id % 5}",
                message_type=MessageType.TASK_REQUEST,
                payload={"i": i, "tid": thread_id},
                priority=MessagePriority.NORMAL,
            )
        elapsed = time.perf_counter() - t0
        thread_results.append(msgs_per_thread / elapsed if elapsed > 0 else 0)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return sum(thread_results)


def benchmark_poll(num_threads, msgs_per_thread):
    """Benchmark poll() throughput: pre-seed inbox, then N threads poll concurrently."""
    bus = make_bus()
    profile = "poll_target"

    # Pre-seed enough messages
    total = num_threads * msgs_per_thread
    for i in range(total):
        bus.send(
            from_profile="seeder",
            to_profile=profile,
            message_type=MessageType.TASK_REQUEST,
            payload={"i": i},
        )

    barrier = threading.Barrier(num_threads)
    thread_results = []

    def worker(_tid):
        barrier.wait()
        t0 = time.perf_counter()
        for _ in range(msgs_per_thread):
            bus.poll(profile_name=profile, limit=1)
        elapsed = time.perf_counter() - t0
        thread_results.append(msgs_per_thread / elapsed if elapsed > 0 else 0)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return sum(thread_results)


def benchmark_ack(num_threads, msgs_per_thread):
    """Benchmark acknowledge() throughput: pre-seed messages and collect IDs, then ack concurrently."""
    bus = make_bus()
    profile = "ack_target"

    # Pre-seed messages
    total = num_threads * msgs_per_thread
    msg_ids = []
    for i in range(total):
        mid = bus.send(
            from_profile="seeder",
            to_profile=profile,
            message_type=MessageType.TASK_REQUEST,
            payload={"i": i},
        )
        msg_ids.append(mid)

    # Distribute IDs across threads
    chunks = [msg_ids[i::num_threads] for i in range(num_threads)]

    barrier = threading.Barrier(num_threads)
    thread_results = []

    def worker(thread_id):
        ids = chunks[thread_id]
        barrier.wait()
        t0 = time.perf_counter()
        for mid in ids:
            try:
                bus.acknowledge(message_id=mid)
            except Exception:
                pass
        elapsed = time.perf_counter() - t0
        thread_results.append(len(ids) / elapsed if elapsed > 0 else 0)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return sum(thread_results)


def run_all():
    thread_counts = [1, 5, 10, 20]
    msgs_per_thread = 200

    print("\n=== IPC Message Bus Throughput Benchmark ===\n")
    print(f"  Messages per thread : {msgs_per_thread}")
    print(f"  Concurrency levels  : {thread_counts}\n")

    print(f"{'Threads':>8} | {'Send (msg/s)':>14} | {'Poll (msg/s)':>14} | {'Ack (msg/s)':>13}")
    print("-" * 60)

    for n in thread_counts:
        total_msgs = n * msgs_per_thread

        send_tps = benchmark_send(n, msgs_per_thread)
        poll_tps = benchmark_poll(n, msgs_per_thread)
        ack_tps  = benchmark_ack(n, msgs_per_thread)

        print(f"{n:>8} | {send_tps:>14,.0f} | {poll_tps:>14,.0f} | {ack_tps:>13,.0f}")

    print()


if __name__ == "__main__":
    run_all()
