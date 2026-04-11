import argparse
import asyncio
import random
import time
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.mcp_guardrails import MCPGuardrailError, get_mcp_guardrails


@dataclass
class CallResult:
    ok: bool
    code: str
    latency_s: float


def _pct(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    k = max(0, min(len(values) - 1, int(round((p / 100.0) * (len(values) - 1)))))
    arr = sorted(values)
    return float(arr[k])


async def _simulated_call(
    timeout: float,
    *,
    base_latency_s: float,
    jitter_s: float,
    failure_rate: float,
    timeout_rate: float,
) -> Dict:
    # Simulated upstream latency and outcomes.
    await asyncio.sleep(max(0.0, base_latency_s + random.uniform(0.0, max(0.0, jitter_s))))
    r = random.random()
    if r < timeout_rate:
        await asyncio.sleep(max(0.0, timeout + 0.05))
        raise asyncio.TimeoutError("simulated timeout")
    if r < timeout_rate + failure_rate:
        raise RuntimeError("simulated upstream failure")
    return {"content": [{"type": "text", "text": "ok"}]}


async def _one_request(
    idx: int,
    sem: asyncio.Semaphore,
    *,
    business_id: int,
    target_key: str,
    base_latency_s: float,
    jitter_s: float,
    failure_rate: float,
    timeout_rate: float,
    timeout_seconds: float,
    operation_class: str,
) -> CallResult:
    guard = get_mcp_guardrails()
    async with sem:
        start = time.perf_counter()
        try:
            await guard.call_tool_with_guardrails(
                business_id=business_id,
                target_key=target_key,
                timeout_seconds=timeout_seconds,
                operation_class=operation_class,
                tool_name=target_key.split(":")[-1],
                tenant_tier="gold",
                idempotency_key=f"load-{idx}" if operation_class == "write_like" else None,
                execute_call=lambda bounded_t: _simulated_call(
                    bounded_t,
                    base_latency_s=base_latency_s,
                    jitter_s=jitter_s,
                    failure_rate=failure_rate,
                    timeout_rate=timeout_rate,
                ),
            )
            return CallResult(ok=True, code="ok", latency_s=time.perf_counter() - start)
        except MCPGuardrailError as e:
            return CallResult(ok=False, code=e.code, latency_s=time.perf_counter() - start)
        except Exception:
            return CallResult(ok=False, code="unexpected_error", latency_s=time.perf_counter() - start)


async def run(args: argparse.Namespace) -> int:
    random.seed(args.seed)
    sem = asyncio.Semaphore(args.concurrency)

    # same target hot-spot set + read/write mix
    target_keys = [f"platform:load:/mcp:tool_{i}" for i in range(max(1, args.distinct_tools))]
    op_write_every = max(1, args.write_every)

    tasks = []
    for i in range(args.total_requests):
        tk = target_keys[i % len(target_keys)]
        op = "write_like" if (i % op_write_every == 0) else "read_like"
        tasks.append(
            asyncio.create_task(
                _one_request(
                    i,
                    sem,
                    business_id=args.business_id,
                    target_key=tk,
                    base_latency_s=args.base_latency_ms / 1000.0,
                    jitter_s=args.jitter_ms / 1000.0,
                    failure_rate=args.failure_rate,
                    timeout_rate=args.timeout_rate,
                    timeout_seconds=args.timeout_seconds,
                    operation_class=op,
                )
            )
        )

    started = time.perf_counter()
    out = await asyncio.gather(*tasks)
    elapsed = time.perf_counter() - started

    latencies = [r.latency_s for r in out]
    ok_count = sum(1 for r in out if r.ok)
    total = len(out)
    err_counts = Counter(r.code for r in out if not r.ok)
    success_rate = ok_count / max(1, total)
    p50 = _pct(latencies, 50)
    p95 = _pct(latencies, 95)
    p99 = _pct(latencies, 99)
    quota_ratio = err_counts.get("mcp_quota_exceeded", 0) / max(1, total)

    print("\n=== MCP Guardrails Load Test ===")
    print(f"requests={total} concurrency={args.concurrency} tools={len(target_keys)} elapsed_s={elapsed:.2f}")
    print(f"success_rate={success_rate:.4f} p50={p50:.3f}s p95={p95:.3f}s p99={p99:.3f}s")
    if err_counts:
        print("errors:", dict(err_counts))
    else:
        print("errors: {}")

    slo_ok = True
    if success_rate < args.slo_min_success_rate:
        print(f"SLO_FAIL: success_rate {success_rate:.4f} < {args.slo_min_success_rate:.4f}")
        slo_ok = False
    if p95 > args.slo_max_p95_seconds:
        print(f"SLO_FAIL: p95 {p95:.3f}s > {args.slo_max_p95_seconds:.3f}s")
        slo_ok = False
    if quota_ratio > args.slo_max_quota_exceeded_ratio:
        print(f"SLO_FAIL: quota_ratio {quota_ratio:.4f} > {args.slo_max_quota_exceeded_ratio:.4f}")
        slo_ok = False

    print("SLO_RESULT:", "PASS" if slo_ok else "FAIL")
    return 0 if slo_ok else 2


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="MCP guardrails load-test harness with SLO checks")
    p.add_argument("--total-requests", type=int, default=3000)
    p.add_argument("--concurrency", type=int, default=500)
    p.add_argument("--distinct-tools", type=int, default=10, help="Hot-spot test with low tool cardinality")
    p.add_argument("--business-id", type=int, default=5001)
    p.add_argument("--write-every", type=int, default=5, help="Every Nth request is write_like")
    p.add_argument("--timeout-seconds", type=float, default=3.0)
    p.add_argument("--base-latency-ms", type=float, default=80.0)
    p.add_argument("--jitter-ms", type=float, default=120.0)
    p.add_argument("--failure-rate", type=float, default=0.01)
    p.add_argument("--timeout-rate", type=float, default=0.005)
    p.add_argument("--seed", type=int, default=42)
    # SLO gates
    p.add_argument("--slo-min-success-rate", type=float, default=0.99)
    p.add_argument("--slo-max-p95-seconds", type=float, default=2.5)
    p.add_argument("--slo-max-quota-exceeded-ratio", type=float, default=0.03)
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
